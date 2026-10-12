#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成长机会选择器 —— 从 `hfut_site/hfut_opportunity_sources.json` 的
`seed_opportunities` 里挑出当次要进邮件的那几条（2026-09-23 新增）。

## 为什么需要它

`hfut_opportunity_sources.json` 承担两个职责：
- `sources`：59 个信息源的**清单**（"去哪找"，含实测可抓取性）——给每日巡检按图索骥；
- `seed_opportunities`：**已人工核实过、当前确实开放**的机会条目。

邮件每天只需要 3~8 条，不能把 10 条全塞进去（会淹没正文），也不该让模型每天现挑
（挑错一条就是一条假信息）。所以把"筛选+排序+字段映射"固化成脚本。

## 排序与筛选规则

1. 只取 `verified: true`（脚本不替人做核实，没标的直接跳过并提示）；
2. **有截止日期的排在前面并按日期升序**（越紧迫越靠前）；
   无截止日期（常年开放/进行中）的排在后面；
3. 已过期的**默认丢弃**（除非 `--include-expired`）；
4. 最多输出 `--max` 条（默认 6）。

## 用法

```bash
cd "D:/项目整理/01-代码项目/hfut_info_monitor"
.venv/Scripts/python.exe scripts/opportunity_pick.py --report
.venv/Scripts/python.exe scripts/opportunity_pick.py --emit-into temp_files/daily_intel_2026-09-23.json
.venv/Scripts/python.exe scripts/opportunity_pick.py --emit        # 输出 JSON 数组
```

## ⚠️ 已知局限（留给后续增强，不要假装它不存在）

`seed_opportunities` 目前**没有"适用届别"字段**。而清单里多条是**「2027 届校园招聘」**
——那是面向 2027 年毕业的学生；用户是 **2024 级本科、2028 年毕业**，
这类校招对他是**提前一年**的（可作了解，但不是当下要投的）。
所以 `--report` 会把这类条目单独标出来，避免误读为"现在就该投"。
后续应在数据文件里补 `fit_grades`（如 `["2027届"]`）字段，本脚本再据此过滤。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_FILE = os.path.join(ROOT, "hfut_site", "hfut_opportunity_sources.json")
COMP_FILE = os.path.join(ROOT, "hfut_site", "hfut_competitions.json")

DEFAULT_MAX = 6


def load_competition_urls():
    """取出赛历里所有官方 URL，用于**跨栏目去重**。

    2026-09-23 实测踩到：`comp-catalog` 增量更新后，把「第十一届华为 ICT 大赛」
    同时放进了 `hfut_competitions.json`（赛事日历）和 `seed_opportunities`（成长机会），
    邮件里会在两个分区各出现一次 —— 读者会以为是两条不同信息。
    赛事名两边写法不同（"第十一届华为 ICT 大赛（2026-2027 中国赛）" vs 赛事表的全名），
    **按名字匹配会漏**，所以用 URL 当去重键。
    """
    if not os.path.exists(COMP_FILE):
        return set()
    try:
        with open(COMP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    return {_norm_url(c.get("official_url") or c.get("url"))
            for c in (data.get("competitions") or [])}


def _norm_url(u: str) -> str:
    """URL 归一化：去空白、去末尾斜杠、去 fragment、小写主机部分。
    只做保守处理，够用来判"是不是同一个链接"即可。"""
    s = str(u or "").strip().rstrip("/")
    s = s.split("#", 1)[0]
    return s.lower()

# 命中这些词的多为"面向特定毕业届别"的校招，与用户（2024 级 / 2028 届）错位一年。
# ⚠️ 源数据写法不统一：既有「2027届校园招聘」，也有「2027 校园招聘」（不带"届"），
#    只匹配 `20\d{2}\s*届` 会漏掉后者（2026-09-23 实测：「腾讯 2027 校园招聘」漏标）。
#    所以两种都要认。反过来，像「第十一届华为 ICT 大赛（2026-2027 中国赛）」
#    里的 2026-2027 是**赛季年份**，不是届别 —— 故用 `20\d{2}` 后紧跟"届/校园招聘/校招"来限定，
#    避免把赛季误判成届别。
_GRADE_HINT = re.compile(r"20\d{2}\s*(?:届|校园招聘|校招)")


def _parse(d):
    try:
        return datetime.strptime(str(d).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        return None


def _short(s, n=110):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def load_seeds():
    if not os.path.exists(SRC_FILE):
        raise SystemExit(f"[opp] 信息源文件不存在: {SRC_FILE}")
    with open(SRC_FILE, encoding="utf-8") as f:
        return json.load(f).get("seed_opportunities") or []


def build(today: date | None = None, max_n: int = DEFAULT_MAX,
          include_expired: bool = False, max_mismatch: int = 2):
    """返回 (opportunities 数组, 统计 dict)。

    排序键的**顺序很重要**（2026-09-23 实测踩坑）：
    第一版只按「有无截止日期 + 日期升序」排，结果 10 条 seed 里
    **5 条"2027 届校招"**（面向 2023 级）把栏目占满，真正适用于用户
    （2024 级 / 2028 届）的「阿里云工开物算力券」「天池大赛」「国家奖学金」
    反而因为都"无截止日期"、在组内按文件原序排在后面，被 `[:max]` 截掉了。
    所以必须：① 先按"是否届别错位"分组（适用的在前）；
              ② 无截止日期的组内**保留文件原序**（seed 是人手工排过的，比按名字排更可信）；
              ③ 届别错位的**单独限量**，避免整栏被占满。
    """
    today = today or date.today()
    seeds = load_seeds()
    comp_urls = load_competition_urls()
    rows, skipped_unverified, expired, mismatch_rows = [], 0, 0, []
    skipped_dup = []

    for idx, s in enumerate(seeds):
        if not isinstance(s, dict):
            continue
        if not s.get("verified"):
            skipped_unverified += 1
            continue
        if not (s.get("url") or "").strip():
            skipped_unverified += 1
            continue
        # 跨栏目去重：已经在"赛事日历"里出现过的，不再在"成长机会"里重复一次
        if _norm_url(s.get("url")) in comp_urls:
            skipped_dup.append(s.get("name"))
            continue
        dl = _parse(s.get("deadline"))
        if dl is not None and dl < today:
            expired += 1
            if not include_expired:
                continue
        item = {
            "name": s.get("name"),
            "url": s.get("url"),
            "type": s.get("type") or "机会",
        }
        if s.get("org"):
            item["org"] = s["org"]
        if s.get("region"):
            item["region"] = s["region"]
        if s.get("deadline"):
            item["deadline"] = s["deadline"]
        why = _short(s.get("why"))
        if why:
            item["desc"] = why
        is_mismatch = bool(_GRADE_HINT.search(f"{s.get('name','')}{s.get('why','')}"))
        if is_mismatch:
            item["desc"] = ("（面向特定毕业届别，请核对是否适用于你）" + (item.get("desc") or ""))[:140]
            item["_mismatch"] = True
        item["_idx"] = idx
        rows.append(item)

    fits = [r for r in rows if not r.get("_mismatch")]
    mismatch = [r for r in rows if r.get("_mismatch")]

    def _key(r):
        dl = _parse(r.get("deadline"))
        return (dl is None, dl or date.max, r["_idx"])

    fits.sort(key=_key)
    mismatch.sort(key=_key)

    picked = fits[:max_n]
    if len(picked) < max_n and max_mismatch > 0:
        picked += mismatch[: min(max_mismatch, max_n - len(picked))]

    picked_mismatch = [r["name"] for r in picked if r.get("_mismatch")]
    for r in picked:
        r.pop("_mismatch", None)
        r.pop("_idx", None)

    return picked, {
        "today": today.strftime("%Y-%m-%d"),
        "total_seeds": len(seeds),
        "emitted": len(picked),
        "fit_count": len(picked) - len(picked_mismatch),
        "skipped_unverified": skipped_unverified,
        "skipped_dup_with_calendar": skipped_dup,
        "expired": expired,
        "grade_mismatch": picked_mismatch,
        "grade_mismatch_available": len(mismatch),
    }


def print_report(rows, stats):
    print("=" * 74)
    print(f"成长机会 · 基准日 {stats['today']}")
    print(f"seed 共 {stats['total_seeds']} 条 → 输出 {stats['emitted']} 条"
          f"（未核实 {stats['skipped_unverified']} / 已过期 {stats['expired']}）")
    if stats["grade_mismatch"]:
        print(f"⚠ 其中 {len(stats['grade_mismatch'])} 条面向特定毕业届别，已加提示：")
        for n in stats["grade_mismatch"]:
            print(f"     - {n}")
    print("=" * 74)
    base = datetime.strptime(stats["today"], "%Y-%m-%d").date()
    for r in rows:
        dl = _parse(r.get("deadline"))
        when = "常年/进行中" if dl is None else f"{r['deadline']}（{max((dl-base).days,0)} 天后）"
        print(f"  · [{r.get('type')}]{r.get('region','') and ' ' + r['region']} {r['name']}")
        print(f"      {r.get('org','')} | {when}")
        print(f"      {r.get('url')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="成长机会选择器（邮件 opportunities 数组生成）")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD，默认今天")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"最多输出几条，默认 {DEFAULT_MAX}")
    ap.add_argument("--include-expired", action="store_true", help="连已过期的一起输出")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--report", action="store_true", help="打印人读表格")
    g.add_argument("--emit", action="store_true", help="输出 JSON 数组到 stdout")
    ap.add_argument("--emit-into", default=None, metavar="DATA_JSON",
                    help="把 opportunities 数组写进指定的邮件数据 JSON（原地补键）")
    args = ap.parse_args()

    today = _parse(args.today) or date.today()
    rows, stats = build(today=today, max_n=args.max,
                        include_expired=args.include_expired)

    if args.emit_into:
        if not os.path.exists(args.emit_into):
            print(f"[opp] 目标数据文件不存在: {args.emit_into}", file=sys.stderr)
            return 2
        with open(args.emit_into, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            print("[opp] 目标数据文件顶层必须是对象", file=sys.stderr)
            return 2
        payload["opportunities"] = rows
        with open(args.emit_into, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[opp] 已写入 {args.emit_into}：opportunities = {len(rows)} 条")
        if stats["grade_mismatch"]:
            print(f"[opp] ⚠ {len(stats['grade_mismatch'])} 条面向特定毕业届别，已在 desc 中标注")
        return 0

    if args.emit:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print_report(rows, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
