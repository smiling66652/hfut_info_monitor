#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《每日情报》邮件正文 —— 固定模板渲染器（2026-09-23 新增）。

## 为什么需要它

2026-09-18 起，《每日情报》正文一直是「每天由模型现场写一份 HTML」。
带来的两个必然问题（用户 2026-09-23 反馈）：

1. **顶端色彩每天漂移**：没有固定样式，模型每天重写一遍 CSS，
   header 从深蓝到墨黑到深灰都出现过，与紧随其后的橙色 DDL 块撞色。
2. **DDL 条目丢链接**：DDL 块是自由段落，没有结构约束，
   模型常写成「创新大赛国赛报名：9月25日 12:00 截止 — 请准备材料」，
   而硬性要求里写的是"每条信息必须带来源链接"。

根治办法不是"下次写得好看点"，而是**把版式固化成模板**：
模型只产出结构化 JSON，样式与链接由本模块强制保证。

## 用法

```bash
# 1) 写一份 data.json（见下方 SCHEMA），2) 渲染
python scripts/daily_intel_template.py --data d.json --out temp_files/daily_intel_2026-09-23.html

# 只校验不写文件
python scripts/daily_intel_template.py --data d.json --check
```

也支持 `--stdin` 从标准输入读 JSON。

## 数据结构（SCHEMA）

```json
{
  "date": "2026-09-23",
  "scope_note": "覆盖最近 7 天",            // 可选
  "ddls": [                                 // 顶部 DDL 提醒，url 必填
    {"deadline": "9月25日 12:00 截止",
     "title": "中国国际大学生创新大赛国赛报名",
     "url": "https://...",
     "note": "需提交参赛材料",              // 可选
     "source": "科研院"}                    // 可选
  ],
  "hfut": [                                 // 合工大官网，url 必填
    {"channel": "科研院-通知公告", "title": "...", "url": "https://...",
     "date": "2026-09-20", "tag": "funding"}   // tag 可选
  ],
  "github": [                               // url 必填
    {"name": "Bytebot", "stars": "8,213", "desc": "...", "url": "https://..."}
  ],
  "baoyan": [                               // 保研竞赛，url 必填
    {"title": "...", "desc": "...", "url": "https://..."}
  ]
}
```

`tag` 取值：`funding` | `scholarship` | `competition` | `notice` | `academic` | `career`。

### 2026-09-23 新增两个并列栏目

```json
{
  "competitions": [
    {"name": "全国大学生数学建模竞赛", "level": "A",
     "deadline": "2026-11-20", "deadline_label": "报名截止",
     "official_url": "https://...", "organizer": "...", "note": "一句话",
     "prep_links": [{"label": "历年真题", "url": "https://..."}]}
  ],
  "opportunities": [
    {"name": "...", "url": "https://...", "org": "安徽省教育厅",
     "type": "人才培养计划 | 基金 | 企业培养计划 | 招聘 | 大厂活动 | 奖学金",
     "region": "全国 | 安徽", "deadline": "2026-10-15", "desc": "一句话"}
  ]
}
```

- `competitions` 的 `deadline` 是**下一个该盯的关键日期**（报名/初赛/决赛里最早的那个未来日期），
  由 `scripts/competition_calendar.py` 从 `hfut_site/hfut_competitions.json` 算出，
  **不要在正文里手写**。模板据此显示「还有 N 天」并按四档换色。
- 栏目顺序：DDL → 官网 → **赛事日历** → **成长机会** → 保研竞赛 → GitHub 趋势。

## 设计约定（改样式只改这一个文件）

- **顶端：浅色简洁**（2026-09-23 用户选定方向）。极浅蓝底 `#f6f9fd` + 左侧 4px 品牌蓝竖条 +
  统计数字横排。**不要大面积深色底**；颜色只用在细线、数字、链接这些强调点上。
  被弃用的旧样式是 `linear-gradient(135deg,#1e40af,#4f46e5,#7c3aed)` 的大块深色渐变 + 底部彩条
  —— 用户明确反馈"不喜欢"，别再改回去。
- 浅色主题的另一个理由：邮件客户端对浅底深字渲染更稳定
  （深色底上的 `rgba()` 半透明在 QQ 邮箱里会掉色）。
- 所有条目一律 `target="_blank"` 且**禁止 `href="#"`**（模板层强制，见 `_assert_url`）。
- 文本进 HTML 前统一转义，避免标题里的 `&`/`<` 撑坏排版。
- ⚠️ 写新的"按 class 取块"的正则时**注意嵌套**：`<div class="comp">` 内部有嵌套 div，
  非贪婪 `(.*?)</div>` 会停在第一个 `</div>` 而漏掉后半块内容（`lint_html` 2026-09-23 踩过）。
  用"块起点切片"而不是非贪婪匹配。
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from datetime import datetime

# ── 校验 ────────────────────────────────────────────────────────────


class TemplateDataError(ValueError):
    """数据结构不合法（缺 url / 缺 title / 类型错）。"""


_JUNK_URLS = {"", "#", "javascript:void(0)", "about:blank", "None", "null"}


def _assert_url(url: str, where: str) -> str:
    """条目必须带可点击的真实链接 —— 这是本模板存在的首要理由。"""
    u = (url or "").strip()
    if u.lower() in _JUNK_URLS:
        raise TemplateDataError(
            f"{where}: 缺少有效链接（url={url!r}）。"
            f"每条信息都必须给出可点击的来源链接，不允许留空或用 '#' 占位。"
        )
    if not re.match(r"^https?://", u, re.I):
        raise TemplateDataError(f"{where}: url 必须是 http/https 开头（得到 {u!r}）")
    return u


def _esc(s) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


def _need_str(row: dict, key: str, where: str) -> str:
    v = (row.get(key) or "").strip() if isinstance(row.get(key), str) else row.get(key)
    if not v:
        raise TemplateDataError(f"{where}: 缺少必填字段 {key!r}")
    return str(v)


_TAG_LABELS = {
    "funding": ("科研基金", "tag-funding"),
    "scholarship": ("奖学金", "tag-scholarship"),
    "competition": ("竞赛", "tag-competition"),
    "notice": ("通知", "tag-notice"),
    "academic": ("学术", "tag-academic"),
    "career": ("就业", "tag-career"),
}


# ── 样式（唯一真源）────────────────────────────────────────────────

# ⚠️ 下面这个字符串会**原样进邮件 HTML**，所以：
#   - 只放 CSS，不要放设计说明/历史沿革 —— 笔记请写成 Python 注释（就是你现在读的这种），
#     否则既泄露内部思路又多出几 KB 垃圾（2026-09-23 踩过：旧样式说明被渲染进邮件）。
#   - 顶端是「浅色简洁」：极浅蓝底 + 左 4px 品牌竖条 + 统计数字横排。
#     被弃用的旧样式是 linear-gradient(135deg,#1e40af,#4f46e5,#7c3aed) 的大块深色渐变 + 底部彩条，
#     用户明确说不喜欢，别再改回去。
STYLE = """
  * { box-sizing: border-box; }
  body { font-family:"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
         background:#eef2f7; color:#1f2937; margin:0; padding:20px 12px; }
  .container { max-width:720px; margin:0 auto; background:#fff; border-radius:14px;
               box-shadow:0 6px 24px rgba(15,23,42,.10); overflow:hidden; }

  /* ── 顶端（2026-09-23 改版：浅色简洁）────────────────────────── */
  .header { background:#f6f9fd; border-left:4px solid #2563eb;
            padding:22px 26px 20px; color:#0f172a; }
  .header h1 { margin:0; font-size:22px; font-weight:700; color:#0f172a;
               letter-spacing:.01em; }
  .header .sub { margin:6px 0 0; font-size:12.5px; color:#64748b; }
  .stats { margin-top:16px; display:flex; gap:26px; flex-wrap:wrap; }
  .stats .st .n { font-size:19px; font-weight:700; color:#1d4ed8; line-height:1.1; }
  .stats .st .l { font-size:11.5px; color:#64748b; margin-top:2px; }


  /* ── DDL 提醒 ─────────────────────────────────── */
  .ddl-wrap { padding:16px 24px 0; }
  .ddls { background:#fffbeb; border:1px solid #fde68a; border-left:4px solid #f59e0b;
          border-radius:0 10px 10px 0; padding:14px 18px 12px; }
  .ddls-title { margin:0 0 10px; font-size:15px; font-weight:700; color:#b45309; }
  .ddls ul { margin:0; padding-left:16px; }
  .ddls li { margin:0 0 12px; font-size:13px; line-height:1.65; }
  .ddls li:last-child { margin-bottom:2px; }
  .ddl-deadline { display:inline-block; font-weight:700; color:#b91c1c;
                  background:#fee2e2; border-radius:5px; padding:1px 7px; margin-right:6px; }
  .ddl-title { font-weight:600; color:#1f2937; }
  .ddl-link { display:inline-block; margin-left:6px; font-size:12px; color:#1d4ed8;
              text-decoration:none; border-bottom:1px dashed #93c5fd; }
  .ddl-note { color:#6b7280; font-size:12px; margin-top:3px; }
  .ddl-src { color:#5b6b7b; font-size:11px; margin-top:2px; }

  /* ── 分区 ─────────────────────────────────────── */
  .section { border-top:1px solid #eef2f7; }
  details { padding:0 24px; }
  summary { padding:16px 0; cursor:pointer; font-size:16px; font-weight:600;
            color:#111827; list-style:none; display:flex; align-items:center;
            gap:8px; user-select:none; }
  summary::-webkit-details-marker { display:none; }
  summary::before { content:"\\25B8"; font-size:12px; color:#6366f1;
                    transition:transform .2s; display:inline-block; }
  details[open] summary::before { transform:rotate(90deg); }
  .badge { display:inline-block; font-size:11px; padding:2px 9px; border-radius:999px;
           font-weight:600; margin-left:auto; }
  .badge-sci { background:#eef2ff; color:#4338ca; }
  .badge-comp { background:#eef2ff; color:#3730a3; }
  .badge-opp { background:#ecfdf5; color:#047857; }
  .badge-github { background:#fff7ed; color:#c2410c; }
  .badge-baoyan { background:#fdf2f8; color:#be185d; }
  .content { padding:0 0 20px; }

  /* ── 条目 ─────────────────────────────────────── */
  .item { padding:12px 0; border-bottom:1px dashed #eef2f7; }
  .item:last-child { border-bottom:none; }
  .item-title { font-size:14px; font-weight:600; color:#111827; margin-bottom:5px;
                line-height:1.55; }
  .item-title a { color:#111827; text-decoration:none; }
  .item-title a:hover { color:#4f46e5; }
  .item-meta { font-size:12px; color:#64748b; display:flex; align-items:center;
               gap:8px; flex-wrap:wrap; }
  .item-meta a { color:#1d4ed8; text-decoration:none; }
  .item-meta a:hover { text-decoration:underline; }
  .item-tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:5px;
              background:#f3f4f6; color:#4b5563; }
  .tag-funding { background:#e3f2fd; color:#1565c0; }
  .tag-scholarship { background:#fff8e1; color:#b45309; }
  .tag-competition { background:#e8f5e9; color:#2e7d32; }
  .tag-notice { background:#eef2ff; color:#4338ca; }
  .tag-academic { background:#f5f3ff; color:#6d28d9; }
  .tag-career { background:#ecfeff; color:#0e7490; }

  /* ── 卡片 ─────────────────────────────────────── */
  .card { margin:10px 0; padding:13px 16px; background:#fafbfc; border-radius:9px;
          border:1px solid #eef2f7; }
  .card .name { font-weight:700; font-size:14px; color:#111827; }
  .card .stars { font-size:12px; color:#b45309; margin-left:8px; }
  .card .title { font-weight:600; font-size:14px; color:#111827; line-height:1.55; }
  .card .desc { font-size:13px; color:#4b5563; margin-top:5px; line-height:1.68; }
  .card .url { font-size:12px; margin-top:6px; }
  .card .url a { color:#1d4ed8; text-decoration:none; }
  .card .url a:hover { text-decoration:underline; }

  /* ── 赛事日历（带倒计时）────────────────────────
     需求（2026-09-23 用户原话）：「一天又一天的计时，当 A、B 类赛事还有 3 个月的时候开始提醒」
     所以每条都显示"还有 N 天"，并按剩余天数分四档换左边框与文字颜色：
       ≥90 天 → 灰（尚远）  ｜  ≤90 天 → 蓝（进入提醒窗口）
       ≤30 天 → 琥珀（紧迫）｜  ≤14 天 → 红（临界）                        */
  .comp { margin:10px 0; padding:12px 14px; background:#fff; border:1px solid #e5e7eb;
          border-left:3px solid #cbd5e1; border-radius:8px; }
  .comp.c-lead { border-left-color:#2563eb; }
  .comp.c-urgent { border-left-color:#f59e0b; background:#fffdf5; }
  .comp.c-critical { border-left-color:#dc2626; background:#fff5f5; }
  .comp .top { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  .comp .name { font-weight:700; font-size:14px; color:#0f172a; }
  .comp .lvl { font-size:11px; font-weight:700; padding:1px 7px; border-radius:4px; }
  .comp .lvl-a { background:#eef2ff; color:#3730a3; }
  .comp .lvl-b { background:#f1f5f9; color:#475569; }
  .comp .cd { margin-left:auto; font-size:12px; font-weight:700; white-space:nowrap; }
  .comp .cd-far { color:#64748b; }
  .comp .cd-lead { color:#1d4ed8; }
  .comp .cd-urgent { color:#b45309; }
  .comp .cd-critical { color:#b91c1c; }
  .comp .meta { font-size:12px; color:#64748b; margin-top:5px; }
  .comp .note { font-size:12.5px; color:#4b5563; margin-top:5px; line-height:1.65; }
  /* 准备路线一行速览：用 focus 拼成「补基础 → 刷真题 → 组队模拟 → 冲刺」 */
  .comp .prep { font-size:12.5px; color:#475569; margin-top:5px; line-height:1.65;
                background:#f8fafc; border-radius:6px; padding:5px 9px; }
  .comp .prep b { color:#334155; font-weight:600; margin-right:6px; }
  .comp .links { margin-top:7px; display:flex; flex-wrap:wrap; gap:12px; font-size:12px; }
  .comp .links a { color:#1d4ed8; text-decoration:none; }
  .comp .bar { height:3px; background:#f1f5f9; border-radius:2px; margin-top:9px; overflow:hidden; }
  .comp .bar i { display:block; height:100%; background:#bfdbfe; }
  .comp.c-urgent .bar i { background:#fcd34d; }
  .comp.c-critical .bar i { background:#fca5a5; }

  /* ── 成长机会 ─────────────────────────────────── */
  .opp { margin:10px 0; padding:12px 14px; background:#fafbfc; border:1px solid #eef2f7;
         border-radius:8px; }
  .opp .hd { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  .opp .name { font-weight:700; font-size:14px; color:#0f172a; }
  .opp .org { font-size:12px; color:#64748b; }
  .opp .desc { font-size:13px; color:#4b5563; margin-top:5px; line-height:1.65; }
  .opp .meta { font-size:12px; color:#64748b; margin-top:7px; display:flex;
               flex-wrap:wrap; gap:10px; align-items:center; }
  .opp .t { font-size:11px; padding:1px 7px; border-radius:5px; background:#ecfdf5; color:#047857; }
  .opp .dl { font-weight:700; color:#b91c1c; }
  .opp .url a { color:#1d4ed8; text-decoration:none; }

  .empty { padding:4px 0 18px; font-size:13px; color:#5b6b7b; }
  .footer { padding:16px 24px; background:#f8fafc; font-size:12px;
            color:#5b6b7b; text-align:center; line-height:1.7; }
"""


def _render_ddls(ddls: list) -> str:
    if not ddls:
        return ""
    rows = []
    for i, d in enumerate(ddls):
        where = f"ddls[{i}]"
        if not isinstance(d, dict):
            raise TemplateDataError(f"{where}: 应为对象")
        url = _assert_url(d.get("url"), where)
        title = _need_str(d, "title", where)
        deadline = _need_str(d, "deadline", where)
        note = (d.get("note") or "").strip()
        source = (d.get("source") or "").strip()
        rows.append(
            "        <li>"
            f'<span class="ddl-deadline">{_esc(deadline)}</span>'
            f'<span class="ddl-title">{_esc(title)}</span>'
            f'<a class="ddl-link" href="{_esc(url)}" target="_blank">查看通知 &rarr;</a>'
            + (f'<div class="ddl-note">{_esc(note)}</div>' if note else "")
            + (f'<div class="ddl-src">来源：{_esc(source)}</div>' if source else "")
            + "</li>"
        )
    return (
        '  <div class="ddl-wrap">\n'
        '    <div class="ddls">\n'
        f'      <div class="ddls-title">\u23f0 近期 DDL 提醒（{len(ddls)} 项）</div>\n'
        "      <ul>\n"
        + "\n".join(rows)
        + "\n      </ul>\n    </div>\n  </div>\n"
    )


def _render_hfut(rows: list) -> str:
    if not rows:
        body = '        <div class="empty">今日官网栏目未取到近 7 天新通知。</div>\n'
    else:
        out = []
        for i, it in enumerate(rows):
            where = f"hfut[{i}]"
            url = _assert_url(it.get("url"), where)
            title = _need_str(it, "title", where)
            channel = (it.get("channel") or "官网").strip()
            date = (it.get("date") or "").strip()
            label, css = _TAG_LABELS.get((it.get("tag") or "").strip(), (None, None))
            tag_html = f'<span class="item-tag {css}">{_esc(label)}</span>' if label else ""
            meta_date = f"<span>{_esc(date)}</span>" if date else ""
            out.append(
                '        <div class="item">\n'
                f'          <div class="item-title"><a href="{_esc(url)}" target="_blank">{_esc(title)}</a></div>\n'
                f'          <div class="item-meta">{tag_html}<span>{_esc(channel)}</span>{meta_date}'
                f'<a href="{_esc(url)}" target="_blank">原文 &rarr;</a></div>\n'
                "        </div>"
            )
        body = "\n".join(out) + "\n"
    count_badge = f"{len(rows)} 条" if rows else "无"
    return (
        '  <div class="section">\n'
        "    <details open>\n"
        f'      <summary><span>\U0001f3eb</span> 合工大官网 <span class="badge badge-sci">{count_badge}</span></summary>\n'
        '      <div class="content">\n'
        + body
        + "      </div>\n    </details>\n  </div>\n"
    )


def _render_github(rows: list) -> str:
    if not rows:
        body = '        <div class="empty">今日未检索到值得关注的仓库。</div>\n'
    else:
        out = []
        for i, it in enumerate(rows):
            where = f"github[{i}]"
            url = _assert_url(it.get("url"), where)
            name = _need_str(it, "name", where)
            desc = _need_str(it, "desc", where)
            stars = (it.get("stars") or "").strip()
            stars_html = f'<span class="stars">\u2b50 {_esc(stars)}</span>' if stars else ""
            display = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
            out.append(
                '        <div class="card">\n'
                f'          <div><span class="name">{_esc(name)}</span>{stars_html}</div>\n'
                f'          <div class="desc">{_esc(desc)}</div>\n'
                f'          <div class="url"><a href="{_esc(url)}" target="_blank">{_esc(display)}</a></div>\n'
                "        </div>"
            )
        body = "\n".join(out) + "\n"
    count_badge = f"{len(rows)} 个" if rows else "无"
    return (
        '  <div class="section">\n'
        "    <details>\n"
        f'      <summary><span>\u2b50</span> GitHub 趋势 <span class="badge badge-github">AI Agent / LLM / RAG · {count_badge}</span></summary>\n'
        '      <div class="content">\n'
        + body
        + "      </div>\n    </details>\n  </div>\n"
    )


def _render_baoyan(rows: list) -> str:
    if not rows:
        body = '        <div class="empty">今日无新增保研 / 竞赛情报。</div>\n'
    else:
        out = []
        for i, it in enumerate(rows):
            where = f"baoyan[{i}]"
            url = _assert_url(it.get("url"), where)
            title = _need_str(it, "title", where)
            desc = (it.get("desc") or "").strip()
            display = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
            out.append(
                '        <div class="card">\n'
                f'          <div class="title">{_esc(title)}</div>\n'
                + (f'          <div class="desc">{_esc(desc)}</div>\n' if desc else "")
                + f'          <div class="url"><a href="{_esc(url)}" target="_blank">{_esc(display)}</a></div>\n'
                "        </div>"
            )
        body = "\n".join(out) + "\n"
    count_badge = f"{len(rows)} 条" if rows else "无"
    return (
        '  <div class="section">\n'
        "    <details>\n"
        f'      <summary><span>\U0001f393</span> 保研与竞赛 <span class="badge badge-baoyan">{count_badge}</span></summary>\n'
        '      <div class="content">\n'
        + body
        + "      </div>\n    </details>\n  </div>\n"
    )


# ── 赛事倒计时（2026-09-23 新增）──────────────────────────────────────
# 需求原话：「一天又一天的计时，当 A、B 类赛事还有 3 个月的时候开始提醒」
# 所以以 90 天为「进入提醒窗口」的分界，并按剩余天数分四档做视觉分级。
REMIND_LEAD_DAYS = 90


def days_until(deadline, today):
    """距离 deadline 还有多少天（今天 = 0）。解析失败返回 None。"""
    try:
        d = datetime.strptime(str(deadline or "").strip(), "%Y-%m-%d").date()
        t = datetime.strptime(str(today or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (d - t).days


def comp_urgency(days, lead_days=REMIND_LEAD_DAYS):
    """把剩余天数映射成紧迫度档位：expired / critical / urgent / lead / far。"""
    if days is None:
        return "none"
    if days < 0:
        return "expired"
    if days <= 14:
        return "critical"
    if days <= 30:
        return "urgent"
    if days <= lead_days:
        return "lead"
    return "far"


def _countdown_text(days):
    if days is None:
        return ""
    if days < 0:
        return "已结束"
    if days == 0:
        return "今天截止"
    return f"还有 {days} 天"


def _render_competitions(rows, today) -> str:
    """赛事日历。每项含 A/B 级别、倒计时、备考资料直达链接。"""
    if not rows:
        body = '        <div class="empty">90 天窗口内暂无 A/B 类赛事，或赛历数据未更新。</div>\n'
    else:
        # 按紧迫度排序：剩余天数少的在前；没有日期的排最后
        def _sort_key(it):
            d = days_until(it.get("deadline"), today)
            return (d is None, d if d is not None else 0)
        out = []
        for i, it in enumerate(sorted(rows, key=_sort_key)):
            where = f"competitions[{i}]"
            url = _assert_url(it.get("official_url") or it.get("url"), where)
            name = _need_str(it, "name", where)
            days = days_until(it.get("deadline"), today)
            u = comp_urgency(days)
            lvl = (it.get("level") or "").strip().upper()
            lvl_html = ""
            if lvl in ("A", "B"):
                lvl_html = f'<span class="lvl lvl-{lvl.lower()}">{lvl} 类</span>'
            elif lvl:
                lvl_html = f'<span class="lvl lvl-b">{_esc(lvl)}</span>'
            cd = _countdown_text(days)
            cd_html = f'<span class="cd cd-{u if u != "none" else "far"}">{cd}</span>' if cd else ""
            dl_label = (it.get("deadline_label") or "关键日期").strip()
            dl = (it.get("deadline") or "").strip()
            meta_bits = []
            if dl:
                meta_bits.append(f"{_esc(dl_label)}：<b>{_esc(dl)}</b>")
            if it.get("organizer"):
                meta_bits.append(_esc(it["organizer"]))
            note = (it.get("note") or "").strip()
            roadmap = (it.get("roadmap_brief") or "").strip()
            links = [f'<a href="{_esc(url)}" target="_blank">官方主页 &rarr;</a>']
            for ln in (it.get("prep_links") or []):
                if isinstance(ln, dict) and ln.get("url") and ln.get("label"):
                    lu = _assert_url(ln["url"], f"{where}.prep_links")
                    links.append(f'<a href="{_esc(lu)}" target="_blank">{_esc(ln["label"])} &rarr;</a>')
            bar = ""
            if days is not None and days >= 0:
                # 进度条：已过比例（进入窗口后最长 90 天）
                pct = int(min(100, max(3, (1 - min(days, REMIND_LEAD_DAYS) / REMIND_LEAD_DAYS) * 100)))
                bar = f'<div class="bar"><i style="width:{pct}%"></i></div>'
            out.append(
                f'        <div class="comp c-{u if u != "none" else "far"}">\n'
                f'          <div class="top"><span class="name">{_esc(name)}</span>{lvl_html}{cd_html}</div>\n'
                + (f'          <div class="meta">{" · ".join(meta_bits)}</div>\n' if meta_bits else "")
                + (f'          <div class="note">{_esc(note)}</div>\n' if note else "")
                + (f'          <div class="prep"><b>准备路线</b>{_esc(roadmap)}</div>\n' if roadmap else "")
                + f'          <div class="links">{"".join(links)}</div>\n'
                + bar + "\n        </div>"
            )
        body = "\n".join(out) + "\n"
    n_lead = sum(1 for it in rows
                 if (days_until(it.get("deadline"), today) is not None
                     and 0 <= days_until(it.get("deadline"), today) <= REMIND_LEAD_DAYS))
    badge = f"{n_lead} 项临近" if rows else "无"
    return (
        '  <div class="section">\n'
        "    <details open>\n"
        f'      <summary><span>\U0001f3c6</span> 赛事日历（A/B 类） <span class="badge badge-comp">{badge}</span></summary>\n'
        '      <div class="content">\n'
        + body
        + "      </div>\n    </details>\n  </div>\n"
    )


def _render_opportunities(rows) -> str:
    """成长机会：各省市/教育厅/人才培养计划/基金/企业计划/大厂活动。"""
    if not rows:
        body = '        <div class="empty">今日未发现新的成长机会。</div>\n'
    else:
        out = []
        for i, it in enumerate(rows):
            where = f"opportunities[{i}]"
            url = _assert_url(it.get("url"), where)
            name = _need_str(it, "name", where)
            org = (it.get("org") or "").strip()
            desc = (it.get("desc") or it.get("why") or "").strip()
            otype = (it.get("type") or "").strip()
            region = (it.get("region") or "").strip()
            deadline = (it.get("deadline") or "").strip()
            meta = []
            if otype:
                meta.append(f'<span class="t">{_esc(otype)}</span>')
            if region:
                meta.append(f"<span>{_esc(region)}</span>")
            if deadline:
                meta.append(f'<span>截止 <span class="dl">{_esc(deadline)}</span></span>')
            meta.append(f'<span class="url"><a href="{_esc(url)}" target="_blank">查看详情 &rarr;</a></span>')
            out.append(
                '        <div class="opp">\n'
                f'          <div class="hd"><span class="name">{_esc(name)}</span>'
                + (f'<span class="org">{_esc(org)}</span>' if org else "") + "</div>\n"
                + (f'          <div class="desc">{_esc(desc)}</div>\n' if desc else "")
                + f'          <div class="meta">{"".join(meta)}</div>\n'
                "        </div>"
            )
        body = "\n".join(out) + "\n"
    badge = f"{len(rows)} 条" if rows else "无"
    return (
        '  <div class="section">\n'
        "    <details open>\n"
        f'      <summary><span>\U0001f331</span> 成长机会 <span class="badge badge-opp">{badge}</span></summary>\n'
        '      <div class="content">\n'
        + body
        + "      </div>\n    </details>\n  </div>\n"
    )


def render(data: dict) -> str:
    """把结构化数据渲染成固定的邮件正文 HTML。

    任何一条信息缺链接都会抛 `TemplateDataError` —— 这是刻意的：
    宁可渲染失败让调用方去补链接，也不要发出一封"点了没反应"的情报。
    """
    if not isinstance(data, dict):
        raise TemplateDataError("data 必须是 JSON 对象")

    date = (data.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    ddls = data.get("ddls") or []
    hfut = data.get("hfut") or []
    competitions = data.get("competitions") or []
    opportunities = data.get("opportunities") or []
    github = data.get("github") or []
    baoyan = data.get("baoyan") or []
    for key, val in (("ddls", ddls), ("hfut", hfut), ("competitions", competitions),
                     ("opportunities", opportunities), ("github", github), ("baoyan", baoyan)):
        if not isinstance(val, list):
            raise TemplateDataError(f"{key} 必须是数组")

    scope = (data.get("scope_note") or "").strip()
    _dl = [days_until(it.get("deadline"), date) for it in competitions]
    n_lead = sum(1 for d in _dl if d is not None and 0 <= d <= REMIND_LEAD_DAYS)
    stats_html = "".join(
        f'<div class="st"><div class="n">{n}</div><div class="l">{_esc(lbl)}</div></div>'
        for n, lbl in ((len(hfut), "官网条目"), (n_lead, "临近赛事"),
                       (len(opportunities), "成长机会"), (len(ddls), "待办 DDL")))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日情报 {_esc(date)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>\U0001f4ec 每日情报</h1>
    <p class="sub">{_esc(date)} · 合工大官网 · GitHub 趋势 · 保研竞赛 · 赛事日历 · 成长机会{(" · " + _esc(scope)) if scope else ""}</p>
    <div class="stats">{stats_html}</div>
  </div>
{_render_ddls(ddls)}{_render_hfut(hfut)}{_render_competitions(competitions, date)}{_render_opportunities(opportunities)}{_render_baoyan(baoyan)}{_render_github(github)}  <div class="footer">
    由合工大每日情报系统自动生成 · {_esc(date)}<br>
    每条信息均附原文链接，点标题即可直达 · 赛事倒计时以邮件日期为基准
  </div>
</div>
</body>
</html>
"""


# ── 事后校验（用于手写正文 / 历史正文兜底）────────────────────────

_RE_DDL_LI = re.compile(r'<li\b[^>]*>(.*?)</li>', re.I | re.S)
_RE_ANCHOR_HREF = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\']', re.I)
_RE_DIV_TOK = re.compile(r'</?div\b', re.I)


def _iter_div_blocks(html: str, cls: str):
    """产出每个顶层 `<div class="... cls ...">` 的内部 HTML。

    为什么不用 `(.*?)</div>`：comp / opp 条目内部还嵌着 .top / .meta / .links
    等多层 <div>，非贪婪正则会停在**第一个内层 `</div>`**（即 .top）上，
    把一条明明带链接的条目截成「只有名字+倒计时」，于是被误判为「没有链接」。
    这里改成括号配对扫描：从开标签起数 <div> / </div>，回到 0 才收尾。
    """
    open_re = re.compile(
        r'<div\b[^>]*\bclass\s*=\s*["\'][^"\']*\b' + re.escape(cls) + r'\b[^"\']*["\'][^>]*>',
        re.I,
    )
    for m in open_re.finditer(html):
        start = m.end()
        depth, pos = 1, start
        while depth > 0:
            t = _RE_DIV_TOK.search(html, pos)
            if not t:
                break
            depth += -1 if html[t.start():t.start() + 2] == "</" else 1
            pos = t.end()
            if depth == 0:
                yield html[start:t.start()]
                break


def lint_html(html: str) -> list:
    """检查一份正文 HTML 是否满足「条目都带真实链接」。

    返回问题描述列表（空列表 = 通过）。可直接用于 `send_daily_intel.py`
    发送前把关，防止临时手写的正文又把 DDL 写成无链接的纯文本。
    """
    problems = []
    if not html or not html.strip():
        return ["正文为空"]

    # 1) 全文档不得出现占位链接
    for m in _RE_ANCHOR_HREF.finditer(html):
        href = m.group(1).strip()
        if href.lower() in _JUNK_URLS:
            problems.append(f"存在占位/空链接: href={href!r}")

    # 2) DDL 区块内每条 <li> 必须带链接
    for ddl_block in re.findall(
        r'<div[^>]*class\s*=\s*["\'][^"\']*\bddls\b[^"\']*["\'][^>]*>(.*?)</div>\s*</div>',
        html, re.I | re.S,
    ):
        for idx, li in enumerate(_RE_DDL_LI.findall(ddl_block), 1):
            if not _RE_ANCHOR_HREF.search(li):
                text = re.sub(r"<[^>]+>", "", li).strip()[:40]
                problems.append(f"DDL 第 {idx} 条没有链接:「{text}」")

    # 2b) 赛事日历 / 成长机会 的每个条目也必须带链接（2026-09-23 新增两栏目时同步加）
    # ⚠️ 不能用 `<div ... class="comp" ...>(.*?)</div>` 这种非贪婪匹配：
    #    comp 块内部有嵌套 div（.top / .meta / .note），非贪婪会停在**第一个** </div>，
    #    于是永远扫不到后面的 .links，导致**全部条目被误报"没有链接"**
    #    （2026-09-23 实测踩到：渲染出的每条赛事都被自己的 lint 挡下，main() 直接 exit 4）。
    #    也不能用「本块起点 → 下一块起点」的切片：**每类最后一条**的区间会一直延伸到
    #    文末，把后面 opp / baoyan / github 的链接算进来，于是漏报"最后一条无链接"。
    #    改用 `_iter_div_blocks`：从开标签起做 <div>/</div> 括号配对，回到 0 才收尾，
    #    既不怕嵌套，也不会越界到下一个区块。
    for cls, label in (("comp", "赛事"), ("opp", "成长机会")):
        for idx, blk in enumerate(_iter_div_blocks(html, cls), 1):
            if not _RE_ANCHOR_HREF.search(blk):
                text = re.sub(r"<[^>]+>", "", blk).strip()[:40]
                problems.append(f"{label} 第 {idx} 条没有链接:「{text}」")

    # 3) 条目总数与链接数粗校验：有条目分区却一个链接都没有
    if re.search(r'class\s*=\s*["\'][^"\']*\bitem\b', html, re.I) and not _RE_ANCHOR_HREF.search(html):
        problems.append("正文有条目列表但全文不含任何链接")
    return problems


def _load_data(args) -> dict:
    if args.stdin:
        return json.load(sys.stdin)
    with open(args.data, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="《每日情报》固定模板渲染器")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="结构化数据 JSON 路径")
    src.add_argument("--stdin", action="store_true", help="从标准输入读 JSON")
    ap.add_argument("--out", help="输出 HTML 路径（缺省则打印到 stdout）")
    ap.add_argument("--check", action="store_true", help="只校验数据，不输出 HTML")
    args = ap.parse_args()

    try:
        data = _load_data(args)
    except FileNotFoundError:
        print(f"[template] 数据文件不存在: {args.data}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"[template] JSON 解析失败: {e}", file=sys.stderr)
        return 2

    try:
        html = render(data)
    except TemplateDataError as e:
        print(f"[template] 数据校验失败: {e}", file=sys.stderr)
        return 3

    problems = lint_html(html)
    if problems:
        for p in problems:
            print(f"[template] 排版告警: {p}", file=sys.stderr)
        return 4

    n = sum(len(data.get(k) or []) for k in
            ("ddls", "hfut", "competitions", "opportunities", "github", "baoyan"))
    if args.check:
        print(f"[template] OK | 条目 {n} 条 | 全部带链接")
        return 0

    if args.out:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[template] 已生成 {args.out} | {len(html)} 字符 | 条目 {n} 条")
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    sys.exit(main())
