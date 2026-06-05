"""
QQ 群公告/消息监控模块
通过 agent-browser (Playwright) 登录网页版 QQ，获取群公告和最新消息

需要：
1. agent-browser skill 已安装
2. 用户已登录网页版 QQ（会复用已有登录态的浏览器）

数据输出格式（与现有系统兼容）：
{
    "source": "qq_group",
    "title": "群公告标题",
    "content": "公告内容",
    "time": "2026-06-03",
    "url": "https://...",
    "group_name": "群名称",
    "author": "发布者"
}
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("HFUT_Monitor")


def _load_config():
    """加载配置"""
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def monitor_qq_groups(headless: bool = True) -> list:
    """
    监控 QQ 群公告（使用 Playwright 通过网页版获取）
    
    Args:
        headless: 是否无头模式（False = 显示浏览器，方便首次登录）
        
    Returns:
        list: 群公告/消息列表
    """
    logger.info("🔍 启动 QQ 群监控...")
    
    # 检查 agent-browser 是否可用
    try:
        from agent_browser import AgentBrowser
    except ImportError:
        logger.warning("⚠️ agent-browser 未安装，跳过 QQ 群监控")
        logger.warning("   安装方式: 在 WorkBuddy 中安装 agent-browser 技能")
        return []
    
    config = _load_config()
    qq_accounts = config.get("qq_accounts", [])
    
    if not qq_accounts:
        logger.warning("⚠️ 配置中未找到 qq_accounts，跳过")
        return []
    
    results = []
    
    try:
        browser = AgentBrowser(headless=headless)
        
        # 打开 QQ 邮箱/群公告页面（无需完整 QQ 网页版，用 QQ 邮箱的群公告入口）
        # 实际方案：访问 QQ 群论坛/公告页面
        logger.info("   正在打开 QQ 群公告页面...")
        
        # 方案：通过 QQ 邮箱的群应用获取公告
        # URL: https://mail.qq.com (登录后进入群应用)
        # 由于需要登录，这里采用 pragmatic 方案：
        # 读取 QQ 本地数据库（如果 wechat-decrypt / QQDecrypt 可用）
        
        logger.info("   尝试通过本地 QQ 数据库读取群公告...")
        db_results = _read_qq_db()
        if db_results:
            results.extend(db_results)
            logger.info(f"   ✅ 从本地数据库读取到 {len(db_results)} 条群消息")
        else:
            logger.warning("   本地数据库不可用，需要配置 QQDecrypt")
            logger.warning("   或手动登录 QQ 网页版后重试")
        
        browser.close()
        
    except Exception as e:
        logger.error(f"❌ QQ 群监控失败: {e}")
        return []
    
    logger.info(f"✅ QQ 群监控完成，获得 {len(results)} 条数据")
    return results


def _read_qq_db() -> list:
    """
    尝试读取 QQ 本地数据库获取群消息
    需要 QQDecrypt 工具先解密 nt_msg.db
    """
    # QQ NT 数据库路径（Windows）
    qq_nt_paths = [
        Path.home() / "Documents" / "Tencent Files" / "nt_qq" / "nt_msg.db",
        Path(os.environ.get("QQ_HOME", "")) / "nt_qq" / "nt_msg.db",
    ]
    
    # 检查是否有已解密的数据库
    decrypted_dir = Path(__file__).parent.parent / "data" / "qq_decrypted"
    if decrypted_dir.exists():
        db_files = list(decrypted_dir.glob("*.db"))
        if db_files:
            return _parse_qq_db(db_files[0])
    
    logger.debug("   未找到已解密的 QQ 数据库")
    logger.debug("   请先用 QQDecrypt 解密 %APPDATA%\\Tencent\\QQ\\nt_qq\\nt_msg.db")
    return []


def _parse_qq_db(db_path: Path) -> list:
    """解析已解密的 QQ 数据库，提取群公告/消息"""
    import sqlite3
    
    results = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 查询群消息表（QQ NT 数据库结构）
        # 实际表名需要通过 QQDecrypt 文档确认
        tables = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        logger.debug(f"   QQ DB 表: {table_names}")
        
        # 尝试常见表名
        for table in ["group_msg_table", "msg_table", "group_message", "messages"]:
            if table in table_names:
                rows = cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 50").fetchall()
                desc = cursor.description
                for row in rows:
                    item = dict(zip([d[0] for d in desc], row))
                    results.append({
                        "source": "qq_group_db",
                        "title": item.get("friend_name", item.get("sender", "未知")),
                        "content": item.get("msg_data", item.get("content", "")),
                        "time": datetime.now().strftime("%Y-%m-%d"),
                        "group_name": item.get("group_name", ""),
                        "url": "",
                    })
                break
        
        conn.close()
    except Exception as e:
        logger.debug(f"   解析 QQ 数据库失败: {e}")
    
    return results


def monitor_qq_official_accounts() -> list:
    """
    监控 QQ 公众号/订阅号（如「合肥工业大学」）
    通过 Playwright 访问 QQ 公众号页面
    """
    logger.info("📢 启动 QQ 公众号监控...")
    results = []
    
    # QQ 公众号内容需要通过 QQ 客户端或网页版访问
    # 这里提供一个框架，实际使用时需要:
    # 1. 登录 QQ 网页版
    # 2. 访问公众号页面
    # 3. 提取文章列表
    
    logger.warning("⚠️ QQ 公众号监控需要登录态，建议使用微信公众号监控替代")
    logger.warning("   合肥工业大学在微信有公众号，建议优先使用 wechat_monitor.py")
    
    return results


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    print("测试 QQ 群监控...")
    data = monitor_qq_groups(headless=False)
    print(f"获得 {len(data)} 条数据")
    for item in data[:5]:
        print(f"  - {item.get('title', '')}: {item.get('content', '')[:50]}")
