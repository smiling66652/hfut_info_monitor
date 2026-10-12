#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赛事日历计算器 —— 把 `hfut_site/hfut_competitions.json` 变成邮件能直接用的
`competitions` 数组（2026-09-23 新增）。

## 为什么需要它

用户需求原话：

> 找找 A\\B 类赛事明细，**一天又一天的计时，当 A、B 类赛事还有 3 个月的时候开始提醒**，
> 同时可以附带准备一些备考资料的直达链接，可以有一个完整的准备路线

`hfut_competitions.json` 里存的是**赛事的完整赛程**（报名/初赛/决赛各有起止日期），
而邮件需要的是**"接下来该盯哪一个日期、还剩多少天"**。这个转换不该让模型每天现算
——算错一天就是一次误报或漏报。所以固化成脚本。

## 它做三件事

1. **挑出"下一个关键日期"**：在 报名开始/报名截止/初赛起止/决赛起止 里，
   取**最早的那个 >= 今天**的日期，并带上中文标签（如"报名截止"）。
   若全部已过，则取最后一个过去日期并标记为已结束（默认不输出）。
2. **按 90 天窗口过滤**：只有剩余天数 <= `REMIND_LEAD_DAYS`（默认 90）的才进邮件，
   这正是"还有 3 个月时开始提醒"的落地。按剩余天数升序（越紧越靠前）。
3. **合并备考资料链接**：从 `hfut_site/hfut_competition_prep.json` 按赛事名匹配，
   把 `prep.official_outline` / `past_papers` / `tutorials` 等拍平成 `prep_links`。

## 用法

```bash
cd "D:/项目整理/01-代码项目/hfut_info_monitor"

# 人看：打印当前在提醒窗口内的赛事（含倒计时）
.venv/Scripts/python.exe scripts/competition_calendar.py --report

# 放宽窗口看全部
.venv/Scripts/python.exe scripts/competition_calendar.py --report --lead 3650

# 机用：直接把这个数组写进当天的邮件数据 JSON（推荐，一条命令完成集成）
.venv/Scripts/python.exe scripts/competition_calendar.py \
    --emit-into temp_files/daily_intel_2026-09-23.json

# 机用：只输出 JSON 片段到 stdout
.venv/Scripts/python.exe scripts/competition_calendar.py --emit
```

## 设计取舍

- **不在渲染层算倒计时**：模板只负责把传进来的 `deadline` 显示成"还有 N 天"；
  "哪个日期该盯"属于业务判断，放在这里，便于单独测试与复用。
- **窗口可调**：`--lead` 默认 90 天（3 个月）；`--lead 0` 表示不过滤。
- **宽进严出**：数据缺日期时**跳过该赛事并在 stderr 提示**，绝不编一个日期出来。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COMP_FILE = os.path.join(ROOT, "hfut_site", "hfut_competitions.json")
PREP_FILE = os.path.join(ROOT, "hfut_site", "hfut_competition_prep.json")

REMIND_LEAD_DAYS = 90          # 用户要的"还有 3 个月时开始提醒"

# 赛程里哪些字段可以当作"关键日期"，以及它们的中文标签。
# 顺序即优先级：同一天命中多个时取更靠前的标签（报名截止 比 初赛 更该被盯）。
_DATE_FIELDS = [
    ("registration", "end", "报名截止"),
    ("registration", "start", "报名开始"),
    ("preliminary", "end", "初赛结束"),
    ("preliminary", "start", "初赛"),
    ("final", "end", "决赛结束"),
    ("final", "start", "决赛"),
]


def _parse(d):
    """解析 YYYY-MM-DD；失败返回 None。"""
    try:
        return datetime.strptime(str(d).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        return None


def pick_next_key_date(schedule: dict, today: date):
    """在赛程里挑"下一个该盯的日期"。

    ⚠️ 2026-09-23 实测踩坑后重写（**修的是一个已经发出去的误报**）。

    **病因不是"start vs end"，而是"把阶段区间的左端当成了确切日期"**：

    `final` / `preliminary` 这两个字段装的往往是**一段区间**，不是某一天。例如
      「外研社·国才杯」 `final = 2026-10-01 ~ 2026-12-31`，note 明写"国赛 10—12 月"
      ACM-ICPC        `final = 2026-10-01 ~ 2026-12-31`，note 明写"赛期 2026 年秋季，以各赛站通知为准"
    两者 `final.start` 都是 2026-10-01。第一版逻辑"取最早的未来日期"就会选中它，
    渲染成「决赛：2026-10-01 · 还有 8 天」—— **把区间起点包装成了硬日期**，
    而且还盖过了更该被盯的 `registration.end`（外语大赛 10-04 报名截止）。

    修正规则（**只保留"能代表某一天"的字段**）：
      ✅ `registration.start` —— 报名开放日，是真·某一天，且"报名 39 天后开始"对读者有用
      ✅ `registration.end`   —— 报名截止，最硬的 DDL
      ✅ `preliminary.end`    —— 初赛/预选结束，可视为收口
      ✅ `final.end`          —— 决赛结束
      ❌ `preliminary.start` / `final.start` —— **阶段区间的左端，不是确切日期，一律排除**

    在这四个字段里取**最早的未来日期**；全为过去时取最后一个过去日期（用于标"已结束"）。

    为什么不改成"截止优先"：那样会把"报名还没开始"的赛事（大创 11-01 开放、力学 12-01 开放）
    整个挤出提醒窗口 —— 而它们恰恰是**现在就该开始留意**的。
    """
    allowed = (("registration", "start", "报名开始"),
               ("registration", "end", "报名截止"),
               ("preliminary", "end", "初赛结束"),
               ("final", "end", "决赛结束"))
    cands = []
    for grp, key, label in allowed:
        d = _parse(((schedule or {}).get(grp) or {}).get(key))
        if d:
            cands.append((d, label))
    if not cands:
        return None, ""
    fut = [c for c in cands if c[0] >= today]
    if fut:
        fut.sort(key=lambda c: c[0])
        return fut[0][0], fut[0][1]
    past = sorted(cands, key=lambda c: c[0])
    return past[-1][0], past[-1][1]


def load_prep_index():
    """把备考文件按赛事名建索引，用于合并 prep_links。

    返回 `(dict, list)`：`dict` 供精确命中，`list` 供包含式兜底匹配。
    """
    if not os.path.exists(PREP_FILE):
        return {}, []
    try:
        with open(PREP_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[calendar] ⚠ 备考文件解析失败，跳过链接合并: {e}", file=sys.stderr)
        return {}, []
    idx, items = {}, []
    for c in data.get("competitions") or []:
        name = (c.get("name") or "").strip()
        if name:
            k = _norm_name(name)
            idx[k] = c
            items.append((k, c))
    return idx, items


# 括号处理（2026-09-23 第二次修正，两处都要改对）：
#
# ① **必须用栈式配对，不能用正则配对**。第一版用
#    `[（(【\[「《][^）)】\]」》]*[）)】\]」》]` 去匹"开括号+内容+闭括号"，
#    遇到**嵌套括号**会配错 —— 实测：
#      `中国国际大学生创新大赛（原「互联网+」大学生创新创业大赛）`
#    正则会从 `（` 一路吃到 `」`（因为「是开括号但它不在排除集里），
#    删掉 `（原「互联网+」`，留下孤立的 `）`，归一化结果变成
#    `中国国际大学生创新大赛大学生创新创业大赛）` —— 永远匹配不上赛历里的
#    `中国国际大学生创新大赛`，所以"备考链接一条都进不了邮件"。
#
# ② **括号里的内容不能当噪声丢掉 —— 它常常是别名**。合工大/赛历里大量使用
#    `（原「互联网+」大学生创新创业大赛）`、`（大挑）`、`（CUMCM / 高教社杯）` 这种写法，
#    括号里那个名字往往才是能和别的数据源对上的那个。
#    所以 `_name_variants()` 会**同时返回"主名"与"各括号内片段"**，任一命中即算匹配。
_OPEN = "（(【[「《"
_CLOSE = "）)】]」》"


def _strip_parens(s: str) -> str:
    """栈式剥离**所有括号及其内容**（支持嵌套，且能容忍不平衡的括号）。"""
    out, depth = [], 0
    for ch in s:
        if ch in _OPEN:
            depth += 1
            continue
        if ch in _CLOSE:
            if depth > 0:
                depth -= 1
            continue            # 孤立闭括号也直接丢掉，不留残渣
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _punct(s: str) -> str:
    """去标点/空白/连字符/**残留括号字符**，只保留可用于比对的实质字符。

    括号字符也要去：嵌套括号会让"取括号内片段"的正则截出带残渣的别名
    （实测 `（原「互联网+」…）` 的别名会是 `原「互联网`），
    这类半截别名虽然一般不会误匹配，但留着就是脏数据。
    """
    return re.sub(
        r"[\s、，,;；:：·\-—_/\\\"'“”‘’+（(）)【】\[\[］\]「」《》]+",
        "", str(s or "")
    ).strip()


def _norm_name(s: str) -> str:
    """赛事名归一化（主名）：剥离括号与其内容，再去标点。"""
    return _punct(_strip_parens(str(s or "")))


def _name_variants(s: str) -> set:
    """返回可用于匹配的名字候选集：**主名 + 各括号内片段**。

    例：`中国国际大学生创新大赛（原「互联网+」大学生创新创业大赛）`
        → {`中国国际大学生创新大赛`, `原互联网大学生创新创业大赛`}
        `「挑战杯」全国大学生课外学术科技作品竞赛（大挑）`
        → {`全国大学生课外学术科技作品竞赛`, `挑战杯`, `大挑`}
    这样赛历名和备考名之间只要**有一个别名对得上**就算同一赛事。
    """
    raw = str(s or "")
    cands = {_norm_name(raw)}
    # 逐层取括号内片段（最内层也要能取到，所以每一层都全量扫）
    for seg in re.findall(rf"[{re.escape(_OPEN)}]([^{re.escape(_CLOSE)}]*)[{re.escape(_CLOSE)}]", raw):
        v = _punct(seg)
        if v:
            cands.add(v)
    return {c for c in cands if c}


def prep_bundle_for(comp_name: str, prep_idx, prep_items=None):
    """返回该赛事的备考配套：`{"links": [...], "roadmap_brief": "..."}`。

    ⚠️ 2026-09-23 修正：`roadmap` 与 `experience` 都在**备考文件**里
    （`hfut_competition_prep.json` 的 `competitions[].roadmap / .experience`），
    不在赛历文件里。第一版去 `hfut_competitions.json` 上找 `roadmap`，
    自然一条都找不到 —— 于是"准备路线"这个用户明确点名的需求**整块没进邮件**。
    现在统一从**匹配到的备考记录**里取。
    """
    rec = _match_prep(comp_name, prep_idx, prep_items)
    if not rec:
        return {"links": [], "roadmap_brief": ""}

    out, seen = [], set()
    for key_g, fallback in (("official_outline", "官方大纲"), ("past_papers", "历年真题"),
                            ("tutorials", "官方培训"), ("books", "推荐书目"),
                            ("tools", "工具环境")):
        for ln in (rec.get("prep") or {}).get(key_g) or []:
            if not isinstance(ln, dict):
                continue
            u, lbl = (ln.get("url") or "").strip(), (ln.get("label") or fallback).strip()
            if u and u not in seen:
                seen.add(u)
                out.append({"label": lbl[:18], "url": u})

    # 经验贴 / 小红书笔记也要进邮件：用户原话是「官方信息和**一些经验贴或者小红书笔记**」。
    # ⚠️ 必须在截断**之前**给它们留位置（2026-09-23 第二次修正）：
    #    第一版把官方链接全放前面再 `[:5]`，而实测有 4 个赛事的官方链接正好满 5 条，
    #    于是经验类被**全部挤掉** —— 小红书的条数一直是 0，等于用户点名要的东西又没进邮件。
    #    现在：官方最多留 4 条，经验类最多补 2 条，总数上限 6。
    exp_rank = {"小红书": 0, "经验贴": 1, "官方": 2}
    exps = [e for e in (rec.get("experience") or [])
            if isinstance(e, dict) and (e.get("url") or "").strip()]
    exps.sort(key=lambda e: exp_rank.get((e.get("type") or "").strip(), 9))
    exp_links = []
    for e in exps[:2]:
        u = e["url"].strip()
        if u in seen:
            continue
        seen.add(u)
        t = (e.get("type") or "").strip()
        exp_links.append({"label": {"小红书": "小红书笔记", "经验贴": "经验贴",
                                    "官方": "官方攻略"}.get(t, t or "经验贴"), "url": u})
    # 官方 ≤4 条 + 经验类 ≤2 条 = 最多 6 条。
    # ⚠️ 2026-09-23 第三次修正：第一版这里还写了个尾部 `out[:5]`，
    #    于是"4+2=6"只是注释里的谎话，实际永远只有 1 条经验贴能活下来
    #    （由 browser-daemon 在真机渲染时数出来：小红书 4 条、经验贴 0 条，
    #     而源文件里华为ICT/机械工程创新创意各有 3+3 条 experience 全被吃掉）。
    #    这类"注释说的和代码做的不一致"最容易骗过复核，所以注释必须和上限一起改。
    out = out[:4] + exp_links[:2]

    # 准备路线速览：四段取 `focus` 拼一行（完整内容仍留在 JSON 里）
    focuses = []
    for p in (rec.get("roadmap") or []):
        if isinstance(p, dict) and (p.get("focus") or "").strip():
            focuses.append(p["focus"].strip()[:14])
    return {"links": out[:5], "roadmap_brief": " → ".join(focuses[:4])}


def _match_prep(comp_name: str, prep_idx, prep_items=None):
    """把赛历赛事对到备考记录上（候选集精确 → 包含式兜底）。

    候选集 = 主名 + 各括号内别名（见 `_name_variants`）。
    包含式兜底是必需的：备考文件里有一条 `中国国际大学生创新大赛 / "挑战杯"系列竞赛`
    **一条覆盖两个赛事**，赛历里的「挑战杯」靠别名 `挑战杯` 才能对上它。
    """
    variants = _name_variants(comp_name)
    for v in variants:
        if v in prep_idx:
            return prep_idx[v]
    if prep_items:
        cands = [(k, v) for k, v in prep_items
                 if any(vv and (vv in k or k in vv) for vv in variants)]
        if cands:
            cands.sort(key=lambda kv: -len(kv[0]))
            return cands[0][1]
    return None


def build(today: date | None = None, lead_days: int = REMIND_LEAD_DAYS,
          include_expired: bool = False, use_prep: bool = True):
    """返回 (competitions 数组, 统计信息 dict)。"""
    today = today or date.today()
    if not os.path.exists(COMP_FILE):
        raise SystemExit(f"[calendar] 赛事清单不存在: {COMP_FILE}")
    with open(COMP_FILE, encoding="utf-8") as f:
        data = json.load(f)

    prep_idx, prep_items = (load_prep_index() if use_prep else ({}, []))
    rows, skipped_no_date, skipped_far, expired = [], 0, 0, 0

    for c in data.get("competitions") or []:
        d, label = pick_next_key_date(c.get("schedule") or {}, today)
        if not d:
            skipped_no_date += 1
            print(f"[calendar] ⚠ 无可用日期，跳过: {c.get('name')}", file=sys.stderr)
            continue
        days = (d - today).days
        if days < 0:
            expired += 1
            if not include_expired:
                continue
        elif lead_days and days > lead_days:
            skipped_far += 1
            continue

        item = {
            "name": c.get("name"),
            "level": c.get("level"),
            "deadline": d.strftime("%Y-%m-%d"),
            "deadline_label": label,
            "official_url": c.get("official_url") or c.get("url"),
        }
        if c.get("organizer"):
            item["organizer"] = c["organizer"]
        # note 取赛程说明里最有信息量的一条，别把整段 JSON 塞进去
        sched = c.get("schedule") or {}
        for grp, key, _ in _DATE_FIELDS:
            if grp == "registration" and key == "end":
                n = ((sched.get(grp) or {}).get("note") or "").strip()
                if n:
                    item["note"] = n[:120]
                break
        if not item.get("note") and c.get("fit_reason"):
            item["note"] = str(c["fit_reason"])[:120]
        bundle = prep_bundle_for(c.get("name") or "", prep_idx, prep_items)
        if bundle["links"]:
            item["prep_links"] = bundle["links"]
        # 准备路线（2026-09-23 补）：用户原话要「一个完整的准备路线」，
        # 但第一版只挑了"下一个关键日期"，roadmap 整个没进邮件。
        # 邮件是每日摘要，塞不下四段完整 roadmap，所以取每段的 `focus` 拼成一行
        # （如「补基础 → 刷真题 → 组队模拟 → 冲刺」），完整内容仍在 JSON 里。
        if bundle["roadmap_brief"]:
            item["roadmap_brief"] = bundle["roadmap_brief"]
        rows.append(item)

    # 越紧迫越靠前；同天数按赛事名稳定排序
    rows.sort(key=lambda r: (_parse(r["deadline"]) - today).days)
    stats = {
        "today": today.strftime("%Y-%m-%d"),
        "lead_days": lead_days,
        "total_in_file": len(data.get("competitions") or []),
        "in_window": len(rows),
        "skipped_no_date": skipped_no_date,
        "skipped_too_far": skipped_far,
        "expired": expired,
        "prep_matched": sum(1 for r in rows if r.get("prep_links")),
    }
    return rows, stats


def print_report(rows, stats):
    print("=" * 74)
    print(f"赛事日历 · 基准日 {stats['today']} · 提醒窗口 {stats['lead_days']} 天")
    print(f"清单共 {stats['total_in_file']} 项 → 窗口内 {stats['in_window']} 项"
          f"（窗口外 {stats['skipped_too_far']} / 无日期 {stats['skipped_no_date']}"
          f" / 已过期 {stats['expired']}）")
    print(f"其中 {stats['prep_matched']} 项带备考资料链接")
    print("=" * 74)
    if not rows:
        print("（窗口内暂无赛事）")
        return
    base = datetime.strptime(stats["today"], "%Y-%m-%d").date()
    for r in rows:
        days = (_parse(r["deadline"]) - base).days
        flag = "🔴" if days <= 14 else ("🟠" if days <= 30 else "🔵")
        lvl = f"[{r.get('level')}]" if r.get("level") else "[?]"
        print(f"{flag} {days:>4} 天 | {r['deadline']} {r['deadline_label']:<6} | {lvl} {r['name']}")
        print(f"          {r.get('official_url')}")
        for ln in r.get("prep_links") or []:
            print(f"          └─ {ln['label']}: {ln['url']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="赛事日历计算器（邮件 competitions 数组生成）")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD，默认今天")
    ap.add_argument("--lead", type=int, default=REMIND_LEAD_DAYS,
                    help=f"提醒窗口天数，默认 {REMIND_LEAD_DAYS}（=3 个月）；0=不过滤")
    ap.add_argument("--include-expired", action="store_true", help="连已过期的一起输出")
    ap.add_argument("--no-prep", action="store_true", help="不合并备考资料链接")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--report", action="store_true", help="打印人读表格")
    g.add_argument("--emit", action="store_true", help="输出 JSON 数组到 stdout")
    ap.add_argument("--emit-into", default=None, metavar="DATA_JSON",
                    help="把 competitions 数组写进指定的邮件数据 JSON（原地补键）")
    args = ap.parse_args()

    today = _parse(args.today) or date.today()
    rows, stats = build(today=today, lead_days=args.lead,
                        include_expired=args.include_expired,
                        use_prep=not args.no_prep)

    if args.emit_into:
        if not os.path.exists(args.emit_into):
            print(f"[calendar] 目标数据文件不存在: {args.emit_into}", file=sys.stderr)
            return 2
        with open(args.emit_into, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            print("[calendar] 目标数据文件顶层必须是对象", file=sys.stderr)
            return 2
        payload["competitions"] = rows
        payload.setdefault("date", today.strftime("%Y-%m-%d"))
        with open(args.emit_into, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[calendar] 已写入 {args.emit_into}：competitions = {len(rows)} 项"
              f"（窗口 {stats['lead_days']} 天，基准 {stats['today']}）")
        return 0

    if args.emit:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    print_report(rows, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
