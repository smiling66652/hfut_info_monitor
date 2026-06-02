"""
微信公众号监控模块
功能：监控合肥工业大学相关公众号，爬取文章，过滤推送
"""

import json
import requests
from datetime import datetime
import time
import sys
import os

# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import load_config

# 加载配置
CONFIG = load_config()

WECHAT_ACCOUNTS = CONFIG['wechat_accounts']
PUSH_METHOD = CONFIG['push_method']

# 导入自定义模块
from filter import filter_and_extract
from push import push_immediate, push_daily_digest

def search_wechat_account(account_name):
    """
    搜索微信公众号（使用搜狗微信搜索）
    :param account_name: 公众号名称
    :return: 公众号信息列表
    """
    # 注意：这里需要使用 WechatSogou 库
    # 由于该库需要单独安装，这里提供接口框架
    
    print(f"🔍 搜索公众号：{account_name}")
    
    # TODO：实际实现需要安装 WechatSogou
    # from wechatsogou import WechatSogouAPI
    # ws_api = WechatSogouAPI()
    # accounts = ws_api.search_account(account_name)
    
    # 模拟数据（实际使用时删除）
    accounts = [
        {
            'account': account_name,
            'account_id': 'mock_id_12345'
        }
    ]
    
    return accounts

def get_account_articles(account):
    """
    获取公众号历史文章
    :param account: 公众号信息
    :return: 文章列表
    """
    account_name = account['account']
    print(f"📖 获取公众号文章：{account_name}")
    
    # TODO：实际实现
    # from wechatsogou import WechatSogouAPI
    # ws_api = WechatSogouAPI()
    # articles = ws_api.get_account_article(account['account_id'])
    
    # 模拟数据（实际使用时删除）
    articles = [
        {
            'title': '【模拟数据】合工大2026年保研通知',
            'article_url': 'http://mp.weixin.qq.com/mock_url',
            'time': datetime.now().strftime('%Y-%m-%d'),
            'content': '这是模拟的保研通知内容...'
        }
    ]
    
    return articles

def process_article(article):
    """
    处理单篇文章
    :param article: 文章信息
    :return: 是否推送
    """
    title = article['title']
    content = article.get('content', '')
    
    # 合并标题和内容进行过滤
    text = title + '\n' + content
    
    # 过滤垃圾信息
    is_pass, info = filter_and_extract(text)
    
    if not is_pass:
        print(f"🗑️  过滤垃圾信息：{title}")
        return False
    
    # 补充信息
    info['source'] = '微信公众号'
    info['title'] = title
    info['url'] = article.get('article_url', '')
    info['time'] = article.get('time', '')
    
    # 推送到QQ邮箱
    if info['is_important']:
        print(f"🚨 推送重要信息：{title}")
        push_immediate(info)
    else:
        print(f"📝 归档普通信息：{title}")
        # 保存到数据库或文件
        save_to_archive(info)
    
    return True

def save_to_archive(info):
    """
    保存信息到归档
    :param info: 信息字典
    """
    from config_loader import get_json_path, get_db_path
    
    # 方案1：保存到JSON文件
    with open(get_json_path(), 'a', encoding='utf-8') as f:
        import json
        json.dump(info, f, ensure_ascii=False)
        f.write('\n')
    
    # 方案2：保存到SQLite数据库（可选）
    # import sqlite3
    # conn = sqlite3.connect(get_db_path())
    # conn = sqlite3.connect('D:/hfut_info_monitor/data/hfut_archive.db')
    # ...

def monitor_wechat():
    """
    主监控函数
    """
    print("=" * 60)
    print("合工大信息监控系统 - 微信公众号监控")
    print("=" * 60)
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    all_articles = []
    
    for account_name in WECHAT_ACCOUNTS:
        print(f"📋 处理公众号：{account_name}")
        
        # Step 1：搜索公众号
        accounts = search_wechat_account(account_name)
        
        if not accounts:
            print(f"❌ 未找到公众号：{account_name}")
            continue
        
        # Step 2：获取文章
        for account in accounts:
            articles = get_account_articles(account)
            
            print(f"  找到 {len(articles)} 篇文章")
            
            # Step 3：处理每篇文章
            for article in articles:
                success = process_article(article)
                if success:
                    all_articles.append(article)
                
                # 避免频率限制
                time.sleep(1)
        
        print()
    
    # Step 4：生成每日摘要（如果有文章）
    if all_articles:
        print(f"📊 共处理 {len(all_articles)} 篇文章")
        # 这里可以调用 push_daily_digest(all_articles)
    
    print()
    print(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

if __name__ == '__main__':
    # 测试
    print("📧 测试微信公众号监控模块...")
    print()
    print("⚠️  注意：需要安装 WechatSogou 库才能实际使用")
    print("   安装命令：")
    print("   cd D:/hfut_info_monitor")
    print("   git clone https://github.com/Chyroc/WechatSogou.git")
    print("   cd WechatSogou")
    print("   pip install -r requirements.txt")
    print()
    
    # 运行模拟测试
    monitor_wechat()
