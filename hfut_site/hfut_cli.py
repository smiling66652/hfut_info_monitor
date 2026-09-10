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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=360)
        lines = (r.stdout + r.stderr).strip().split("\n")
        return r.returncode == 0, lines
    except subprocess.TimeoutExpired:
        return False, ["[超时] 扫描超过360秒"]
    except Exception as e:
        return False, [f"[异常] {e}"]


def run_report_gen():
    """生成 HTML 报告"""
    try:
        r = subprocess.run(
            [PYTHON, os.path.join(SCRIPT_DIR, "hfut_report_gen.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
        return r.returncode == 0
    except Exception:
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
            encoding="utf-8", errors="replace", timeout=30
        )
        output = (r.stdout + r.stderr).strip()
        for line in output.split("\n"):
            print(f"  [邮件] {line}")
        return r.returncode == 0
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
    fail_channels   = state.get("failed_channels", [])
    last_scan       = state.get("last_scan_time", "未知")

    today_items = [i for i in index if i.get("date", "") == today]
    if new_count is None:
        new_count = len(today_items)

    # 高相关度（近7天）
    recent  = [i for i in index if i.get("date", "") >= week_ago]
    flagged = []
    for item in sorted(recent, key=lambda x: (x.get("score", 0), x.get("date", "")), reverse=True):
        cat = classify_hfut(item.get("title", ""))
        if cat in ("保研", "竞赛", "教务", "奖学金"):
            flagged.append((cat, item))
        elif item.get("score", 0) >= 10:
            flagged.append(("重要", item))

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📋 合工大巡检报告 {today}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"总索引: {total} 条")
    lines.append(f"今日新增: {new_count} 条")
    lines.append(f"覆盖栏目: {len(ok_channels)}/{len(ok_channels)+len(fail_channels)}")
    lines.append(f"上次扫描: {last_scan}")

    if fail_channels:
        lines.append(f"⚠ 失败栏目: {', '.join(fail_channels)}")

    if flagged:
        lines.append("")
        lines.append(f"🔔 关键通知（近7天，共 {len(flagged)} 条）:")
        for cat, item in flagged[:10]:
            lines.append(f"  [{cat}] {item.get('date','')} {item.get('title','')}")
            lines.append(f"         {item.get('url','')}")
    else:
        lines.append("")
        lines.append("✅ 近7天无保研/竞赛/教务/奖学金相关通知")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 报告: {REPORT_HTML}")
    return "\n".join(lines)


# ── 知识库子命令 ─────────────────────────────────────────────

def cmd_kb(query: str, args: list):
    """知识库查询：引导用户使用 IMA 知识库"""
    print(f"[hfut_cli] 🔍 知识库查询: {query}")
    print(f"   💡 提示：请使用 IMA 知识库「合工大官网信息库」进行检索")
    print(f"   🔗 知识库ID: 7460513151727999")
    print(f"   在 IMA 中搜索以上关键词即可获取历史巡检记录。")
    # 也尝试用本地数据搜索
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

    run_report_gen()

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
            print("[hfut_cli] 📧 邮件已推送至 2240678683@qq.com")


def cmd_report():
    """仅读取现有数据生成摘要，不扫描（0 网络请求）"""
    index, state = load_data()
    if not index:
        print("[hfut_cli] ⚠ 无数据，请先运行: python hfut_cli.py scan")
        return
    run_report_gen()
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
