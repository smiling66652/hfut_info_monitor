#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P4 素材 ·《每日情报》邮件样例图生成器（可复现）

## 它到底截的是什么

截的是**真实已发送邮件的正文存档**，不是"照着样子重画一张"。

- 源文件：`temp_files/daily_intel_2026-10-12.html`
  由 `scripts/send_daily_intel.py` 在 2026-10-12 21:53 落盘，
  正文经 `scripts/daily_intel_template.py` 的固定模板渲染
  （同一份 `render()` + `lint_html()`，与线上发信走同一条代码路径）。
- 本脚本只做一件事：把那份 HTML 用 Chromium 渲染并截图。
  **不改动邮件里的任何一个字**，所以图上每一条都是真跑出来的。

真实性自证（可用 `--verify` 复跑）：
邮件里 16 条官网条目的 URL，去重后 16 条**全部**能在扫描归档里查到
（`hfut_site/hfut_latest_notices.json` 或 `data/hfut_archive.json`）。

## 用法

```bash
cd D:/项目整理/01-代码项目/hfut_info_monitor
PY="C:/Users/Matebook/AppData/Local/Python/pythoncore-3.14-64/python.exe"
PLAYWRIGHT_BROWSERS_PATH=D:/playwright-browsers "$PY" \
  "docs/参赛材料/路演素材/make_p4_intel_sample.py"
```

产物（都写在同目录）：
- `P4_每日情报样例_20261012.html`  邮件正文副本（与 temp_files 那份逐字节一致）
- `P4_每日情报样例_20261012.png`   主图：页头统计 + DDL 提醒 + 官网条目（投影用）
- `P4_每日情报样例_全页_20261012.png` 全页存档（存档/附录用，不建议塞进 PPT）

## 为什么要 zoom

邮件正文字号是 13–14px，那是**读邮件**的尺寸；直接塞进路演 PPT 会糊成一片。
这里用 `zoom` 把整封邮件等比放大后再 2 倍像素密度截图，
让"每条信息都带原文链接"这个卖点在小图里也能看清。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

SRC_HTML = os.path.join(PROJ, "temp_files", "daily_intel_2026-10-12.html")
OUT_HTML = os.path.join(HERE, "P4_每日情报样例_20261012.html")
OUT_MAIN = os.path.join(HERE, "P4_每日情报样例_20261012.png")
OUT_FULL = os.path.join(HERE, "P4_每日情报样例_全页_20261012.png")

# 邮件正文 .container 宽 720px；乘 ZOOM 得到实际排版宽度
ZOOM = 1.45
VIEWPORT_W = int(720 * ZOOM) + 40
DPR = 2

# 主图只保留"页头统计 + DDL 提醒 + 前 N 条官网条目"，其余栏目隐藏（由浏览器算高度，见 shoot()）
# 注意：第 1 个 .section 就是合工大官网（.ddl-wrap 不是 .section）。
MAIN_ITEMS = 6

# 条数标注：让"图上 6 条 / 页头 16 条"这件事在图上自曝，而不是等评委自己数出来。
# 数字全部由脚本从真实邮件里数出来后填进文案，**不写死**，邮件变了文案自动跟着变。
NOTE_CSS = """
  .asset-note { display:block; margin:0 0 10px; padding:10px 14px;
                background:#fffbeb; border:1px dashed #f59e0b;
                border-left:4px solid #f59e0b; border-radius:0 8px 8px 0;
                color:#92400e; font-size:14px; line-height:1.6; }
  .asset-note b { color:#b45309; font-weight:700; }
  .asset-note .tag { display:inline-block; font-size:11.5px; font-weight:700;
                      background:#fde68a; color:#92400e; border-radius:4px;
                      padding:1px 7px; margin-right:8px; letter-spacing:.02em; }
"""


def build_note(total: int, shown: int) -> str:
    """生成标注文案。total = 邮件真实条数，shown = 图上画出的条数。"""
    rest = total - shown
    return (
        '<span class="tag">图示说明</span>'
        f'本封情报共收录 <b>{total} 条</b>合工大官网通知，'
        f'受投影可读性限制，此处图示最新 <b>{shown} 条</b>，'
        f'其余 <b>{rest} 条</b>为同一格式的同类通知（完整版见全页存档图，可当场展开）。'
    )


def verify(src_html: str) -> dict:
    """把邮件里每条官网条目的 URL 拿去扫描归档里对账，返回对账结果。"""
    html = open(src_html, encoding="utf-8").read()
    urls = list(dict.fromkeys(re.findall(r'class="item-title"><a href="(.*?)"', html)))
    known = set()
    notices = json.load(
        open(os.path.join(PROJ, "hfut_site", "hfut_latest_notices.json"), encoding="utf-8")
    )
    known |= {x["url"] for x in notices}
    with open(os.path.join(PROJ, "data", "hfut_archive.json"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                known.add(json.loads(line)["url"])

    unescaped = [u.replace("&amp;", "&") for u in urls]
    hit = [u for u in unescaped if u in known]
    miss = [u for u in unescaped if u not in known]
    return {
        "官网条目 URL 数": len(urls),
        "命中扫描归档": len(hit),
        "未命中": miss,
        "DDL 条数": len(re.findall(r'class="ddl-deadline"', html)),
        "赛事条目数": len(re.findall(r'class="comp ', html)),
        "成长机会条目数": len(re.findall(r'class="opp"', html)),
    }


def shoot(zoom: float, dpr: int, total_items: int) -> None:
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\playwright-browsers")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": VIEWPORT_W, "height": 1400},
            device_scale_factor=dpr,
        )
        page.goto("file:///" + OUT_HTML.replace("\\", "/"))
        # 只放大排版，不改一个字；顺便把折叠的 details 展开
        page.add_style_tag(content=f"body{{zoom:{zoom};}} details{{display:block;}}")

        # 「共 16 条 · 图示最新 6 条」标注。
        # 为什么必须有：页头统计写 16，图上只画 6 条，评委一数就发现对不上。
        # 主动交代 = 可控，被评委自己发现 = 扣分。
        # 关键纪律：这段只在**截图时**注入到 DOM，写回磁盘的
        # P4_每日情报样例_20261012.html 仍与真实邮件逐字节一致（cmp 可验）。
        # 全页存档图里 16 条全都在，所以**不能**带这条标注 —— 只给主图加。
        page.add_style_tag(content=NOTE_CSS)

        # ── 全页存档：恢复全部条目，这张图 16 条全在，**不要**加"图示 6 条"标注 ──
        page.evaluate(
            """() => {
                document.querySelectorAll('.section, .item').forEach(el => {
                    el.style.display = '';
                });
            }"""
        )
        page.wait_for_timeout(300)
        page.screenshot(path=OUT_FULL, full_page=True)

        # ── 主图：隐藏其余栏目 + 第 N 条起的官网条目，再插入条数标注 ──
        page.evaluate(
            """(n) => {
                document.querySelectorAll('.section').forEach((s, i) => {
                    if (i > 0) s.style.display = 'none';   // 只留第 1 个 section（合工大官网）
                });
                document.querySelectorAll('.section .content .item').forEach((el, i) => {
                    if (i >= n) el.style.display = 'none';   // 官网条目只留前 n 条
                });
            }""",
            MAIN_ITEMS,
        )
        page.evaluate(
            """(txt) => {
                const sec = document.querySelectorAll('.section')[0];
                if (!sec) return;
                const badge = document.createElement('div');
                badge.className = 'asset-note';
                badge.innerHTML = txt;
                const content = sec.querySelector('.content');
                if (content) content.parentNode.insertBefore(badge, content);
                else sec.appendChild(badge);
            }""",
            build_note(total_items, MAIN_ITEMS),
        )
        page.wait_for_timeout(400)
        page.screenshot(path=OUT_MAIN, full_page=True)
        browser.close()

    for f in (OUT_MAIN, OUT_FULL):
        print(f"[p4] {os.path.basename(f)}  {png_size(f)}  "
              f"{os.path.getsize(f) / 1024:.0f} KB")


def png_size(path: str) -> str:
    """不引第三方库，直接读 PNG 头 IHDR 的宽高。"""
    with open(path, "rb") as f:
        head = f.read(24)
    w = int.from_bytes(head[16:20], "big")
    h = int.from_bytes(head[20:24], "big")
    return f"{w}×{h}"


def main() -> int:
    ap = argparse.ArgumentParser(description="P4 每日情报样例图生成器")
    ap.add_argument("--verify", action="store_true", help="只跑真实性对账，不出图")
    args = ap.parse_args()

    if not os.path.exists(SRC_HTML):
        print(f"[p4] 找不到源邮件 {SRC_HTML}", file=sys.stderr)
        return 2

    if args.verify:
        r = verify(SRC_HTML)
        for k, v in r.items():
            print(f"  {k}: {v}")
        return 0 if not r["未命中"] else 1

    shutil.copyfile(SRC_HTML, OUT_HTML)
    print(f"[p4] 已复制邮件正文 -> {os.path.basename(OUT_HTML)}")

    r = verify(SRC_HTML)
    if r["未命中"]:
        print(f"[p4] ⚠ 有 {len(r['未命中'])} 条 URL 无法在扫描归档中核对，仍继续出图：")
        for u in r["未命中"]:
            print("      ", u)
    else:
        print(f"[p4] ✅ 真实性对账通过：{r['命中扫描归档']}/{r['官网条目 URL 数']} "
              f"条官网 URL 均可在扫描归档中查到")

    total = r["官网条目 URL 数"]
    if total <= MAIN_ITEMS:
        print(f"[p4] 邮件官网条目只有 {total} 条，不足图示 {MAIN_ITEMS} 条，"
              f"标注无意义，跳过标注", file=sys.stderr)
    shoot(ZOOM, DPR, total)
    print(f"[p4] 完成 | 主图标注：共 {total} 条，图示 {MAIN_ITEMS} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
