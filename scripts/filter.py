"""
垃圾信息过滤模块
功能：过滤广告、推广、垃圾信息，扩充关键词体系
- 延迟加载配置（避免 import 级联失败）
- 统一使用 logging
- 修复 URL 正则误判官网通知的问题
- v4.1: 新增竞赛/二手关键词体系 + is_competition() + get_category()
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

# ====== 竞赛/比赛关键词 ======
COMPETITION_KEYWORDS: list[str] = [
    "比赛", "竞赛", "报名", "大创", "挑战杯", "互联网+", "互联网＋",
    "数学建模", "电赛", "机器人", "智能车", "机械创新", "结构设计",
    "保研", "推免", "夏令营", "实验室招新", "导师招生", "科创",
    "创新创业", "科研", "论文", "答辩", "国赛", "省赛", "校赛",
    "立项", "结题", "项目申报", "学科竞赛", "机械设计",
    "工程训练", "创新大赛", "节能减排", "电子设计",
]

# ====== 二手物品关键词 ======
SECONDHAND_KEYWORDS: list[str] = [
    "出售", "转让", "出闲置", "低价出", "急出", "甩卖", "二手", "闲置",
    "99新", "几乎全新", "仅拆封", "包邮", "自取", "面交", "可小刀",
    "价格", "降价", "便宜出", "白菜价", "跳楼价", "亏本出",
    "换新出", "多余出", "毕业出", "离校出", "搬家出",
    "出 ", "出iPad", "出iphone", "出电脑", "出笔记本", "出手机",
    "出平板", "出相机", "出耳机", "出显卡", "出显示器",
]

# ====== 二手物品正则（兜底识别"出+物品名"模式）======
_SECONDHAND_REGEX = re.compile(
    r'(?:^|[\s，,。！!])出\s*[A-Za-z0-9\u4e00-\u9fff]{1,20}',
    re.IGNORECASE
)


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


def is_competition(text: str) -> bool:
    """
    判断文本是否包含竞赛/比赛/保研相关信息

    在现有 is_important() 基础上，增加竞赛专属关键词匹配。

    :param text: 待判断文本
    :return: True（竞赛相关信息）/ False
    """
    if not text:
        return False

    # 先排除垃圾信息
    if is_garbage(text):
        return False

    for keyword in COMPETITION_KEYWORDS:
        if keyword in text:
            logger.debug(f"🏆 竞赛信息（关键词）：{keyword}")
            return True

    return False


def is_secondhand(text: str) -> bool:
    """
    判断文本是否包含二手物品交易相关信息

    :param text: 待判断文本
    :return: True（二手交易相关信息）/ False
    """
    if not text:
        return False

    if is_garbage(text):
        return False

    # 关键词匹配
    text_lower = text.lower()
    for keyword in SECONDHAND_KEYWORDS:
        if keyword.lower() in text_lower:
            logger.debug(f"🛒 二手信息（关键词）：{keyword}")
            return True

    # 正则兜底：匹配"出 + 物品名"模式（如"出iPad Pro 256G"）
    if _SECONDHAND_REGEX.search(text):
        logger.debug("🛒 二手信息（正则）：出+物品名模式")
        return True

    return False


def get_category(text: str) -> str:
    """
    获取文本的分类类别

    按优先级判定：
    1. 垃圾信息 → "garbage"
    2. 竞赛/比赛/保研 → "competition"
    3. 二手交易 → "secondhand"
    4. 重要通知（含 is_important 关键词） → "notice"
    5. 其他 → "general"

    :param text: 待分类文本
    :return: 类别字符串："competition" / "secondhand" / "notice" / "garbage" / "general"
    """
    if not text:
        return "general"

    if is_garbage(text):
        return "garbage"

    if is_competition(text):
        return "competition"

    if is_secondhand(text):
        return "secondhand"

    if is_important(text):
        return "notice"

    return "general"


if __name__ == '__main__':
    # 配置日志用于测试
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
    
    test_texts = [
        "【通知】明天上午9点开会",
        "加群123456领取红包",
        "点击链接http://xxx.com/register 注册领取优惠",  # 应被过滤
        "关于2026年保研工作的通知，详情见 http://www.hfut.edu.cn/notice/123",  # 官网链接不应被过滤
        "【通知】请查看 http://mp.weixin.qq.com/s/xxx 最新公告",  # 微信公众号链接不应被过滤
        "挑战杯报名通知，截止时间2026年3月15日",  # 竞赛类
        "出闲置 iPad Air 5，几乎全新，￥2500",    # 二手类
        "今天天气真好",                            # 普通信息
    ]
    
    print("=" * 50)
    print("过滤器测试")
    print("=" * 50)
    
    for text in test_texts:
        is_pass, info = filter_and_extract(text)
        status = "✅ 通过" if is_pass else "❌ 过滤"
        category = get_category(text)
        print(f"\n{status}：{text[:40]}...")
        print(f"   分类：{category} | 竞赛：{is_competition(text)} | 二手：{is_secondhand(text)}")
        if info:
            print(f"   标题：{info['title']} | 重要：{info['is_important']} | 关键词：{info['keywords']}")
