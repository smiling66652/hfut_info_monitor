"""
垃圾信息过滤模块
功能：过滤广告、推广、垃圾信息
- 延迟加载配置（避免 import 级联失败）
- 统一使用 logging
- 修复 URL 正则误判官网通知的问题
"""

import re
import json
import logging
from typing import Optional

logger = logging.getLogger('Filter')

# 延迟加载的配置和关键词（模块级默认值，import 时不会触发 I/O）
_GARBAGE_KEYWORDS: list[str] = []
_IMPORTANT_KEYWORDS: list[str] = []
_CONFIG_LOADED = False


def _ensure_config_loaded():
    """延迟加载配置（只在首次调用时执行 I/O）"""
    global _GARBAGE_KEYWORDS, _IMPORTANT_KEYWORDS, _CONFIG_LOADED
    
    if _CONFIG_LOADED:
        return
    
    try:
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, 'config', 'config.json')
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        _GARBAGE_KEYWORDS = config['ai_secretary']['filter_rules']['garbage_keywords']
        _IMPORTANT_KEYWORDS = config['ai_secretary']['filter_rules']['important_keywords']
        _CONFIG_LOADED = True
        logger.info("✅ 过滤规则配置加载成功")
    except (FileNotFoundError, KeyError) as e:
        logger.warning(f"⚠️ 过滤规则配置加载失败，使用空规则：{e}")
        _CONFIG_LOADED = True  # 标记为已尝试，避免重复尝试


# ====== 垃圾信息正则表达式（优化版）======
# 注意：移除了过于宽泛的 URL 正则，改为更精确的模式
GARBAGE_PATTERNS = [
    r'点击链接(领取|领取|注册|下载)',     # 点击链接+诱导动词
    r'扫码加群',                          # 扫码加群
    r'https?://[^\s]*(领红包|优惠券|注册|推广|邀请)',  # 链接+营销关键词
    r'加群\d+',                           # 加群+数字
    r'兼职\d+',                           # 兼职+数字
    r'日赚\d+',                           # 日赚+数字
]


def is_garbage(text: str) -> bool:
    """
    判断文本是否是垃圾信息
    :param text: 待判断文本
    :return: True（垃圾信息）/ False（有价值信息）
    """
    if not text or len(text.strip()) == 0:
        return True
    
    # 确保配置已加载
    _ensure_config_loaded()
    
    # 规则1：关键词过滤
    for keyword in _GARBAGE_KEYWORDS:
        if keyword in text:
            logger.debug(f"🗑️ 垃圾信息（关键词）：{keyword}")
            return True
    
    # 规则2：正则表达式过滤（优化版，减少误判）
    for pattern in GARBAGE_PATTERNS:
        if re.search(pattern, text):
            logger.debug(f"🗑️ 垃圾信息（正则）：{pattern}")
            return True
    
    return False


def is_important(text: str) -> bool:
    """
    判断文本是否包含重要信息
    :param text: 待判断文本
    :return: True（重要信息）/ False（普通信息）
    """
    if not text:
        return False
    
    _ensure_config_loaded()
    
    for keyword in _IMPORTANT_KEYWORDS:
        if keyword in text:
            return True
    
    return False


def extract_key_info(text: str) -> dict:
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
        info['title'] = lines[0][:50]
    
    # 提取时间（简单正则匹配，按优先级排序）
    time_patterns = [
        (r'\d{4}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2})?', '%Y年%m月%d日'),
        (r'\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?', '%Y-%m-%d'),
        (r'\d{2}-\d{2}(?:\s+\d{2}:\d{2})?', '%m-%d'),
    ]
    
    for pattern, fmt in time_patterns:
        match = re.search(pattern, text)
        if match:
            info['time'] = match.group()
            break
    
    # 提取关键词
    _ensure_config_loaded()
    for keyword in _IMPORTANT_KEYWORDS:
        if keyword in text:
            info['keywords'].append(keyword)
    
    return info


def filter_and_extract(text: str) -> tuple[bool, Optional[dict]]:
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
    # 配置日志用于测试
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
    
    test_texts = [
        "【通知】明天上午9点开会",
        "加群123456领取红包",
        "点击链接http://xxx.com/register 注册领取优惠",  # 应被过滤
        "关于2026年保研工作的通知，详情见 http://www.hfut.edu.cn/notice/123",  # 官网链接不应被过滤
        "【通知】请查看 http://mp.weixin.qq.com/s/xxx 最新公告",  # 微信公众号链接不应被过滤
    ]
    
    print("=" * 50)
    print("过滤器测试")
    print("=" * 50)
    
    for text in test_texts:
        is_pass, info = filter_and_extract(text)
        status = "✅ 通过" if is_pass else "❌ 过滤"
        print(f"\n{status}：{text[:40]}...")
        if info:
            print(f"   标题：{info['title']} | 重要：{info['is_important']} | 关键词：{info['keywords']}")
