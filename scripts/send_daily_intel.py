#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《每日情报》统一发信脚本（2026-09-18 固化，2026-09-23 接入固定模板）。

替代过去「每天现场生成一份 send_daily_intel_YYYY-MM-DD.py」的做法：
发信逻辑只此一份，日期 / 正文 / 收件人都由参数决定。

用法：
  python scripts/send_daily_intel.py                            # 发今天，正文取 temp_files/daily_intel_<今天>.html
  ★ python scripts/send_daily_intel.py --test                   # 测试模式：只发给 2240678683@qq.com（调试必用）
  python scripts/send_daily_intel.py --date 2026-09-18          # 指定日期（正文与标题都用这个日期）
  python scripts/send_daily_intel.py --html D:/x/y.html         # 指定正文文件
  ★ python scripts/send_daily_intel.py --from-json d.json --test # 用固定模板渲染 + 只发自己（推荐调试姿势）
  python scripts/send_daily_intel.py --to a@x.com,b@y.com       # 临时收件人（补发 / 定向测试）
  python scripts/send_daily_intel.py --dry-run                  # 只校验不发送，用于排查

### ⚠️ 收件人纪律（2026-09-23 用户要求，务必遵守）

- **任何调试 / 验证 / 预览 / 试跑，一律加 `--test`** —— 它把收件人强制收敛到
  `2240678683@qq.com`，与配置文件里的名单完全解耦，绝不会打扰其他 9 位收件人。
- **只有正式版才不加 `--test`**（此时读 `hfut_site/hfut_email_config.json` 的 `recipients`，
  以配置文件为准，人数会变，勿在代码/注释里写死）。
- `--test` 与 `--to` **互斥**：同时给会直接报错，避免"以为是测试、其实群发"。
- 起因：开发过程中曾用默认收件人验证，导致 10 位收件人收到了非正式邮件。

仅用标准库（smtplib + email），不依赖任何第三方包。

### 发送前的排版把关（2026-09-23 新增）

发送前会自动跑一遍 `daily_intel_template.lint_html()`，检测两类**用户明确抱怨过的**问题：
- DDL 区块里有条目**没有链接**（2026-09-23 用户反馈："ddl项目好像没有附带链接"）；
- 全文出现 `href="#"` 这类**点了没反应的占位链接**（同日实际发生：计算机学院两条通知的"链接"
  指向 `#`）。

默认只告警不拦截（避免自动任务因排版问题整封发不出去）；加 `--strict` 则直接拒绝发送。
"""

import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(ROOT, "hfut_site", "hfut_email_config.json")

# ── 测试收件人（2026-09-23 用户要求固化）──────────────────────────────
# 用户原话：「以后测试只给 2240678683 这个号发，正式版完成之后再发全部」
# 起因：开发过程中直接用默认收件人验证，导致 10 位收件人收到了非正式邮件。
# 约定：**任何调试 / 验证 / 预览 / 补发试跑一律加 `--test`**，
#       它会把收件人强制收敛到下面这一个，与配置文件里的名单完全解耦。
#       `--test` 与 `--to` 互斥（同时给会报错，避免"以为是测试、其实群发"）。
TEST_RECIPIENT = "2240678683@qq.com"


def _mask_email(addr: str) -> str:
    """脱敏邮箱（224****83@qq.com）——收件人属个人信息，日志一律脱敏。"""
    if not addr or "@" not in addr:
        return "***"
    name, domain = addr.split("@", 1)
    if len(name) <= 6:
        return f"{name[0]}****@{domain}"
    return f"{name[:3]}****{name[-2:]}@{domain}"


def _mask_emails(addrs) -> str:
    return "、".join(_mask_email(a) for a in addrs) if addrs else "（无）"

sys.path.insert(0, SCRIPT_DIR)
try:
    from daily_intel_template import lint_html, render as render_template, TemplateDataError
    _HAS_TEMPLATE = True
except ImportError:            # 模板模块缺失不应阻断发信
    _HAS_TEMPLATE = False

    def lint_html(_html):      # type: ignore[misc]
        return []



SEND_LOG_FILE = os.path.join(ROOT, "temp_files", "send_log.jsonl")


def _log_send(entry: dict) -> None:
    """每笔投递（含失败）追加到 temp_files/send_log.jsonl，事后可追溯重复邮件。"""
    try:
        os.makedirs(os.path.dirname(SEND_LOG_FILE), exist_ok=True)
        entry.setdefault("ts", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with open(SEND_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[send_daily_intel] ⚠ 写发送日志失败（不影响投递）: {e}")


def _full_send_already_ok(date: str) -> bool:
    """当天是否已有一条成功群发记录（防重复群发闸）。"""
    if not os.path.isfile(SEND_LOG_FILE):
        return False
    try:
        with open(SEND_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (rec.get("date") == date and rec.get("mode") == "full"
                        and rec.get("status") == "ok"):
                    return True
    except OSError:
        return False
    return False


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        sys.exit(f"[send_daily_intel] 邮件配置不存在: {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _smtp_send(sender: str, password: str, host: str, port: int,
               recipients: list, msg: MIMEMultipart) -> None:
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
        s.login(sender, password)
        s.sendmail(sender, recipients, msg.as_string())


def send(html: str, subject: str, recipients: list, config: dict,
         log_ctx: dict | None = None) -> str:
    """发送邮件；主账号失败时自动降级到配置里的 fallback 发件账号（2026-09-24 新增）。

    fallback 来自 hfut_email_config.json 的 `fallback` 键：
      {"sender": ..., "password": ..., "smtp_host": ..., "smtp_port": ...}
    触发条件：主账号抛 SMTPException / OSError（含 ConnectionResetError 10054 等频控断连）。
    注意：若主账号已部分投递后才失败，降级重发可能让个别收件人收到重复邮件 —— 宁重勿丢。
    返回实际使用的发件账号。log_ctx 会并入发送日志（2026-09-25 新增）。
    """
    sender = config.get("sender")
    password = config.get("password")
    host = config.get("smtp_host", "smtp.qq.com")
    port = int(config.get("smtp_port", 465))
    if not sender or not password:
        sys.exit("[send_daily_intel] 配置缺少 sender / password")

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(html, "html", "utf-8"))

    used = sender
    status = "ok"
    err = None
    try:
        _smtp_send(sender, password, host, port, recipients, msg)
    except (smtplib.SMTPException, OSError) as primary_err:
        fb = config.get("fallback") or {}
        fb_sender = fb.get("sender")
        fb_password = fb.get("password")
        if not (fb_sender and fb_password) or fb_sender == sender:
            used, status, err = sender, "failed", f"{type(primary_err).__name__}: {primary_err}"
            if log_ctx is not None:
                _log_send({**log_ctx, "sender": used, "status": status, "error": err})
            raise
        fb_host = fb.get("smtp_host", host)
        fb_port = int(fb.get("smtp_port", port))
        print(f"[send_daily_intel] ⚠ 主账号 {sender} 发送失败"
              f"（{type(primary_err).__name__}），切换备用发件账号 {fb_sender} 重试…")
        msg.replace_header("From", fb_sender)
        used = fb_sender
        try:
            _smtp_send(fb_sender, fb_password, fb_host, fb_port, recipients, msg)
        except (smtplib.SMTPException, OSError) as fb_err:
            used, status, err = fb_sender, "failed", f"{type(fb_err).__name__}: {fb_err}"
            if log_ctx is not None:
                _log_send({**log_ctx, "sender": used, "status": status, "error": err,
                           "primary_error": f"{type(primary_err).__name__}: {primary_err}"})
            raise
        print(f"[send_daily_intel] ⚠ 本封实际由备用账号 {fb_sender} 发出（主账号失败兜底）")
    if log_ctx is not None:
        _log_send({**log_ctx, "sender": used, "status": status})
    return used


def main() -> int:
    ap = argparse.ArgumentParser(description="《每日情报》统一发信脚本")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="情报日期，默认今天（同时决定标题与默认正文路径）")
    ap.add_argument("--html", default=None,
                    help="正文 HTML 路径，默认 temp_files/daily_intel_<日期>.html")
    ap.add_argument("--subject", default=None,
                    help="邮件标题，默认 [每日情报] <日期>")
    ap.add_argument("--to", default=None,
                    help="临时收件人，逗号分隔；缺省则读配置里的 recipients")
    ap.add_argument("--from-json", default=None, metavar="DATA_JSON",
                    help="用固定模板从结构化 JSON 渲染正文（推荐；自动落盘到 temp_files/）")
    ap.add_argument("--strict", action="store_true",
                    help="排版校验不通过时直接拒绝发送（默认只告警）")
    ap.add_argument("--test", action="store_true",
                    help=f"测试模式：只发给 {TEST_RECIPIENT}（调试/预览一律加这个，与 --to 互斥）")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不实际发送")
    ap.add_argument("--force", action="store_true",
                    help="当天已成功群发过仍强制重发（默认拒绝，防止重复邮件）")
    args = ap.parse_args()

    if args.test and args.to:
        sys.exit("[send_daily_intel] --test 与 --to 互斥：测试走 --test，定向补发走 --to，"
                 "两者同时给会把'以为是测试'变成'群发'。")

    html_path = args.html or os.path.join(ROOT, "temp_files", f"daily_intel_{args.date}.html")

    # ① 首选：从结构化数据用固定模板渲染（版式与链接由模板保证）
    if args.from_json:
        if not _HAS_TEMPLATE:
            sys.exit("[send_daily_intel] 缺少 daily_intel_template.py，无法用 --from-json")
        try:
            with open(args.from_json, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            sys.exit(f"[send_daily_intel] 数据文件不存在: {args.from_json}")
        except json.JSONDecodeError as e:
            sys.exit(f"[send_daily_intel] JSON 解析失败: {e}")
        data.setdefault("date", args.date)
        try:
            html = render_template(data)
        except TemplateDataError as e:
            sys.exit(f"[send_daily_intel] 模板校验失败，拒绝渲染: {e}")
        os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[send_daily_intel] 已用固定模板渲染 -> {html_path}")
    else:
        if not os.path.isfile(html_path):
            sys.exit(f"[send_daily_intel] 正文不存在: {html_path}")
        with open(html_path, encoding="utf-8") as f:
            html = f.read()

    # ② 排版把关：DDL 必须有链接、不得有 href="#" 占位
    problems = lint_html(html)
    if problems:
        print(f"[send_daily_intel] ⚠ 排版校验发现 {len(problems)} 处问题:")
        for p in problems[:10]:
            print(f"    - {p}")
        if len(problems) > 10:
            print(f"    … 另有 {len(problems) - 10} 处")
        if args.strict:
            sys.exit("[send_daily_intel] --strict 已开启，拒绝发送有问题的正文")
        print("[send_daily_intel] 默认只告警，继续发送（如需拦截请加 --strict）")
    else:
        print("[send_daily_intel] ✅ 排版校验通过（所有条目均带可点击链接）")

    config = load_config()
    if args.test:
        recipients = [TEST_RECIPIENT]
        how = "🧪 测试模式（只发你自己）"
    elif args.to:
        recipients = [x.strip() for x in args.to.split(",") if x.strip()]
        how = "🎯 定向补发（--to 指定）"
    else:
        recipients = config.get("recipients", [])
        how = "📢 正式群发（读配置收件人）"
    if not recipients:
        sys.exit("[send_daily_intel] 没有可用收件人")

    subject = args.subject or f"[每日情报] {args.date}"
    if args.test:
        subject = f"[测试] {subject}"

    print(f"[send_daily_intel] {how}")
    print(f"[send_daily_intel] 正文: {html_path} ({len(html)} 字符)")
    print(f"[send_daily_intel] 标题: {subject}")
    print(f"[send_daily_intel] 收件人({len(recipients)}): {_mask_emails(recipients)}")
    if how.startswith("📢") and len(recipients) > 1 and not args.dry_run:
        print(f"[send_daily_intel] ⚠ 这是群发，将真实投递给 {len(recipients)} 位收件人 —— "
              f"若只是想看看效果，请改用 --test")

    bad = [r for r in recipients if "@" not in r or "." not in r.split("@")[-1]]
    if bad:
        sys.exit(f"[send_daily_intel] 收件人格式可疑: {bad}")

    if args.dry_run:
        print("[send_daily_intel] DRY-RUN，未发送")
        return 0

    # ③ 防重复群发闸：当天已成功群发过就拒绝（2026-09-25 新增，起因：2240678683 一天收到 3 份）
    is_full = not args.test and not args.to
    log_ctx = {"date": args.date,
               "mode": "test" if args.test else ("to" if args.to else "full"),
               "recipient_count": len(recipients), "subject": subject}
    if is_full and not args.force and _full_send_already_ok(args.date):
        _log_send({**log_ctx, "sender": None, "status": "blocked-duplicate"})
        sys.exit(f"[send_daily_intel] 🛑 {args.date} 已有一条成功群发记录"
                 f"（见 temp_files/send_log.jsonl），拒绝重复群发。"
                 f"确要重发请加 --force。")

    try:
        used = send(html, subject, recipients, config, log_ctx=log_ctx)
    except Exception as e:
        print(f"[send_daily_intel] SENT FAILED | {type(e).__name__}: {e}")
        return 1
    print(f"[send_daily_intel] SENT ok | subject: {subject} | sender: {used} "
          f"| to({len(recipients)}): {_mask_emails(recipients)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
