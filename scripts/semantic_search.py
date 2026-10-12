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
import hashlib
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path

# ---------- 路径（脚本可从任意 cwd 运行；支持 D:/hfut_info_monitor 联接目录） ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_PATH = PROJECT_ROOT / "data" / "hfut_archive.json"

# Ollama 地址支持环境变量覆盖：便于路演现场一键演示降级效果
# 例：HFUT_OLLAMA_PORT=19999 .venv/Scripts/python.exe scripts/search_demo_server.py
OLLAMA_PORT = os.environ.get("HFUT_OLLAMA_PORT", "11434")
OLLAMA_BASE = f"http://localhost:{OLLAMA_PORT}"
OLLAMA_URL = f"{OLLAMA_BASE}/api/generate"
OLLAMA_MODEL = os.environ.get("HFUT_LLM_MODEL", "qwen2:7b")
OLLAMA_TIMEOUT = 5  # 秒；路演场景宁快勿慢

OLLAMA_EMBED_URL = f"{OLLAMA_BASE}/api/embed"
EMBED_MODEL = os.environ.get("HFUT_EMBED_MODEL", "qwen3-embedding:0.6b")
EMBED_QUERY_TIMEOUT = 5    # 秒；查询向量，超时即降级
EMBED_BUILD_TIMEOUT = 300  # 秒；首次建全库索引较慢（257 条约 80s），允许等待
VECTOR_CACHE = PROJECT_ROOT / "data" / ".semantic_vector_cache.json"
SUMMARY_CHARS = 200        # 嵌入文本 = 标题 + 摘要前 N 字
RRF_K = 60                 # RRF 平滑常数
W_BM25, W_VEC = 0.5, 0.5   # 混合权重：关键词层与语义层均衡

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
    "保研": ["推免", "免试", "研究生", "联合培养"],
    "推免": ["保研", "免试", "研究生"],
    "考研": ["研究生", "招生"],
    "出国": ["国际交流", "联合培养", "交换生"],
    "交流": ["交流", "访问", "来访"],
    "补助": ["奖助学金", "助学金"],
    "奖助": ["奖学金", "助学金"],
    "资助": ["奖助学金", "助学金"],
    "竞赛": ["大赛", "比赛"],
    "讲座": ["讲座", "大讲堂", "报告"],
    "学分": ["实践", "学分", "第二课堂"],
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
# 熔断器：某个 Ollama 能力首次失败后，本次进程内不再重试，避免每次查询白等超时
_LLM_AVAILABLE = None


def rewrite_query(query):
    """让本地 qwen2:7b 扩展同义词/相关词；失败返回原查询。"""
    global _LLM_AVAILABLE
    if _LLM_AVAILABLE is False:
        return query
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
        if not rewritten:
            _LLM_AVAILABLE = False
            return query
        _LLM_AVAILABLE = True
        return (query + " " + rewritten).strip()
    except Exception:
        _LLM_AVAILABLE = False
        return query


# ---------- 向量语义层：Ollama /api/embed + 本地缓存 ----------
_EMBED_AVAILABLE = None


def _embed(texts, timeout):
    """调用本地嵌入模型，返回 list[list[float]]；任何异常返回 None（触发降级）。
    keep_alive 让模型常驻内存，避免路演首次调用的冷启动等待。
    熔断：首次失败后本次进程内直接返回 None，不让每次查询白等超时。"""
    global _EMBED_AVAILABLE
    if _EMBED_AVAILABLE is False:
        return None
    body = json.dumps({"model": EMBED_MODEL, "input": texts,
                       "keep_alive": "30m"}).encode("utf-8")
    try:
        req = urllib.request.Request(
            OLLAMA_EMBED_URL, data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        embs = data.get("embeddings")
        if not embs or len(embs) != len(texts):
            _EMBED_AVAILABLE = False
            return None
        _EMBED_AVAILABLE = True
        return embs
    except Exception:
        _EMBED_AVAILABLE = False
        return None


def _doc_text(d):
    title = d.get("title", "")
    body = str(d.get("content") or "")[:SUMMARY_CHARS]
    return (title + " " + body).strip() if body else title


_WEAK_TITLE_RE = re.compile(r"^[\d年月日\s，,。、：:]{1,12}$")


def display_title(d):
    """展示用标题：公众号归档常把正文首句当标题（如『2023年』），此类回退取正文摘要。"""
    t = (d.get("title") or "").strip()
    content = str(d.get("content") or "").strip().replace("\n", " ")
    if (len(t) < 10 or _WEAK_TITLE_RE.match(t)) and len(content) > len(t):
        return content[:48] + ("…" if len(content) > 48 else "")
    return t


def load_doc_vectors(docs, rebuild=False):
    """取全库向量：优先磁盘缓存（按内容指纹失效），否则调 Ollama 构建并缓存。
    缓存即离线资产：命中即返回（不重复探测），Ollama 不可用时后续查询嵌入
    会失败并自动熔断退回 BM25。不可用返回 None。"""
    fingerprint = hashlib.md5(
        (EMBED_MODEL + str(len(docs)) +
         "|".join(_doc_text(d) for d in docs)).encode("utf-8")
    ).hexdigest()
    if not rebuild:
        try:
            with open(VECTOR_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("fingerprint") == fingerprint:
                return cache["vectors"]
        except Exception:
            pass
    vecs = _embed([_doc_text(d) for d in docs], EMBED_BUILD_TIMEOUT)
    if vecs is None:
        return None
    try:
        with open(VECTOR_CACHE, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint, "model": EMBED_MODEL,
                       "vectors": vecs}, f)
    except Exception:
        pass  # 缓存写失败不影响检索
    return vecs


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


def rrf_fuse(rankings, weights):
    """Reciprocal Rank Fusion：score(d) = Σ w_r / (K + rank_r(d))
    rankings: [(rank, doc_idx)...] 按相关度降序；weights 对应权重。"""
    fused = {}
    for (ranked, w) in zip(rankings, weights):
        for rank, idx in enumerate(ranked, start=1):
            fused[idx] = fused.get(idx, 0.0) + w / (RRF_K + rank)
    return fused


# ---------- 对外主入口 ----------
def do_search(query, top_n=5, use_llm=True, use_vector=True, use_synonym=True):
    """返回 dict：hits 列表 + 元信息。供 CLI 与 Web demo 共用。
    向量层不可用时自动退回 BM25，行为与纯关键词版一致。
    use_synonym=False 用于 A/B 对照（复现升级前的原始关键词版本）。"""
    docs = load_docs()
    if not docs:
        return {"query": query, "hits": [], "total": 0, "engine": "none",
                "rewritten": None}
    engine = "bm25"
    q_for_rank = expand_query(query) if use_synonym else query
    if q_for_rank != query:
        engine = "bm25+synonym"
    if use_llm:
        rewritten = rewrite_query(q_for_rank)
        if rewritten != q_for_rank:
            engine += "+ollama"
            q_for_rank = rewritten

    index = BM25Index(docs)
    bm25_hits = index.search(q_for_rank, max(top_n * 5, 20))
    bm25_scores = dict(bm25_hits)
    bm25_rank = [i for i, _ in bm25_hits]

    vec_scores, vec_rank = None, []
    if use_vector:
        doc_vecs = load_doc_vectors(docs)
        if doc_vecs:
            q_vec = _embed([q_for_rank], EMBED_QUERY_TIMEOUT)
            if q_vec:
                vec_scores = {i: cosine(q_vec[0], v) for i, v in enumerate(doc_vecs)}
                vec_rank = sorted(vec_scores, key=lambda i: vec_scores[i],
                                  reverse=True)[:max(top_n * 5, 20)]
                engine += "+vector"

    if vec_rank:
        fused = rrf_fuse([bm25_rank, vec_rank], [W_BM25, W_VEC])
        order = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_n]
        score_of = lambda i: round(fused[i], 5)
    else:
        order = [i for i, _ in bm25_hits][:top_n]
        score_of = lambda i: round(bm25_scores.get(i, 0.0), 3)

    hits = []
    for i in order:
        d = docs[i]
        h = {
            "title": display_title(d),
            "date": d.get("time", ""),
            "source": d.get("source", ""),
            "url": d.get("url", ""),
            "score": score_of(i),
        }
        if vec_scores is not None:
            h["bm25"] = round(bm25_scores.get(i, 0.0), 3)
            h["cosine"] = round(vec_scores.get(i, 0.0), 4)
        hits.append(h)
    return {"query": query, "rewritten": q_for_rank if engine != "bm25" else None,
            "engine": engine, "total": len(docs), "hits": hits}


def _fmt_result(r):
    lines = []
    lines.append(f"查询：{r['query']}  [引擎: {r['engine']}]  库内共 {r['total']} 条")
    if r.get("rewritten"):
        lines.append(f"（查询扩展：{r['rewritten']}）")
    lines.append("-" * 72)
    vec_on = "cosine" in (r["hits"][0] if r["hits"] else {})
    for n, h in enumerate(r["hits"], 1):
        extra = ""
        if vec_on:
            extra = f"  [bm25={h.get('bm25')}  cos={h.get('cosine')}]"
        lines.append(f"{n}. [{h['score']}]{extra} {h['title']}")
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
    ap.add_argument("--no-llm", action="store_true", help="禁用 LLM 改写")
    ap.add_argument("--no-vector", action="store_true",
                    help="禁用向量语义层，纯 BM25（用于效果对照）")
    args = ap.parse_args()

    r = do_search(args.query, args.n, use_llm=not args.no_llm,
                  use_vector=not args.no_vector)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(_fmt_result(r))


if __name__ == "__main__":
    main()
