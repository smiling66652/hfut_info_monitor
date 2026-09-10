#!/usr/bin/env python3
"""
hfut_email.py - 合工大巡检报告邮件推送 v1.0
============================================
用标准库 smtplib 发送 HTML 格式巡检报告邮件，零额外依赖。

配置文件: hfut_email_config.json
  {
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "sender": "你的QQ邮箱@qq.com",
    "password": "你的邮箱授权码（非登录密码）",
    "recipients": ["2240678683@qq.com"]
  }

用法:
  python hfut_email.py                    # 发送今日巡检报告
  python hfut_email.py --test             # 发送测试邮件
  python hfut_email.py --setup            # 初始化配置向导
"""

import sys, io, os, json, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from hfut_config import (
    KEYWORDS, PRIORITY_ORDER, CAT_COLORS, CAT_ICONS, classify_hfut,
)

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "hfut_email_config.json")
INDEX_FILE  = os.path.join(SCRIPT_DIR, "hfut_full_index.json")
STATE_FILE  = os.path.join(SCRIPT_DIR, "hfut_scan_state.json")


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


def build_html_report(index, state, new_count=0) -> str:
    """构建精美 HTML 邮件报告"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    gen_time = now.strftime("%Y-%m-%d %H:%M:%S")

    total = state.get("total_items", len(index))
    channel_results = state.get("channel_results", {})
    ok_count  = len([k for k, v in channel_results.items() if v > 0])
    fail_list = state.get("failed_channels", [])
    last_scan = state.get("last_scan_time", "未知")

    # 近7天条目
    recent = [i for i in index if i.get("date", "") >= week_ago]
    recent_sorted = sorted(recent, key=lambda x: (x.get("score", 0), x.get("date", "")), reverse=True)

    # 关键通知
    flagged = []
    for item in recent_sorted:
        cat = classify_hfut(item.get("title", ""), item.get("score", 0))
        if cat:
            flagged.append((cat, item))

    # ── 状态概要卡片 HTML ──
    fail_html = ""
    if fail_list:
        fail_html = f"""
        <tr>
          <td style="padding:8px 12px;color:#94a3b8;font-size:13px;">失败栏目</td>
          <td style="padding:8px 12px;color:#ef4444;font-size:13px;">⚠ {', '.join(fail_list)}</td>
        </tr>"""

    # ── 关键通知列表 HTML ──
    if flagged:
        alert_banner = f"""
        <div style="background:linear-gradient(135deg,#fef3c7,#fde68a);border-left:4px solid #f59e0b;
                    padding:14px 18px;border-radius:8px;margin-bottom:20px;">
          <div style="font-weight:700;color:#92400e;font-size:15px;">
            🔔 发现 {len(flagged)} 条关键通知（近7天）
          </div>
          <div style="color:#78350f;font-size:13px;margin-top:4px;">
            请及时查看，以免错过重要截止日期
          </div>
        </div>"""

        notices_html = ""
        for cat, item in flagged[:15]:
            color  = CAT_COLORS.get(cat, "#64748b")
            icon   = CAT_ICONS.get(cat, "📋")
            title  = item.get("title", "无标题")
            date   = item.get("date", "")
            ch     = item.get("channel", "")
            url    = item.get("url", "#")
            score  = item.get("score", 0)
            notices_html += f"""
            <div style="background:#fff;border:1px solid #e2e8f0;border-left:4px solid {color};
                        border-radius:8px;padding:14px 16px;margin-bottom:12px;">
              <div style="margin-bottom:6px;">
                <span style="background:{color}18;color:{color};border:1px solid {color}40;
                             padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;">
                  {icon} {cat}
                </span>
                <span style="color:#94a3b8;font-size:12px;margin-left:10px;">{date}</span>
              </div>
              <a href="{url}" style="color:#1e293b;font-size:14px;font-weight:600;
                                     text-decoration:none;line-height:1.5;display:block;">
                {title}
              </a>
              <div style="margin-top:6px;display:flex;align-items:center;gap:12px;">
                <span style="color:#64748b;font-size:12px;">📍 {ch}</span>
                <span style="color:#64748b;font-size:12px;">相关度 +{score}</span>
                <a href="{url}" style="color:{color};font-size:12px;font-weight:500;text-decoration:none;">
                  查看原文 →
                </a>
              </div>
            </div>"""
    else:
        alert_banner = ""
        notices_html = """
        <div style="text-align:center;padding:40px 20px;color:#94a3b8;">
          <div style="font-size:36px;margin-bottom:12px;">✅</div>
          <div style="font-size:15px;font-weight:600;">近7天无关键通知</div>
          <div style="font-size:13px;margin-top:6px;">保研、竞赛、教务、奖学金类通知均无更新</div>
        </div>"""

    # ── 今日新增模块 ──
    today_items = [i for i in index if i.get("date", "") == today]
    if new_count > 0 and today_items:
        new_items_html = ""
        for item in sorted(today_items, key=lambda x: x.get("score", 0), reverse=True)[:10]:
            url   = item.get("url", "#")
            title = item.get("title", "无标题")
            ch    = item.get("channel", "")
            new_items_html += f"""
            <div style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">
              <a href="{url}" style="color:#1e293b;font-size:13px;text-decoration:none;
                                     display:block;margin-bottom:4px;">{title}</a>
              <span style="color:#94a3b8;font-size:12px;">📍 {ch}</span>
            </div>"""
        new_section = f"""
        <div style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;
                    margin-bottom:20px;overflow:hidden;">
          <div style="background:#f0fdf4;padding:14px 18px;border-bottom:1px solid #dcfce7;">
            <span style="font-weight:700;color:#166534;font-size:14px;">
              🆕 今日新增 {new_count} 条
            </span>
          </div>
          {new_items_html}
        </div>"""
    else:
        new_section = f"""
        <div style="background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0;
                    padding:16px 18px;margin-bottom:20px;text-align:center;color:#64748b;font-size:13px;">
          今日无新增通知（总索引 {total} 条）
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>合工大官网巡检报告 {today}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;">

<div style="max-width:640px;margin:0 auto;padding:20px 16px;">

  <!-- 头部 -->
  <div style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);
              border-radius:16px;padding:32px 28px;margin-bottom:20px;text-align:center;">
    <div style="font-size:28px;margin-bottom:8px;">🏫</div>
    <h1 style="color:#f8fafc;font-size:22px;font-weight:800;margin:0 0 6px;">
      合工大官网巡检报告
    </h1>
    <div style="color:#94a3b8;font-size:13px;">{gen_time} 自动生成</div>
    {"<div style='margin-top:12px;display:inline-block;background:#fef3c7;color:#92400e;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:600;'>🔔 有关键通知！</div>" if flagged else "<div style='margin-top:12px;display:inline-block;background:rgba(16,185,129,0.15);color:#6ee7b7;padding:4px 14px;border-radius:20px;font-size:12px;font-weight:600;'>✓ 系统运行正常</div>"}
  </div>

  <!-- 统计卡片 -->
  <div style="display:flex;gap:12px;margin-bottom:20px;">
    <div style="flex:1;background:#fff;border-radius:12px;border:1px solid #e2e8f0;
                padding:16px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:#f59e0b;">{total}</div>
      <div style="color:#64748b;font-size:12px;margin-top:4px;">总索引</div>
    </div>
    <div style="flex:1;background:#fff;border-radius:12px;border:1px solid #e2e8f0;
                padding:16px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:#10b981;">{new_count}</div>
      <div style="color:#64748b;font-size:12px;margin-top:4px;">今日新增</div>
    </div>
    <div style="flex:1;background:#fff;border-radius:12px;border:1px solid #e2e8f0;
                padding:16px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:#{"ef4444" if len(flagged) else "3b82f6"};">{len(flagged)}</div>
      <div style="color:#64748b;font-size:12px;margin-top:4px;">关键通知</div>
    </div>
    <div style="flex:1;background:#fff;border-radius:12px;border:1px solid #e2e8f0;
                padding:16px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:#{"f59e0b" if fail_list else "6366f1"};">{ok_count}</div>
      <div style="color:#64748b;font-size:12px;margin-top:4px;">成功栏目</div>
    </div>
  </div>

  <!-- 系统状态 -->
  <div style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;
              margin-bottom:20px;overflow:hidden;">
    <div style="background:#f8fafc;padding:12px 18px;border-bottom:1px solid #e2e8f0;">
      <span style="font-weight:700;color:#334155;font-size:14px;">📊 扫描状态</span>
    </div>
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <td style="padding:8px 12px;color:#94a3b8;font-size:13px;">上次扫描</td>
        <td style="padding:8px 12px;color:#334155;font-size:13px;">{last_scan}</td>
      </tr>
      <tr style="background:#f8fafc;">
        <td style="padding:8px 12px;color:#94a3b8;font-size:13px;">栏目覆盖</td>
        <td style="padding:8px 12px;color:#334155;font-size:13px;">
          {ok_count}/{ok_count + len(fail_list)} 个成功
          {"（<span style='color:#ef4444'>失败: " + "、".join(fail_list) + "</span>）" if fail_list else " ✅"}
        </td>
      </tr>
      <tr>
        <td style="padding:8px 12px;color:#94a3b8;font-size:13px;">近7天关键</td>
        <td style="padding:8px 12px;color:#334155;font-size:13px;">
          {"、".join(set(cat for cat, _ in flagged)) if flagged else "无"}
        </td>
      </tr>
      {fail_html}
    </table>
  </div>

  <!-- 今日新增 -->
  {new_section}

  <!-- 关键通知提醒 -->
  {alert_banner}

  <!-- 关键通知列表 -->
  <div style="background:#fff;border-radius:12px;border:1px solid #e2e8f0;
              margin-bottom:20px;overflow:hidden;">
    <div style="background:#f8fafc;padding:12px 18px;border-bottom:1px solid #e2e8f0;">
      <span style="font-weight:700;color:#334155;font-size:14px;">🔔 近7天关键通知</span>
    </div>
    <div style="padding:16px;">
      {notices_html}
    </div>
  </div>

  <!-- 页脚 -->
  <div style="text-align:center;padding:20px;color:#94a3b8;font-size:12px;">
    <div>合肥工业大学官网信息自动巡检系统 · 每日 08:30 自动推送</div>
    <div style="margin-top:6px;">如需取消订阅，请联系系统管理员</div>
  </div>

</div>
</body>
</html>"""
    return html


def _send_plain_email(text_content: str, subject: str, config: dict) -> bool:
    """发送纯文本邮件（内部函数）"""
    sender     = config.get("sender")
    password   = config.get("password")
    recipients = config.get("recipients", [])
    smtp_host  = config.get("smtp_host", "smtp.qq.com")
    smtp_port  = config.get("smtp_port", 465)

    if not sender or not recipients:
        print("❌ 未配置发件人或收件人")
        return False

    msg = MIMEText(text_content, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)

    def _try_smtp(port: int, use_ssl: bool, timeout: int = 45) -> bool:
        ctx = ssl.create_default_context()
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, port, context=ctx, timeout=timeout) as server:
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=ctx)
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())
            return True
        except Exception as e:
            print(f"   [{port}{'SSL' if use_ssl else 'STARTTLS'}] {type(e).__name__}: {e}")
            return False

    # 优先 465 SSL；失败则降级 587 STARTTLS
    if _try_smtp(465, use_ssl=True):
        print(f"✅ 邮件已发送 → {', '.join(recipients)}")
        return True
    if _try_smtp(587, use_ssl=False):
        print(f"✅ 邮件已发送（降级587）→ {', '.join(recipients)}")
        return True
    print(f"❌ SMTP 发送失败")
    return False


def send_email(html_content: str, subject: str, config: dict) -> bool:
    """发送 HTML 邮件，465 SSL 失败时自动降级到 587 STARTTLS"""
    smtp_host = config.get("smtp_host", "smtp.qq.com")
    sender    = config["sender"]
    password  = config["password"]
    recipients = config.get("recipients", [])

    if not recipients:
        print("❌ 未配置收件人")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    def _try_smtp(port: int, use_ssl: bool, timeout: int = 45) -> bool:
        ctx = ssl.create_default_context()
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, port, context=ctx, timeout=timeout) as server:
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, port, timeout=timeout) as server:
                    server.ehlo()
                    server.starttls(context=ctx)
                    server.login(sender, password)
                    server.sendmail(sender, recipients, msg.as_string())
            return True
        except Exception as e:
            print(f"   [{port}{'SSL' if use_ssl else 'STARTTLS'}] {type(e).__name__}: {e}")
            return False

    # 优先 465 SSL；失败则降级 587 STARTTLS
    tried = []
    if _try_smtp(465, use_ssl=True):
        print(f"✅ 邮件已发送 → {', '.join(recipients)}")
        return True
    tried.append("465-SSL")

    if _try_smtp(587, use_ssl=False):
        print(f"✅ 邮件已发送（降级587）→ {', '.join(recipients)}")
        return True
    tried.append("587-STARTTLS")

    print(f"❌ SMTP 发送失败（已尝试: {', '.join(tried)}）")
    return False


def setup_wizard():
    """配置向导"""
    print("=" * 50)
    print("  合工大巡检邮件推送 — 配置向导")
    print("=" * 50)
    print()
    print("需要以下信息（QQ邮箱为例）：")
    print("  发件邮箱：你的QQ号@qq.com")
    print("  授权码：QQ邮箱设置 → 账户 → POP3/SMTP → 开启 → 生成授权码")
    print("  收件邮箱：2240678683@qq.com（已预填）")
    print()

    config = {
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "sender": input("发件邮箱（如 123456@qq.com）: ").strip(),
        "password": input("邮箱授权码（16位）: ").strip(),
        "recipients": ["2240678683@qq.com"]
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 配置已保存：{CONFIG_FILE}")
    print("运行 python hfut_email.py --test 验证发送")


def main():
    args = sys.argv[1:]

    if "--setup" in args:
        setup_wizard()
        return

    # 加载配置
    if not os.path.exists(CONFIG_FILE):
        print(f"⚠ 未找到邮件配置文件：{CONFIG_FILE}")
        print("请运行 python hfut_email.py --setup 完成配置")
        print()
        print("或手动创建配置文件，格式：")
        print(json.dumps({
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "sender": "你的QQ邮箱@qq.com",
            "password": "16位授权码",
            "recipients": ["2240678683@qq.com"]
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "--test" in args:
        html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px;">
        <h2>✅ 合工大巡检邮件测试</h2>
        <p>发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p>收到此邮件说明邮件推送配置正确！</p>
        <p>每天 <strong>08:30</strong> 将自动推送巡检报告。</p>
        </body></html>"""
        subject = f"✅ 合工大巡检 — 测试邮件 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        send_email(html, subject, config)
        return

    # --text-file=<path> --subject=<text> 模式（纯文本，供 unified_daily_cli 调用）
    text_file_arg = None
    subject_arg   = None
    for a in args:
        if a.startswith("--text-file="):
            text_file_arg = a.split("=", 1)[1]
        if a.startswith("--subject="):
            subject_arg = a.split("=", 1)[1]
    if text_file_arg:
        if not os.path.exists(text_file_arg):
            print(f"❌ 文本文件不存在: {text_file_arg}")
            sys.exit(1)
        with open(text_file_arg, "r", encoding="utf-8") as f:
            text_content = f.read()
        subject = subject_arg or f"每日综合报告 {datetime.now().strftime('%Y-%m-%d')}"
        _send_plain_email(text_content, subject, config)
        return

    # --html-file=<path> --subject=<text> 模式（供 unified_daily_cli 调用）
    html_file_arg = None
    for a in args:
        if a.startswith("--html-file="):
            html_file_arg = a.split("=", 1)[1]
    if html_file_arg:
        if not os.path.exists(html_file_arg):
            print(f"❌ HTML 文件不存在: {html_file_arg}")
            sys.exit(1)
        with open(html_file_arg, "r", encoding="utf-8") as f:
            html = f.read()
        subject = subject_arg or f"每日综合报告 {datetime.now().strftime('%Y-%m-%d')}"
        send_email(html, subject, config)
        return

    # 正常发送巡检报告
    index, state = load_data()
    if not index:
        print("⚠ 无数据，请先运行扫描")
        sys.exit(1)

    # 计算今日新增
    today = datetime.now().strftime("%Y-%m-%d")
    new_count_arg = 0
    for a in args:
        if a.startswith("--new="):
            try:
                new_count_arg = int(a.split("=")[1])
            except:
                pass

    html    = build_html_report(index, state, new_count=new_count_arg)
    today_str = datetime.now().strftime("%Y-%m-%d")
    flagged_count = sum(
        1 for i in index
        if i.get("date", "") >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        and classify_hfut(i.get("title",""), i.get("score",0))
    )
    prefix  = "⚠️ 有关键通知！" if flagged_count else "✅"
    subject = f"{prefix} 合工大巡检报告 {today_str}（新增{new_count_arg}条）"

    send_email(html, subject, config)


if __name__ == "__main__":
    main()
