"""
推送模块
功能：推送监控到的有用信息到 QQ邮箱 和 QQ (通过NapCat)

修复：
- SMTP 连接泄漏 → 使用 try-finally 确保释放
- 统一使用 logging（去除 print）
- 添加重试机制
- 超时和连接池管理
"""

import smtplib
import json
import time
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
import sys
import os
import logging
from typing import Optional

# 添加 scripts 目录到路径
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

# HTTP 会话（复用连接）
_http_session: Optional[requests.Session] = None


def _get_http_session() -> requests.Session:
    """获取或创建 HTTP 会话（连接复用）"""
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
        _http_session.headers.update({
            'User-Agent': 'HFUT-Monitor/3.1'
        })
    return _http_session


def send_qq_message(user_id=None, message="", max_retries=2):
    """
    通过 NapCat 发送 QQ 消息

    Args:
        user_id: 接收者QQ号，默认使用配置中的 default_receiver
        message: 消息内容
        max_retries: 最大重试次数

    Returns:
        dict: API 响应结果 {"status": "ok"/"error", "message": "..."}
    """
    if not NAPCAT_CONFIG.get('enable', False):
        return {"status": "error", "message": "NapCat is disabled"}

    if user_id is None:
        user_id = NAPCAT_CONFIG.get('default_receiver')  # 移除硬编码默认值，必须从配置读取

    params = {
        "user_id": user_id,
        "message": message,
        "access_token": NAPCAT_CONFIG.get('token', '')
    }

    session = _get_http_session()
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(
                f"{NAPCAT_CONFIG['api_url']}/send_private_msg",
                params=params,
                timeout=10
            )
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('status') == 'ok':
                    logger.info(f"[QQ] 消息发送成功 → QQ:{user_id}")
                else:
                    logger.warning(f"[QQ] API返回异常：{result}")
                return result
            else:
                last_error = f"HTTP {resp.status_code}"
                logger.warning(f"[QQ] 请求失败（第{attempt+1}次）：{last_error}")
                
        except requests.exceptions.ConnectionError as e:
            last_error = f"连接失败：{e}"
            logger.warning(f"[QQ] 连接失败（第{attempt+1}次）：{e}")
        except requests.exceptions.Timeout as e:
            last_error = f"超时：{e}"
            logger.warning(f"[QQ] 请求超时（第{attempt+1}次）")
        except Exception as e:
            last_error = str(e)
            logger.error(f"[QQ] 未知错误：{e}")
            break  # 非网络类错误不重试
        
        if attempt < max_retries:
            time.sleep(2 ** attempt)  # 指数退避（修复：原为 1**attempt 始终等于1）
    
    logger.error(f"[QQ] 消息发送最终失败：{last_error}")
    return {"status": "error", "message": last_error or "未知错误"}


def send_email(subject: str, content: str, to_email: str = None) -> bool:
    """
    发送邮件（修复：确保 SMTP 连接正确释放）
    
    Args:
        subject: 邮件主题
        content: 邮件内容（纯文本）
        to_email: 收件人邮箱，默认使用配置中的 receiver_email
        
    Returns:
        bool: 是否发送成功
    """
    if not to_email:
        to_email = RECEIVER_EMAIL

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(content, 'plain', 'utf-8'))

    server = None
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
            server.starttls()
        
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        
        logger.info(f"[Email] ✅ 发送成功：{subject} → {to_email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[Email] ❌ 认证失败：{e}")
        return False
    except smtplib.SMTPConnectError as e:
        logger.error(f"[Email] ❌ 连接失败：{e}")
        return False
    except smtplib.SMTPRecipientsRefused as e:
        logger.error(f"[Email] ❌ 收件人拒绝：{e}")
        return False
    except Exception as e:
        logger.error(f"[Email] ❌ 发送失败：{type(e).__name__}: {e}")
        return False
    finally:
        # 🔴 关键修复：确保 SMTP 连接始终释放
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass  # quit 失败不影响主流程


def push_immediate(info: dict, use_qq=True, use_email=True):
    """
    即时推送重要信息（同时推送到 QQ 和邮箱）

    Args:
        info: 信息字典（包含 title, content, time, source 等）
        use_qq: 是否使用 QQ 推送
        use_email: 是否使用邮件推送
    """
    title = info.get('title', '')[:50] or '无标题'
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
            logger.info(f"[QQ] ✅ 紧急推送成功：{title}")
        else:
            logger.warning(f"[QQ] ⚠️ 紧急推送失败：{result.get('message')}")

    # 邮件消息（完整版）
    if use_email:
        subject = f"【紧急】{title}"
        content = f"""合工大信息监控系统 - 紧急通知

时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

信息来源：{source}
发布时间：{time_str}

标题：
{info.get('title', '无')}

内容：
{info.get('content', '无')}

关键词：
{keywords}

---
此邮件由AI私人秘书自动发送
        """
        email_ok = send_email(subject, content)
        if not email_ok:
            logger.warning(f"[推送] 即时邮件推送可能失败：{subject}")


def push_daily_digest(infos: list, use_qq=True, use_email=True):
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
            qq_msg += f"{i}. {info.get('title', 'N/A')[:40]}\n   来源: {info.get('source', '未知')}\n\n"

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
标题：{info.get('title', 'N/A')}
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


def push_test_qq() -> bool:
    """测试 QQ 推送"""
    if not NAPCAT_CONFIG.get('enable', False):
        logger.warning("[QQ] NapCat 未启用，请在 config.json 中启用或设置环境变量 NAPCAT_TOKEN")
        return False

    message = f"""[合工大监控系统] 测试消息

时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
状态: NapCat QQ 推送功能正常！

如果你收到这条消息，说明 QQ 推送已配置成功。
"""
    result = send_qq_message(message=message)
    if result.get('status') == 'ok':
        logger.info("[QQ] ✅ 测试消息发送成功！")
        return True
    else:
        logger.error(f"[QQ] ❌ 测试消息发送失败: {result}")
        return False


def push_test_email() -> bool:
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
    logger.info("\n" + "="*50)
    logger.info("测试推送功能")
    logger.info("="*50)

    logger.info("[1] 测试 QQ 推送...")
    push_test_qq()

    logger.info("[2] 测试邮件推送...")
    push_test_email()

    logger.info("="*50)


if __name__ == '__main__':
    push_test()
