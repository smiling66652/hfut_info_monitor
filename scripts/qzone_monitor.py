"""
QQ空间监控模块
功能：监控指定QQ号的空间动态，过滤推送

修复：
- 时间格式标准化为 %Y-%m-%d（与日报过滤条件兼容）
- 使用统一的 archive 模块
- 统一使用 logging
- 增强异常处理
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

QQ_ACCOUNTS = CONFIG.get('qq_accounts', [])

# 导入自定义模块
from filter import filter_and_extract
from push import push_immediate, push_daily_digest
from archive import save_to_archive

logger = logging.getLogger('QzoneMonitor')


def get_qzone_moments(qq: str, cookies=None) -> list[dict]:
    """
    获取QQ空间动态
    :param qq: QQ号
    :param cookies: 登录cookies（可选）
    :return: 动态列表
    """
    logger.info(f"🔍 获取QQ空间动态：{qq}")
    
    # 方法1：使用 go-cqhttp（推荐）
    # TODO：需要安装和配置 go-cqhttp
    
    # 方法2：使用 cookies 直接请求（需要登录态）
    if not cookies:
        logger.warning(f"⚠️ 未提供cookies，使用模拟数据（QQ: {qq}）")
        moments = [
            {
                'qq': qq,
                'content': f'【模拟数据】QQ空间动态示例 - {datetime.now().strftime("%Y-%m-%d %H:%M")}',
                # 🔴 关键修复：时间格式统一为 %Y-%m-%d，确保能被日报正确匹配
                'time': datetime.now().strftime('%Y-%m-%d')
            }
        ]
        return moments
    
    # 实际请求（需要有效的cookies）
    url = f"https://user.qzone.qq.com/{qq}/infocenter"
    headers = {
        'Cookie': cookies,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        moments = []  # 解析后的动态列表
        return moments
    except Exception as e:
        logger.error(f"❌ 获取动态失败：{e}")
        return []


def process_moment(moment: dict) -> bool:
    """
    处理单条动态
    :param moment: 动态信息
    :return: 是否推送
    """
    content = moment.get('content', '')
    qq = moment.get('qq', '')
    time_str = moment.get('time', '')
    
    if not content:
        logger.warning("动态内容为空，跳过")
        return False
    
    # 过滤垃圾信息
    is_pass, info = filter_and_extract(content)
    
    if not is_pass:
        logger.debug(f"🗑️ 过滤垃圾信息：{content[:30]}")
        return False
    
    # 补充信息
    info['source'] = f'QQ空间（{qq}）'
    info['title'] = content[:50]
    info['time'] = time_str or datetime.now().strftime('%Y-%m-%d')
    info['qq'] = qq
    
    # 推送到QQ邮箱
    if info.get('is_important'):
        logger.info(f"🚨 推送重要信息：{info['title']}")
        push_immediate(info)
    else:
        logger.info(f"📝 归档普通信息：{info['title'][:30]}")
        save_to_archive(info)
    
    return True


def monitor_qzone():
    """
    主监控函数
    """
    logger.info("=" * 60)
    logger.info("合工大信息监控系统 - QQ空间监控")
    logger.info("=" * 60)
    logger.info(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    all_moments = []
    
    for qq in QQ_ACCOUNTS:
        logger.info(f"📋 处理QQ号：{qq}")
        
        try:
            # Step 1：获取空间动态
            moments = get_qzone_moments(qq)
            
            if not moments:
                logger.info(f"  未找到动态")
                continue
            
            logger.info(f"  找到 {len(moments)} 条动态")
            
            # Step 2：处理每条动态
            for moment in moments:
                try:
                    success = process_moment(moment)
                    if success:
                        all_moments.append(moment)
                    
                    # 避免频率限制
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"处理动态失败：{e}")
                    continue
            
        except Exception as e:
            logger.error(f"处理QQ号 [{qq}] 时出错：{e}")
            continue
        
        logger.info("")
    
    # Step 3：生成每日摘要
    if all_moments:
        logger.info(f"📊 共处理 {len(all_moments)} 条动态")
    
    logger.info(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


def setup_gocqhttp():
    """go-cqhttp 配置说明"""
    print("📋 go-cqhttp 配置说明")
    print("=" * 60)
    print("1. 下载 go-cqhttp")
    print("   网址：https://github.com/Mrs4s/go-cqhttp/releases")
    print()
    print("2. 初始化并配置")
    print("3. 监听QQ空间动态使用 OneBot v11 协议")
    print("=" * 60)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logger.info("📧 测试QQ空间监控模块...")
    logger.info("⚠️ 注意：需要配置 go-cqhttp 才能实际使用")
    
    monitor_qzone()
