#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P8 素材 · 高校「舆情 + 网站巡检」中标金额横向条形图生成器（可复现）

## 数据来源（唯一真源，不杜撰）

`docs/商业化可行性调研报告-2026-09-17.html` →「B 端补充：钱最多，但你暂时够不着」一节。

该节原文：

> 已核实的高校"舆情 + 网站/新媒体巡检"采购预算（2026 年真实中标）：
> 德州学院 ¥127,500、西南科技大学 ¥86,600/年、广东药科大学 ¥69,800、
> 桂林学院 ¥45,000、凯里学院 ¥45,000、佛山职业技术学院 ¥38,200、
> 辽宁建筑职业学院 ¥36,000、河南工学院 ¥99,000、
> 云南国土资源职业学院 ¥100,000/年（一次采购 3 年）、宜昌科技职院 ¥24,000。

本脚本用正则从上面这个 HTML 里**把这 10 条重新解析出来**，
解析不到 10 条就报错退出（宁可不出图，也不出错图）。

⚠️ 注意：`docs/招标采购调研-2026-09-22.md` 里**没有**这 10 条记录，
它只有月饼采购中标与 50 条无金额的公告列表。别引错文件。

## 用法

```bash
cd D:/项目整理/01-代码项目/hfut_info_monitor
PY="C:/Users/Matebook/AppData/Local/Python/pythoncore-3.14-64/python.exe"
PLAYWRIGHT_BROWSERS_PATH=D:/playwright-browsers "$PY" \
  "docs/参赛材料/路演素材/make_p8_bid_price.py"
```

产物：
- `P8_高校巡检中标价_20261012.html`  图的 HTML 源（改配色/文案后重跑即可）
- `P8_高校巡检中标价_20261012.png`   横向条形图，浅色风格
"""

from __future__ import annotations

import argparse
import html as _html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

SRC_MD_HTML = os.path.join(PROJ, "docs", "商业化可行性调研报告-2026-09-17.html")
OUT_HTML = os.path.join(HERE, "P8_高校巡检中标价_20261012.html")
OUT_PNG = os.path.join(HERE, "P8_高校巡检中标价_20261012.png")

# 原文去标签后长这样：
#   「…真实中标）：德州学院 ¥127,500、西南科技大学 ¥86,600/年、广东药科大学 ¥69,800、…、云南国土资源职业学院 ¥100,000/年（一次采购 3 年）、宜昌科技职院 ¥24,000。」
# 金额裹在 <b> 里，所以先 strip 标签再匹配；分隔符是「、」不是「|」。
_RE_REC = re.compile(
    r"(?P<name>[一-龥A-Za-z0-9（）()·]{2,20}?"
    r"(?:职业技术学院|职业学院|大学|学院|学校|职院))"
    r"\s*[¥￥]\s*(?P<amt>[\d,]+)\s*(?P<per>/\s*年)?"
    r"\s*(?P<note>（[^）]{0,20}）)?"
)


def parse_records(src: str) -> list[dict]:
    """从调研报告 HTML 里解析出中标记录。返回按金额倒序的列表。"""
    raw = open(src, encoding="utf-8").read()
    i = raw.find("已核实的高校")
    if i < 0:
        raise SystemExit("[p8] 定位失败：原文里找不到「已核实的高校」")
    seg = re.sub(r"<[^>]+>", "", raw[i:i + 700])

    rows = []
    for m in _RE_REC.finditer(seg):
        note = (m.group("note") or "").strip("（）")
        rows.append({
            "name": m.group("name"),
            "amount": int(m.group("amt").replace(",", "")),
            "annual": bool(m.group("per")),
            "note": note,
        })
    if len(rows) != 10:
        raise SystemExit(f"[p8] 解析到 {len(rows)} 条，不是预期的 10 条，已中止出图")
    return sorted(rows, key=lambda r: r["amount"])


def build_html(rows: list[dict]) -> str:
    amts = [r["amount"] for r in rows]
    lo, hi, n = min(amts), max(amts), len(rows)
    mean = sum(amts) / n

    # 条长按对数压缩：否则 24,000 的条几乎看不见（127,500/24,000 ≈ 5.3 倍）
    import math
    scale = lambda v: 100 * (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))

    bars = []
    for r in rows:
        w = scale(r["amount"])
        per = '<span class="per">/年</span>' if r["annual"] else ""
        note = f'<span class="note">{_html.escape(r["note"])}</span>' if r["note"] else ""
        bars.append(
            f'<div class="row">'
            f'<div class="lab">{_html.escape(r["name"])}{note}</div>'
            f'<div class="track"><div class="fill" style="width:{max(w, 2.2):.1f}%"></div>'
            f'<div class="val">¥{r["amount"]:,}{per}</div></div>'
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>高校巡检舆情服务中标价</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#ffffff;
          font-family:"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
          color:#1f2937; padding:34px 38px 26px; width:1240px; }}
  .hd {{ border-left:5px solid #2563eb; padding:2px 0 2px 16px; margin-bottom:20px; }}
  .hd h1 {{ margin:0; font-size:29px; font-weight:700; color:#0f172a; letter-spacing:.01em; }}
  .hd p  {{ margin:7px 0 0; font-size:14.5px; color:#64748b; line-height:1.6; }}

  .kpi {{ display:flex; gap:14px; margin:20px 0 22px; }}
  .kpi .k {{ flex:1; background:#f6f9fd; border:1px solid #e3ebf5; border-radius:10px;
             padding:13px 16px; }}
  .kpi .k .n {{ font-size:27px; font-weight:700; color:#1d4ed8; line-height:1.15; }}
  .kpi .k .n small {{ font-size:15px; font-weight:600; color:#475569; margin-left:2px; }}
  .kpi .k .l {{ font-size:13px; color:#64748b; margin-top:3px; }}

  .chart {{ border-top:1px solid #eef2f7; padding-top:18px; }}
  .row {{ display:flex; align-items:center; margin-bottom:11px; }}
  .lab {{ width:250px; flex:0 0 250px; font-size:15px; font-weight:600; color:#111827;
          text-align:right; padding-right:14px; line-height:1.35; }}
  .lab .note {{ display:block; font-size:11.5px; font-weight:400; color:#94a3b8;
                margin-top:1px; }}
  .track {{ flex:1; position:relative; height:30px; display:flex; align-items:center; }}
  .fill {{ height:21px; border-radius:0 5px 5px 0;
           background:linear-gradient(90deg,#93c5fd,#2563eb); }}
  /* 行按金额升序排列，所以"最大的一笔"是最后一行 —— 用 nth-last-child 取倒数第 1、2 名做高亮，
     别用 first-child / nth-child(2)（那会正好把最小的两笔标成重点）。
     注意：这段 CSS 在 f-string 里，大括号要写成两个。 */
  .row:nth-last-child(1) .fill {{ background:linear-gradient(90deg,#fcd34d,#f59e0b); }}
  .row:nth-last-child(2) .fill {{ background:linear-gradient(90deg,#fdba74,#ea580c); }}
  .val {{ font-size:14.5px; font-weight:700; color:#1d4ed8; margin-left:11px;
          white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .val .per {{ font-size:12px; font-weight:600; color:#64748b; }}
  .axis {{ margin-left:250px; margin-top:2px; border-top:1px dashed #dbe3ee; padding-top:5px;
           font-size:11.5px; color:#94a3b8; display:flex; justify-content:space-between; }}

  .ft {{ margin-top:20px; padding-top:14px; border-top:1px solid #eef2f7;
         font-size:12.5px; color:#64748b; line-height:1.85; }}
  .ft b {{ color:#b45309; font-weight:600; }}
  .src {{ color:#94a3b8; font-size:11.5px; margin-top:6px; }}
</style></head>
<body>
  <div class="hd">
    <h1>B 端真实预算：高校「舆情 + 网站巡检」已中标项目（2026 年）</h1>
    <p>不是市场规模测算，是<b>已经在公开招标中成交的金额</b>。采购内容明确包含
       "校园新媒体、网站信息校验""错别字敏感词检查"——与本项目能力范围高度重合。</p>
  </div>

  <div class="kpi">
    <div class="k"><div class="n">{n}<small> 条</small></div><div class="l">已核实中标样本数</div></div>
    <div class="k"><div class="n">¥{lo / 10000:.1f}<small>–{hi / 10000:.2f} 万/年</small></div>
      <div class="l">单校年化预算区间</div></div>
    <div class="k"><div class="n">¥{mean / 10000:.2f}<small> 万</small></div>
      <div class="l">样本均值</div></div>
    <div class="k"><div class="n">5.3<small>×</small></div>
      <div class="l">最高 ÷ 最低客单价倍数</div></div>
  </div>

  <div class="chart">{"".join(bars)}</div>
  <div class="axis"><span>¥{lo:,}</span><span>条长按对数标度绘制（线性标度下最低项会压成一条线）</span>
    <span>¥{hi:,}</span></div>

  <div class="ft">
    <b>为什么这条线成立：</b>标 "／年" 的两项为年度预算；云南国土资源职业学院为一次采购 3 年。
    该价位远高于 C 端订阅（¥99–199/年），但招标要求独立法人资格、第三方审计财报与社保信用记录，
    <b>在校学生身份无法直接投标</b> —— 故本项目将其定位为 2–3 年后的目标，而非当前路径。
    <div class="src">数据来源：docs/商业化可行性调研报告-2026-09-17.html「B 端补充」一节原文所载 10 条中标记录，
      本图由 make_p8_bid_price.py 从该文件正则解析生成，未做任何估算或补值。</div>
  </div>
</body></html>
"""


def png_size(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(24)
    return f"{int.from_bytes(head[16:20], 'big')}×{int.from_bytes(head[20:24], 'big')}"


def main() -> int:
    ap = argparse.ArgumentParser(description="P8 中标金额条形图生成器")
    ap.add_argument("--print-data", action="store_true", help="只打印解析到的 10 条记录")
    args = ap.parse_args()

    if not os.path.exists(SRC_MD_HTML):
        print(f"[p8] 数据源不存在 {SRC_MD_HTML}", file=sys.stderr)
        return 2

    rows = parse_records(SRC_MD_HTML)
    if args.print_data:
        for i, r in enumerate(rows, 1):
            print(f"  {i:2d}. {r['name']:<14} ¥{r['amount']:>7,}"
                  f"{' /年' if r['annual'] else '     '} {r['note']}")
        return 0

    amts = [r["amount"] for r in rows]
    print(f"[p8] 解析 {len(rows)} 条真实中标记录 | 区间 ¥{min(amts):,}–¥{max(amts):,}")

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print(f"[p8] 已生成 {os.path.basename(OUT_HTML)}")

    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\playwright-browsers")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1240, "height": 900}, device_scale_factor=2)
        pg.goto("file:///" + OUT_HTML.replace("\\", "/"))
        pg.wait_for_timeout(400)
        pg.locator("body").screenshot(path=OUT_PNG)
        b.close()
    print(f"[p8] 已生成 {os.path.basename(OUT_PNG)}  {png_size(OUT_PNG)}  "
          f"{os.path.getsize(OUT_PNG) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
