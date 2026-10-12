#!/usr/bin/env python3
"""
hfut_cli.py - 合工大巡检系统 CLI 接口 v2.0
==========================================
专为自动化任务设计：极简调用，结构化输出，最大化节省 AI Token。
v2.0 新增：扫描完成后自动发送 HTML 邮件报告。

输出格式：纯文本 KEY: VALUE，AI 只需原样转发，无需处理。

用法：
  python hfut_cli.py scan              # 增量扫描 + 发送邮件（日常使用）
  python hfut_cli.py scan --full       # 全量扫描 + 发送邮件
  python hfut_cli.py scan --no-email   # 仅扫描，不发邮件
  python hfut_cli.py report            # 仅读取现有数据，生成摘要（0 网络请求）
  python hfut_cli.py status            # 查看系统状态（不扫描）
"""

import sys, io, os, json, subprocess, time
from datetime import datetime, timedelta

def _ensure_utf8_console():
    """把控制台编码调成 UTF-8 —— 但**不替换** sys.stdout/stderr 对象。

    原实现 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` 会劫持全局流：
    它把宿主（pytest / IDE / 任何 import 本模块的程序）的 stdout 换成包在其 buffer
    之上的新包装器；宿主随后关闭自己的捕获缓冲，本模块的包装器就指向已关闭的资源 →
    宿主进程抛 `ValueError: I/O operation on closed file`（pytest 下表现为收集阶段直接崩溃）。
    改用 reconfigure() 原地改编码，对象不变，对宿主零副作用。
    """
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass          # 宿主流无 reconfigure（如 StringIO）→ 保持原样即可


_ensure_utf8_console()

from hfut_config import KEYWORDS, PRIORITY_ORDER, classify_hfut

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PYTHON      = sys.executable
INDEX_FILE  = os.path.join(SCRIPT_DIR, "hfut_full_index.json")
STATE_FILE  = os.path.join(SCRIPT_DIR, "hfut_scan_state.json")
REPORT_HTML = os.path.join(SCRIPT_DIR, "hfut_report.html")
EMAIL_CFG   = os.path.join(SCRIPT_DIR, "hfut_email_config.json")


def load_data():
    if not os.path.exists(INDEX_FILE):
        return [], {}
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        index = json.load(f)
    state = {}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    return index, state


def run_scanner(full=False):
    """运行扫描器，返回 (success, output_lines)"""
    args = [PYTHON, os.path.join(SCRIPT_DIR, "hfut_scanner.py"), "--pages", "3"]
    if full:
        args.append("--full")
    t0 = time.time()
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        lines = (r.stdout + r.stderr).strip().split("\n")
        return r.returncode == 0, lines
    except subprocess.TimeoutExpired:
        waited = int(time.time() - t0)
        return False, [f"[超时] 扫描已超过180秒（已等待约{waited}s，正常应为25~70s），可能已卡死"]
    except Exception as e:
        return False, [f"[异常] {e}"]


def run_report_gen():
    """生成 HTML 报告，返回是否成功（不再吞掉异常）"""
    try:
        r = subprocess.run(
            [PYTHON, os.path.join(SCRIPT_DIR, "hfut_report_gen.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        if r.returncode != 0:
            err = (r.stdout + r.stderr).strip()
            print("[hfut_cli] ❌ 报告生成失败（退出码非0）:")
            for line in err.split("\n")[-10:]:
                if line.strip():
                    print(f"           {line}")
            return False
        return True
    except Exception as e:
        print(f"[hfut_cli] ❌ 报告生成异常: {e}")
        return False


def send_email_report(index, state, new_count: int):
    """调用 hfut_email.py 发送邮件"""
    if not os.path.exists(EMAIL_CFG):
        print("[hfut_cli] ⚠ 邮件配置未找到，跳过发送")
        print(f"          配置文件路径: {EMAIL_CFG}")
        print("          运行 python hfut_email.py --setup 完成配置")
        return False

    email_script = os.path.join(SCRIPT_DIR, "hfut_email.py")
    try:
        r = subprocess.run(
            [PYTHON, email_script, f"--new={new_count}"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=90
        )
        output = (r.stdout + r.stderr).strip()
        for line in output.split("\n"):
            if line.strip():
                print(f"  [邮件] {line}")
        if r.returncode != 0:
            print(f"  [邮件] ❌ 邮件进程退出码 {r.returncode}")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print("  [邮件] ❌ 发送超时（>90s），QQ SSL 465 登录/投递较慢，可重试")
        return False
    except Exception as e:
        print(f"  [邮件] ❌ 发送异常: {e}")
        return False


def build_summary(index, state, new_count=None) -> str:
    """构建结构化纯文本摘要（极致精简，供 AI 直接转发）"""
    now      = datetime.now()
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    today    = now.strftime("%Y-%m-%d")

    total           = state.get("total_items", len(index))
    channel_results = state.get("channel_results", {})
    ok_channels     = [k for k, v in channel_results.items() if v > 0]
    # 2026-09-22 修正：失败栏目以 channel_results 为准（state.failed_channels 可能缺字段）。
    # 原写法分母 = 成功数 + 失败数 且失败数取自 state，字段缺失时退化为
    # 「33/33」这种"全部覆盖"的假象。
    # B1 修复：优先采信 state.failed_channels（扫描器已排除主动跳过的浏览器栏目）。
    # 仅当老 state 文件缺失该字段时，才从 channel_results 回退重算，
    # 且必须减去 browser_skipped，避免把主动关闭的栏目误报成"未取到"。
    if "failed_channels" in state:
        fail_channels = state.get("failed_channels", []) or []
    else:
        skip_set = set(state.get("browser_skipped", []) or [])
        fail_channels = [k for k, v in channel_results.items()
                         if v == 0 and k not in skip_set]
    browser_skipped = state.get("browser_skipped", []) or []
    last_scan       = state.get("last_scan_time", "未知")

    today_items = [i for i in index if i.get("date", "") == today]
    if new_count is None:
        new_count = len(today_items)

    # 高相关度（近7天）
    recent  = [i for i in index if i.get("date", "") >= week_ago]
    flagged = []
    # 2026-09-22：把「就业」也纳入高相关度。此前只认 保研/竞赛/教务/奖学金，
    # 而用户明确关注就业（招聘/宣讲会/实习），且就业类通知往往有明确截止时间。
    FLAGGED_CATS = ("保研", "竞赛", "教务", "奖学金", "就业")
    for item in sorted(recent, key=lambda x: (x.get("score", 0), x.get("date", "")), reverse=True):
        cat = classify_hfut(item.get("title", ""))
        if cat in FLAGGED_CATS:
            flagged.append((cat, item))
        elif item.get("score", 0) >= 10:
            flagged.append(("重要", item))

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📋 合工大巡检报告 {today}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"总索引: {total} 条")
    lines.append(f"今日新增: {new_count} 条")
    lines.append(f"覆盖栏目: {len(ok_channels)}/{len(channel_results) or len(ok_channels)}")
    lines.append(f"上次扫描: {last_scan}")

    if fail_channels:
        lines.append(f"⚠ 未取到栏目({len(fail_channels)}): {', '.join(fail_channels)}")
    if browser_skipped:
        lines.append(f"⏭ 本次跳过栏目({len(browser_skipped)}): {', '.join(browser_skipped)}（HFUT_ENABLE_BROWSER_CHANNELS=0）")

    if flagged:
        lines.append("")
        lines.append(f"🔔 关键通知（近7天，共 {len(flagged)} 条，按栏目分组）:")
        # 2026-09-22 修正：原实现 sorted 后直接取 flagged[:10]，24 条只吐出 10 条，
        # 且不按栏目分组 → 下游只能写到基金/实验室安全这类高分类目，
        # 学院（电气/机械/计算机）、教务部、研究生院等栏目全被压缩掉。
        by_channel = {}
        for cat, item in flagged:
            by_channel.setdefault(item.get("channel", "未知栏目"), []).append((cat, item))
        for ch, rows in sorted(by_channel.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines.append(f"  ▎{ch}（{len(rows)}）")
            # B2 修复：不再静默截断。单栏目超过 15 条时显式补一行提示，
            # 让下游 AI 知道还有同类通知未被列出（避免"官网没有信息"反馈复发）。
            shown = rows[:15]
            for cat, item in shown:
                lines.append(f"    [{cat}] {item.get('date','')} {item.get('title','')}")
                lines.append(f"           {item.get('url','')}")
            if len(rows) > len(shown):
                lines.append(f"    ... 另有 {len(rows)-len(shown)} 条同类通知未列出（可用 hfut.py kb <关键词> 查询）")
    else:
        lines.append("")
        lines.append("✅ 近7天无保研/竞赛/教务/奖学金相关通知")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 报告: {REPORT_HTML}")
    return "\n".join(lines)


# ── 知识库子命令 ─────────────────────────────────────────────

def cmd_kb(query: str, args: list):
    """知识库查询：先报本地索引检索结果，末尾再补 IMA 知识库提示"""
    print(f"[hfut_cli] 🔍 知识库查询: {query}")
    # 先执行本地索引检索（实际可查到结果，避免用户误以为查询没生效）
    index, _ = load_data()
    if index:
        matches = [i for i in index if query.lower() in i.get("title", "").lower()
                   or query.lower() in i.get("channel", "").lower()]
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        print(f"\n   📊 本地数据匹配 ({len(matches)} 条):")
        for item in matches[:10]:
            cat = classify_hfut(item.get("title", ""))
            icon = "🔔" if cat else "📄"
            print(f"   {icon} [{item.get('date','')}] {item.get('title','')[:60]}")
            print(f"      {item.get('url','')}")
    else:
        print("   ⚠ 本地暂无索引数据，请先运行 scan 生成索引")
    # 末尾再补一句：如需更全的历史检索可用 IMA 知识库
    print(f"\n   💡 如需更全的历史检索，可用 IMA 知识库「合工大官网信息库」（ID: 7460513151727999）")


# ── 子命令 ──────────────────────────────────────────────────

def cmd_scan(full=False, send_email=True):
    t0   = time.time()
    mode = "全量" if full else "增量"
    print(f"[hfut_cli] 开始{mode}扫描 {datetime.now().strftime('%H:%M:%S')}")

    index_before, _ = load_data()
    count_before    = len(index_before)

    ok, scan_lines = run_scanner(full=full)
    for line in scan_lines[-20:]:
        print(f"  {line}")
    if not ok:
        print("[hfut_cli] ⚠ 扫描器返回异常，后续报告/邮件可能基于旧数据")

    if not run_report_gen():
        print("[hfut_cli] ⚠ 报告生成失败，hfut_report.html 可能停留在旧版本")

    index, state = load_data()
    new_count    = len(index) - count_before

    summary = build_summary(index, state, new_count=new_count)
    print()
    print(summary)
    print(f"\n[hfut_cli] 完成，耗时 {time.time()-t0:.1f}s")

    # 发送邮件
    if send_email:
        print()
        print("[hfut_cli] 📧 发送邮件报告...")
        email_ok = send_email_report(index, state, new_count)
        if email_ok:
            _rcpts = []
            try:
                with open(os.path.join(SCRIPT_DIR, "hfut_email_config.json"), encoding="utf-8") as f:
                    _rcpts = json.load(f).get("recipients", [])
            except Exception:
                pass
            print(f"[hfut_cli] 📧 邮件已推送至 {', '.join(_rcpts) or '(见邮件配置)'}")


def cmd_report():
    """仅读取现有数据生成摘要，不扫描（0 网络请求）"""
    index, state = load_data()
    if not index:
        print("[hfut_cli] ⚠ 无数据，请先运行: python hfut_cli.py scan")
        return
    if not run_report_gen():
        print("[hfut_cli] ⚠ 报告生成失败，hfut_report.html 可能停留在旧版本")
    print(build_summary(index, state))


def cmd_status():
    """查看系统状态"""
    index, state = load_data()
    email_cfg_ok = os.path.exists(EMAIL_CFG)
    print("[hfut_cli] 系统状态")
    print(f"  索引文件:   {'存在' if os.path.exists(INDEX_FILE) else '不存在'}")
    print(f"  总条目数:   {len(index)}")
    print(f"  上次扫描:   {state.get('last_scan_time', '从未')}")
    print(f"  Playwright: ", end="")
    try:
        import playwright  # noqa
        print("已安装 ✅")
    except ImportError:
        print("未安装 ⚠")
    print(f"  邮件配置:   {'已配置 ✅' if email_cfg_ok else '未配置 ⚠ (运行 python hfut_email.py --setup)'}")


# ── 入口 ────────────────────────────────────────────────────

def main():
    # B4 前置检查：若用系统 Python 运行，子进程也会沿用它，易以"缺 requests/bs4"
    # 的子进程失败形式出现，排查困难。仅 warn，不 exit，保持兼容。
    exe = sys.executable or ""
    if ".venv" not in exe:
        print(f"[hfut_cli] ⚠ 当前 Python 不是项目 venv：{exe}")
        print("          子进程将沿用同一解释器，若缺 requests/bs4 会以子进程失败形式报错。")
        print("          建议改用：.venv/Scripts/python.exe hfut.py <命令>")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0].lower()
    if cmd == "scan":
        cmd_scan(full="--full" in args, send_email="--no-email" not in args)
    elif cmd == "report":
        cmd_report()
    elif cmd == "status":
        cmd_status()
    elif cmd == "kb":
        if len(args) < 2:
            print("[hfut_cli] 用法: python hfut_cli.py kb <关键词> [选项]")
            print("          选项: --category <类别> --channel <栏目> --days <N> --limit <N> --stats --recent <N>")
            return
        cmd_kb(args[1], args)
    else:
        print(f"[hfut_cli] 未知命令: {cmd}")
        print("可用命令: scan | scan --full | scan --no-email | report | status")


if __name__ == "__main__":
    main()
