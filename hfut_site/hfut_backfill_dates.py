#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引修复（日期回填 + 标题刷新）（2026-09-22 新增，当日升级为通用修复）。

背景：`hfut_full_index.json` 按 URL 去重入库，一旦某条以「无日期」或「脏标题」
状态写入，后续就算解析器修好了也不会重新解析 —— 而
  - `date` 为空  → 该条**永远不会出现在「近 7 天 / 今日新增」里**
  - 标题粘了正文摘要（教务部实测：`标题 + 摘要全文`）→ 邮件里不可读

实测（2026-09-22）：
  - 宣城校区 23/25 条无日期（该站用 news_year/news_days 反序模板）
  - 机械学院 49 条无日期
  - 教务部-教学要闻 标题粘了整段正文

做法：用**修好后的提取器**重新抓一遍各栏目列表页，建立
`归一化URL -> {date,title}` 映射，再回填索引。规则：
  - date 只补空，不覆盖已有值
  - title 只在「旧标题 = 新标题 + 后缀」时替换（即旧标题是新标题的超集），
    避免把人工/更优的标题冲掉

用法：
  .venv/Scripts/python.exe hfut_site/hfut_backfill_dates.py            # 执行修复
  .venv/Scripts/python.exe hfut_site/hfut_backfill_dates.py --dry-run  # 只看会改多少
  .venv/Scripts/python.exe hfut_site/hfut_backfill_dates.py --titles   # 只刷标题
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hfut_scanner as S  # noqa: E402

INDEX_FILE = S.INDEX_FILE


def fetch_channel(channel, max_pages=5):
    """抓单个栏目（多页），返回 {归一化URL: {date,title}}。

    只抓首页只能补到最新 ~20 条，而索引里积压的是更早的历史条目，
    所以这里借 scan_channel 的分页能力向后翻 max_pages 页。
    """
    if len(channel) == 5:
        name, list_url, domain, ctype, cms = channel
    else:
        name, list_url, domain, ctype, cms, _ = channel
    try:
        items = S.scan_channel(name, list_url, domain, ctype, cms,
                               max_pages, None, use_browser_fallback=False)
        mapping = {}
        for it in items:
            k = S.normalize_url(it.get("url", ""))
            if not k or k in mapping:
                continue
            mapping[k] = {"date": (it.get("date") or "").strip(),
                          "title": (it.get("title") or "").strip()}
        return name, mapping
    except Exception as e:
        return name, {"__error__": str(e)[:80]}


def main() -> int:
    dry = "--dry-run" in sys.argv
    titles_only = "--titles" in sys.argv
    if not os.path.exists(INDEX_FILE):
        print("索引不存在:", INDEX_FILE)
        return 1

    index = json.load(open(INDEX_FILE, encoding="utf-8"))

    def dirty(it):
        """需要修复：无日期，或标题疑似粘了正文摘要。"""
        if not (it.get("date") or "").strip():
            return True
        t = it.get("title") or ""
        # 标题里出现句末标点或很长的逗号串 → 基本是把摘要粘进来了
        return len(t) > 60 and ("。" in t or t.count("，") >= 2)

    need_fix = [i for i in index if (dirty(i) if not titles_only else dirty(i))]
    print(f"索引 {len(index)} 条，需修复 {len(need_fix)} 条")

    need = {i.get("channel") for i in need_fix}
    channels = [c for c in S.CHANNELS if c[0] in need]
    print(f"需要重抓的栏目: {len(channels)} 个 -> {', '.join(c[0] for c in channels)}\n")

    url2rec = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_channel, c): c[0] for c in channels}
        for f in as_completed(futs):
            name, m = f.result()
            if "__error__" in m:
                print(f"  ⚠ {name}: {m['__error__']}")
                continue
            url2rec.update(m)
            print(f"  ✓ {name}: 解析到 {len(m)} 条")
    print(f"\n重抓完成，耗时 {time.time()-t0:.1f}s；映射 {len(url2rec)} 条")

    filled_d = filled_t = 0
    for it in index:
        rec = url2rec.get(S.normalize_url(it.get("url", "")))
        if not rec:
            continue
        if not (it.get("date") or "").strip() and rec["date"]:
            it["date"] = rec["date"]
            filled_d += 1
        old, new = (it.get("title") or "").strip(), rec["title"]
        # 旧标题是新标题的"超集"（多粘了摘要）时才替换
        if new and len(new) >= 6 and old != new and old.startswith(new):
            it["title"] = new
            filled_t += 1

    # 2026-09-23 新增：折叠「标题自身整段重复」。
    # 这类脏数据来自主站老页面 —— 锚点里标题写了两遍，旧解析器取整段 inner 得到
    # 「关于2019年度相关证书材料归档工作的通知 关于2019年度相关证书材料归档工作的通知」。
    # 上面的"超集替换"依赖重抓映射命中（每栏目只翻 5 页），翻不到的历史条目就漏掉了，
    # 所以这里再对**全量索引**跑一次纯文本折叠。
    # 只在"恰好等分为两份且完全相同"时折叠，不做模糊匹配 —— 因此可以安全重复执行。
    deduped = 0
    for it in index:
        old_t = (it.get("title") or "").strip()
        new_t = S._dedupe_repeated_title(old_t)
        if new_t and new_t != old_t:
            it["title"] = new_t
            deduped += 1
    print(f"标题自身重复折叠 {deduped} 条")

    print(f"可回填日期 {filled_d} 条 / 可刷新标题 {filled_t} 条")
    if dry:
        print("DRY-RUN，未写盘")
        return 0

    index.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    json.dump(index, open(INDEX_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"已写回 {INDEX_FILE}")

    left_d = [i for i in index if not (i.get("date") or "").strip()]
    left_t = [i for i in index if dirty(i) and (i.get("date") or "").strip()]
    print(f"剩余无日期 {len(left_d)} 条 / 剩余可疑标题 {len(left_t)} 条")
    import collections
    if left_d:
        for k, v in collections.Counter(i.get("channel") for i in left_d).most_common(6):
            print(f"   [无日期] {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
