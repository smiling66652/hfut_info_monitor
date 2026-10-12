#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语义检索接口 demo（2026 创新大赛路演用）

技术一句话：BM25（手写，零第三方依赖）+ 可选本地 Ollama qwen2:7b 查询改写，
Ollama 不可用时自动降级为纯 BM25，全程零 API 成本。

用法：
    python scripts/semantic_search.py "保研" -n 5
    python scripts/semantic_search.py "奖学金" -n 5 --json
    python scripts/semantic_search.py "竞赛报名" -n 10
"""
import argparse
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

# ---------- 路径（脚本可从任意 cwd 运行；支持 D:/hfut_info_monitor 联接目录） ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PATH = PROJECT_ROOT / "data" / "hfut_archive.json"

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2:7b"
OLLAMA_TIMEOUT = 5  # 秒；路演场景宁快勿慢

# ---------- 中文 bigram + 英文/数字分词（不依赖 jieba） ----------
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+")


def tokenize(text: str):
    """中文按 bigram 切分，英文/数字按连续词切分。"""
    tokens = []
    for seg in _TOKEN_RE.findall(text.lower()):
        if seg[0] >= "\u4e00" and seg[0] <= "\u9fff":
            if len(seg) == 1:
                tokens.append(seg)
            else:
                tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.append(seg)
    return tokens


# ---------- 数据加载 ----------
def load_docs(path: Path = ARCHIVE_PATH):
    """读取 JSONL 归档，每行一个 JSON 对象。"""
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            docs.append(o)
    return docs


# ---------- 轻量内置同义词表（零依赖，保口语查询可命中） ----------
SYNONYMS = {
    "保研": ["推免", "研究生", "免试"],
    "推免": ["保研", "研究生"],
    "考研": ["研究生", "招生"],
    "奖助": ["奖学金", "助学金"],
    "竞赛": ["大赛", "比赛"],
    "运动会": ["校运会", "体育"],
}


def expand_query(query):
    """口语词 → 同义词扩展，如 '保研' → '保研 推免 研究生 免试'。"""
    extra = []
    base = query.lower()
    for word, syns in SYNONYMS.items():
        if word in base:
            extra.extend(s for s in syns if s not in base)
    return query + " " + " ".join(extra) if extra else query


# ---------- 手写 BM25 ----------
class BM25Index:
    """Okapi BM25：k1=1.5, b=0.75，对 标题+内容 建索引（标题命中额外加权）。"""

    def __init__(self, docs, k1=1.5, b=0.75, title_weight=2.0):
        self.k1, self.b, self.tw = k1, b, title_weight
        self.docs = docs
        self.doc_tokens = []
        self.doc_len = []
        self.tf = []          # list[dict[token -> count]]
        self.df = {}          # token -> 文档频率
        self.avgdl = 0.0
        for d in docs:
            t_title = tokenize(d.get("title", ""))
            t_body = tokenize(str(d.get("content") or d.get("title", "")))
            toks = t_title + t_body
            self.doc_tokens.append(toks)
            self.doc_len.append(len(toks) or 1)
            counts = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in set(t_title) | set(t_body):
                self.df[t] = self.df.get(t, 0) + 1
        self.N = len(docs)
        self.avgdl = sum(self.doc_len) / max(self.N, 1)

    def idf(self, token):
        df = self.df.get(token, 0)
        if df == 0:
            return 0.0
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, query, top_n=5):
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = [0.0] * self.N
        for i in range(self.N):
            dl = self.doc_len[i]
            tfs = self.tf[i]
            s = 0.0
            for t in q_tokens:
                f = tfs.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                s += self.idf(t) * f * (self.k1 + 1) / denom
            # 标题命中额外加权：标题 token 数重算一遍，加权部分叠加
            title_set = set(tokenize(self.docs[i].get("title", "")))
            hit_in_title = len(title_set & set(q_tokens))
            s *= 1.0 + self.tw * hit_in_title / max(len(set(q_tokens)), 1)
            scores[i] = s
        ranked = sorted(range(self.N), key=lambda i: scores[i], reverse=True)
        results = [(i, scores[i]) for i in ranked[:top_n] if scores[i] > 0]
        return results


# ---------- 可选：Ollama 查询改写（不可用则静默降级） ----------
def rewrite_query(query):
    """让本地 qwen2:7b 扩展同义词/相关词；失败返回原查询。"""
    prompt = (
        "你是检索查询改写器。把用户查询扩展成一行以空格分隔的同义词和相关词，"
        "只输出这一行词语，不要解释，不要标点。\n用户查询：" + query
    )
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 40, "temperature": 0.2},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rewritten = (data.get("response") or "").strip().splitlines()
        rewritten = rewritten[0].strip() if rewritten else ""
        return (query + " " + rewritten).strip() if rewritten else query
    except Exception:
        return query


# ---------- 对外主入口 ----------
def do_search(query, top_n=5, use_llm=True):
    """返回 dict：hits 列表 + 元信息。供 CLI 与 Web demo 共用。"""
    docs = load_docs()
    if not docs:
        return {"query": query, "hits": [], "total": 0, "engine": "none",
                "rewritten": None}
    engine = "bm25"
    q_for_rank = expand_query(query)
    if q_for_rank != query:
        engine = "bm25+synonym"
    if use_llm:
        rewritten = rewrite_query(q_for_rank)
        if rewritten != q_for_rank:
            engine = "bm25+synonym+ollama" if engine != "bm25" else "bm25+ollama"
            q_for_rank = rewritten
    index = BM25Index(docs)
    hits = []
    for i, score in index.search(q_for_rank, top_n):
        d = docs[i]
        hits.append({
            "title": d.get("title", ""),
            "date": d.get("time", ""),
            "source": d.get("source", ""),
            "url": d.get("url", ""),
            "score": round(score, 3),
        })
    return {"query": query, "rewritten": q_for_rank if engine != "bm25" else None,
            "engine": engine, "total": len(docs), "hits": hits}


def _fmt_result(r):
    lines = []
    lines.append(f"查询：{r['query']}  [引擎: {r['engine']}]  库内共 {r['total']} 条")
    if r.get("rewritten"):
        lines.append(f"（查询扩展：{r['rewritten']}）")
    lines.append("-" * 72)
    for n, h in enumerate(r["hits"], 1):
        lines.append(f"{n}. [{h['score']}] {h['title']}")
        lines.append(f"   {h['date']}  {h['source']}")
        lines.append(f"   {h['url']}")
    if not r["hits"]:
        lines.append("（无命中）")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="hfut_info_monitor 语义检索 demo")
    ap.add_argument("query", help="查询词，如：保研 / 竞赛报名 / 奖学金")
    ap.add_argument("-n", type=int, default=5, help="返回条数（默认 5）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--no-llm", action="store_true", help="禁用 Ollama 改写，纯 BM25")
    args = ap.parse_args()

    r = do_search(args.query, args.n, use_llm=not args.no_llm)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(_fmt_result(r))


if __name__ == "__main__":
    main()
