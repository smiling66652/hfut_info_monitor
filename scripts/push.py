"""
推送模块
功能：推送监控到的有用信息到 QQ邮箱 和 QQ (通过NapCat)
"""

import smtplib
import json
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
import sys
import os

# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import load_config, get_log_dir

# 加载配置
CONFIG = load_config()

# 邮件配置
SMTP_SERVER = CONFIG['push_method']['smtp_server']
SMTP_PORT = CONFIG['push_method']['smtp_port']
SENDER_EMAIL = CONFIG['push_method']['sender_email']
SENDER_PASSWORD = CONFIG['push_method']['sender_password']
RECEIVER_EMAIL = CONFIG['push_method']['receiver_email']

# NapCat QQ 配置
NAPCAT_CONFIG = CONFIG.get('napcat', {})

# 配置日志
import logging
LOG_DIR = get_log_dir()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'push.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('Push')

def send_qq_message(user_id=None, message=""):
    """
    通过 NapCat 发送 QQ 消息

    Args:
        user_id: 接收者QQ号，默认使用配置中的 default_receiver
        message: 消息内容

    Returns:
        dict: API 响应结果
    """
    if not NAPCAT_CONFIG.get('enable', False):
        return {"status": "error", "message": "NapCat is disabled"}

    if user_id is None:
        user_id = NAPCAT_CONFIG.get('default_receiver', '1604220682')

    params = {
        "user_id": user_id,
        "message": message,
        "access_token": NAPCAT_CONFIG.get('token', '')
    }

    try:
        resp = requests.get(
            f"{NAPCAT_CONFIG['api_url']}/send_private_msg",
            params=params,
            timeout=10
        )
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

def send_email(subject, content, to_email=None):
    """发送邮件"""
    if not to_email:
        to_email = RECEIVER_EMAIL

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(content, 'plain', 'utf-8'))

    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[Email] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[Email] Failed: {e}")
        return False

def push_immediate(info, use_qq=True, use_email=True):
    """
    即时推送重要信息（同时推送到 QQ 和邮箱）

    Args:
        info: 信息字典（包含 title, content, time, source 等）
        use_qq: 是否使用 QQ 推送
        use_email: 是否使用邮件推送
    """
    title = info['title'][:50] if len(info['title']) > 50 else info['title']
    source = info.get('source', '未知')
    time_str = info.get('time', '未知')
    keywords = ', '.join(info.get('keywords', []))

    # QQ 消息（简洁版）
    if use_qq and NAPCAT_CONFIG.get('enable', False):
        qq_msg = f"""[合工大] 紧急通知

{title}

来源: {source}
时间: {time_str}
关键词: {keywords}

{info.get('content', '')[:200]}"""

        result = send_qq_message(message=qq_msg)
        if result.get('status') == 'ok':
            print(f"[QQ] 紧急推送成功")
        else:
            print(f"[QQ] 紧急推送失败: {result.get('message')}")

    # 邮件消息（完整版）
    if use_email:
        subject = f"【紧急】{title}"
        content = f"""合工大信息监控系统 - 紧急通知

时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

信息来源：{source}
发布时间：{time_str}

标题：
{info['title']}

内容：
{info.get('content', '无')}

关键词：
{keywords}

---
此邮件由AI私人秘书自动发送
        """
        send_email(subject, content)

def push_daily_digest(infos, use_qq=True, use_email=True):
    """
    推送每日摘要

    Args:
        infos: 信息列表
        use_qq: 是否使用 QQ 推送
        use_email: 是否使用邮件推送
    """
    date_str = datetime.now().strftime('%Y年%m月%d日')
    count = len(infos)

    # QQ 消息（摘要版）
    if use_qq and NAPCAT_CONFIG.get('enable', False):
        qq_msg = f"""[合工大] 每日摘要 - {date_str}

今日收集到 {count} 条有用信息

"""
        for i, info in enumerate(infos[:5], 1):  # 只显示前5条
            qq_msg += f"{i}. {info['title'][:40]}\n   来源: {info.get('source', '未知')}\n\n"

        if count > 5:
            qq_msg += f"... 还有 {count - 5} 条信息，请查看邮件"

        send_qq_message(message=qq_msg)

    # 邮件消息（完整版）
    if use_email:
        subject = f"合工大信息日报 - {datetime.now().strftime('%Y-%m-%d')}"
        content = f"""合工大信息监控系统 - 每日摘要

日期：{date_str}
共收集到 {count} 条有用信息

"""

        for i, info in enumerate(infos, 1):
            content += f"""
【信息 {i}】
标题：{info['title']}
来源：{info.get('source', '未知')}
时间：{info.get('time', '未知')}
关键词：{', '.join(info.get('keywords', []))}

内容摘要：
{info.get('content', '无')[:200]}...

------------------------------------------------------------
"""

        content += """
此邮件由AI私人秘书自动生成
"""

        send_email(subject, content)

def push_test_qq():
    """测试 QQ 推送"""
    if not NAPCAT_CONFIG.get('enable', False):
        print("[QQ] NapCat 未启用，请在 config.json 中启用")
        return False

    message = f"""[合工大监控系统] 测试消息

时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
状态: NapCat QQ 推送功能正常！

如果你收到这条消息，说明 QQ 推送已配置成功。
"""
    result = send_qq_message(message=message)
    if result.get('status') == 'ok':
        print("[QQ] 测试消息发送成功！")
        return True
    else:
        print(f"[QQ] 测试消息发送失败: {result}")
        return False

def push_test_email():
    """测试邮件推送"""
    subject = "合工大信息监控系统 - 测试邮件"
    content = f"""这是一封测试邮件。

如果你收到这封邮件，说明QQ邮箱推送功能配置正确。

时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---
合工大信息监控系统
    """
    return send_email(subject, content)

def push_test():
    """测试所有推送渠道"""
    print("\n" + "="*50)
    print("测试推送功能")
    print("="*50)

    print("\n[1] 测试 QQ 推送...")
    push_test_qq()

    print("\n[2] 测试邮件推送...")
    push_test_email()

    print("\n" + "="*50)

if __name__ == '__main__':
    push_test()
