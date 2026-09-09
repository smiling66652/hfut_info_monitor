"""
微信公众号监控模块（已弃用 - DEPRECATED）
功能：监控合肥工业大学相关公众号，爬取文章，过滤推送

⚠️ 弃用说明：
此模块为旧版微信监控脚本，已不再被 task_orchestrator 调用。
生产环境请使用 wechat_account_monitor.py 模块。
本文件保留仅为历史参考，所有模拟数据已移除。

修复：
- 使用统一的 archive 模块（消除重复的 save_to_archive）
- 统一使用 logging
- 增强异常处理
- 移除所有模拟/假数据返回
"""

import json
import requests
from datetime import datetime
import time
import sys
import os
import logging

# 添加 scripts 目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import load_config

# 加载配置
CONFIG = load_config()

WECHAT_ACCOUNTS = CONFIG.get('wechat_accounts', [])

# 导入自定义模块
from filter import filter_and_extract
from push import push_immediate, push_daily_digest
from archive import save_to_archive

logger = logging.getLogger('WechatMonitor')


def search_wechat_account(account_name: str) -> list[dict]:
    """
    搜索微信公众号（使用搜狗微信搜索）
    :param account_name: 公众号名称
    :return: 公众号信息列表
    """
    logger.info("🔍 搜索公众号：%s", account_name)

    # ⚠️ 此模块已弃用，不再返回模拟数据
    # 实际搜索请使用 wechat_account_monitor.py
    logger.warning(
        "⚠️ [DEPRECATED] wechat_monitor 已弃用，无法搜索公众号 [%s]。"
        "请使用 wechat_account_monitor.py 替代", account_name
    )
    return []


def get_account_articles(account: dict) -> list[dict]:
    """
    获取公众号历史文章
    :param account: 公众号信息
    :return: 文章列表
    """
    account_name = account.get('account', '未知')
    logger.info("📖 获取公众号文章：%s", account_name)

    # ⚠️ 此模块已弃用，不再返回模拟数据
    # 实际文章获取请使用 wechat_account_monitor.py
    logger.warning(
        "⚠️ [DEPRECATED] wechat_monitor 已弃用，无法获取文章 [%s]。"
        "请使用 wechat_account_monitor.py 替代", account_name
    )
    return []


def process_article(article: dict) -> bool:
    """
    处理单篇文章
    :param article: 文章信息
    :return: 是否推送
    """
    title = article.get('title', '')
    content = article.get('content', '')
    
    if not title:
        logger.warning("文章标题为空，跳过")
        return False
    
    # 合并标题和内容进行过滤
    text = title + '\n' + content
    
    # 过滤垃圾信息
    is_pass, info = filter_and_extract(text)
    
    if not is_pass:
        logger.debug(f"🗑️ 过滤垃圾信息：{title[:30]}")
        return False
    
    # 补充信息
    info['source'] = '微信公众号'
    info['title'] = title
    info['url'] = article.get('article_url', '')
    info['time'] = article.get('time', datetime.now().strftime('%Y-%m-%d'))
    
    # 推送到QQ邮箱
    if info.get('is_important'):
        logger.info(f"🚨 推送重要信息：{title}")
        push_immediate(info)
    else:
        logger.info(f"📝 归档普通信息：{title[:30]}")
        save_to_archive(info)
    
    return True


def monitor_wechat():
    """
    主监控函数
    """
    logger.info("=" * 60)
    logger.info("合工大信息监控系统 - 微信公众号监控")
    logger.info("=" * 60)
    logger.info(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    all_articles = []
    
    for account_name in WECHAT_ACCOUNTS:
        logger.info(f"📋 处理公众号：{account_name}")
        
        try:
            # Step 1：搜索公众号
            accounts = search_wechat_account(account_name)
            
            if not accounts:
                logger.warning(f"未找到公众号：{account_name}")
                continue
            
            # Step 2：获取文章
            for account in accounts:
                articles = get_account_articles(account)
                
                logger.info(f"  找到 {len(articles)} 篇文章")
                
                # Step 3：处理每篇文章
                for article in articles:
                    try:
                        success = process_article(article)
                        if success:
                            all_articles.append(article)
                        
                        # 避免频率限制
                        time.sleep(1)
                    except Exception as e:
                        logger.error(f"处理文章失败：{e}")
                        continue
            
        except Exception as e:
            logger.error(f"处理公众号 [{account_name}] 时出错：{e}")
            continue
    
    # Step 4：生成每日摘要
    if all_articles:
        logger.info(f"📊 共处理 {len(all_articles)} 篇文章")
    
    logger.info(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


if __name__ == '__main__':
    # 配置日志用于独立运行
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logger.info("📧 测试微信公众号监控模块...")
    logger.info("⚠️ 注意：需要安装 WechatSogou 库才能实际使用")
    
    # 运行模拟测试
    monitor_wechat()
