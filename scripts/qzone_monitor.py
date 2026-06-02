"""
QQ空间监控模块
功能：监控指定QQ号的空间动态，过滤推送
"""

import json
import requests
from datetime import datetime
import time

# 加载配置
with open('D:/hfut_info_monitor/config/config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

QQ_ACCOUNTS = CONFIG['qq_accounts']
PUSH_METHOD = CONFIG['push_method']

# 导入自定义模块
from filter import filter_and_extract
from push import push_immediate, push_daily_digest

def get_qzone_moments(qq, cookies=None):
    """
    获取QQ空间动态
    :param qq: QQ号
    :param cookies: 登录cookies（可选）
    :return: 动态列表
    """
    print(f"🔍 获取QQ空间动态：{qq}")
    
    # 方法1：使用 go-cqhttp（推荐）
    # TODO：需要安装和配置 go-cqhttp
    # 这里提供接口框架
    
    # 方法2：使用 cookies 直接请求（需要登录态）
    # 如果没有cookies，返回模拟数据
    if not cookies:
        print("⚠️  未提供cookies，使用模拟数据")
        moments = [
            {
                'qq': qq,
                'content': f'【模拟数据】QQ空间动态示例 - {datetime.now().strftime("%Y-%m-%d %H:%M")}',
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        # 解析HTML获取动态（需要根据实际页面结构调整）
        # 这里省略HTML解析代码
        moments = []  # 解析后的动态列表
        return moments
    except Exception as e:
        print(f"❌ 获取动态失败：{e}")
        return []

def process_moment(moment):
    """
    处理单条动态
    :param moment: 动态信息
    :return: 是否推送
    """
    content = moment['content']
    qq = moment['qq']
    time = moment['time']
    
    # 过滤垃圾信息
    is_pass, info = filter_and_extract(content)
    
    if not is_pass:
        print(f"🗑️  过滤垃圾信息：{content[:30]}...")
        return False
    
    # 补充信息
    info['source'] = f'QQ空间（{qq}）'
    info['title'] = content[:50]  # 标题取前50字符
    info['time'] = time
    info['qq'] = qq
    
    # 推送到QQ邮箱
    if info['is_important']:
        print(f"🚨 推送重要信息：{info['title']}")
        push_immediate(info)
    else:
        print(f"📝 归档普通信息：{info['title']}")
        save_to_archive(info)
    
    return True

def save_to_archive(info):
    """
    保存信息到归档
    :param info: 信息字典
    """
    # 方案1：保存到JSON文件
    with open('D:/hfut_info_monitor/data/hfut_archive.json', 'a', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False)
        f.write('\n')
    
    # 方案2：保存到SQLite数据库（可选）
    # import sqlite3
    # conn = sqlite3.connect('D:/hfut_info_monitor/data/hfut_archive.db')
    # ...

def monitor_qzone():
    """
    主监控函数
    """
    print("=" * 60)
    print("合工大信息监控系统 - QQ空间监控")
    print("=" * 60)
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    all_moments = []
    
    for qq in QQ_ACCOUNTS:
        print(f"📋 处理QQ号：{qq}")
        
        # Step 1：获取空间动态
        moments = get_qzone_moments(qq)
        
        if not moments:
            print(f"  未找到动态")
            continue
        
        print(f"  找到 {len(moments)} 条动态")
        
        # Step 2：处理每条动态
        for moment in moments:
            success = process_moment(moment)
            if success:
                all_moments.append(moment)
            
            # 避免频率限制
            time.sleep(1)
        
        print()
    
    # Step 3：生成每日摘要（如果有动态）
    if all_moments:
        print(f"📊 共处理 {len(all_moments)} 条动态")
        # 这里可以调用 push_daily_digest(all_moments)
    
    print()
    print(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

def setup_gocqhttp():
    """
    配置 go-cqhttp（QQ机器人框架）
    提供配置说明
    """
    print("📋 go-cqhttp 配置说明")
    print("=" * 60)
    print("1. 下载 go-cqhttp")
    print("   网址：https://github.com/Mrs4s/go-cqhttp/releases")
    print("   文件：go-cqhttp_windows_amd64.exe")
    print()
    print("2. 初始化配置")
    print("   运行：go-cqhttp.exe")
    print("   自动生成 config.yml")
    print()
    print("3. 修改 config.yml")
    print("   account:")
    print("     uin: 你的QQ号")
    print("     password: '你的密码'")
    print()
    print("4. 重新运行")
    print("   ./go-cqhttp.exe")
    print()
    print("5. 监听QQ空间动态")
    print("   使用 OneBot v11 协议")
    print("   监听事件：notice.friend_increase")
    print("=" * 60)

if __name__ == '__main__':
    # 测试
    print("📧 测试QQ空间监控模块...")
    print()
    print("⚠️  注意：需要配置 go-cqhttp 才能实际使用")
    print("   运行 setup_gocqhttp() 查看配置说明")
    print()
    
    # 运行模拟测试
    monitor_qzone()
