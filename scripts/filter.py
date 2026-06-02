"""
垃圾信息过滤模块
功能：过滤广告、推广、垃圾信息
"""

import re
import json
from datetime import datetime

# 加载配置
with open('D:/hfut_info_monitor/config/config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

# 垃圾信息关键词
GARBAGE_KEYWORDS = CONFIG['ai_secretary']['filter_rules']['garbage_keywords']

# 重要信息关键词
IMPORTANT_KEYWORDS = CONFIG['ai_secretary']['filter_rules']['important_keywords']

# 垃圾信息正则表达式
GARBAGE_PATTERNS = [
    r'点击链接',
    r'扫码加群',
    r'https?://[^\s]+',  # 包含链接
    r'加群\d+',
    r'兼职\d+',
    r'日赚\d+',
]

def is_garbage(text):
    """
    判断文本是否是垃圾信息
    :param text: 待判断文本
    :return: True（垃圾信息）/ False（有价值信息）
    """
    if not text or len(text.strip()) == 0:
        return True
    
    # 规则1：关键词过滤
    for keyword in GARBAGE_KEYWORDS:
        if keyword in text:
            print(f"🗑️  垃圾信息（关键词）：{keyword}")
            return True
    
    # 规则2：正则表达式过滤
    for pattern in GARBAGE_PATTERNS:
        if re.search(pattern, text):
            print(f"🗑️  垃圾信息（正则）：{pattern}")
            return True
    
    # 规则3：重复内容过滤（需要外部调用者提供历史记录）
    # 这个规则在调用函数中实现
    
    return False

def is_important(text):
    """
    判断文本是否包含重要信息
    :param text: 待判断文本
    :return: True（重要信息）/ False（普通信息）
    """
    if not text:
        return False
    
    for keyword in IMPORTANT_KEYWORDS:
        if keyword in text:
            return True
    
    return False

def extract_key_info(text):
    """
    提取关键信息
    :param text: 原始文本
    :return: 关键信息字典
    """
    info = {
        'title': '',
        'time': '',
        'source': '',
        'content': text,
        'is_important': is_important(text),
        'keywords': []
    }
    
    # 提取标题（假设第一行是标题）
    lines = text.split('\n')
    if lines:
        info['title'] = lines[0][:50]  # 标题取前50字符
    
    # 提取时间（简单正则匹配）
    time_patterns = [
        r'\d{4}-\d{2}-\d{2}',
        r'\d{4}年\d{2}月\d{2}日',
        r'\d{2}-\d{2}',
    ]
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            info['time'] = match.group()
            break
    
    # 提取关键词（简单实现）
    for keyword in IMPORTANT_KEYWORDS:
        if keyword in text:
            info['keywords'].append(keyword)
    
    return info

def filter_and_extract(text):
    """
    过滤并提取信息
    :param text: 原始文本
    :return: (是否通过过滤, 关键信息字典)
    """
    if is_garbage(text):
        return False, None
    
    info = extract_key_info(text)
    return True, info

if __name__ == '__main__':
    # 测试
    test_texts = [
        "【通知】明天上午9点开会",
        "加群123456领取红包",
        "点击链接http://xxx.com广告",
        "关于2026年保研工作的通知",
    ]
    
    for text in test_texts:
        is_pass, info = filter_and_extract(text)
        if is_pass:
            print(f"✅ 通过：{text}")
            print(f"   关键信息：{info}")
        else:
            print(f"❌ 过滤：{text}")
