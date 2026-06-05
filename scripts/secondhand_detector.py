"""
二手物品检测器 - SecondHandDetector
====================================

基于关键词匹配引擎检测群消息中的二手物品交易信息，
提取价格、自动分类，支持 strict/loose 两种检测模式。

配置来源: config.json 中的 secondhand_detection 字段

使用示例:
    from scripts.secondhand_detector import SecondHandDetector

    detector = SecondHandDetector(mode="loose")

    # 单条检测
    result = detector.detect("出闲置 iPad Air 5，几乎全新，￥2500，可小刀")
    print(result)  # {is_secondhand: True, confidence: 0.85, items: ["iPad"], ...}

    # 批量检测
    results = detector.detect_batch(messages)
    high_value = detector.get_high_value(messages, min_confidence=0.8)
"""

import json
import re
import logging
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger("HFUT_Monitor.SecondHandDetector")

# ==================== 默认关键词 ====================

DEFAULT_P0_KEYWORDS = ["出售", "转让", "出闲置", "低价出", "急出", "甩卖", "跳楼价"]
DEFAULT_P1_KEYWORDS = ["二手", "闲置", "99新", "几乎全新", "仅拆封"]
DEFAULT_P2_KEYWORDS = ["价格", "包邮", "自取", "面交", "可小刀", "联系方式"]
DEFAULT_PRICE_PATTERNS = [
    r"￥\s*(\d+(?:\.\d{1,2})?)",           # ￥50
    r"¥\s*(\d+(?:\.\d{1,2})?)",            # ¥50
    r"(\d+(?:\.\d{1,2})?)\s*元",            # 50元
    r"(\d+(?:\.\d{1,2})?)\s*r\b",          # 50r
    r"(\d+(?:\.\d{1,2})?)\s*块",            # 50块
    r"原价\s*(\d+(?:\.\d{1,2})?)\s*现价\s*(\d+(?:\.\d{1,2})?)",  # 原价100现价50
]
DEFAULT_ITEM_CATEGORIES = {
    "电子产品": ["手机", "电脑", "笔记本", "平板", "iPad", "耳机", "充电器", "显示器", "键盘",
            "鼠标", "主机", "显卡", "内存", "硬盘", "CPU", "固态", "SSD", "相机", "镜头"],
    "书籍教材": ["教材", "课本", "考研", "四六级", "参考书", "笔记", "真题", "辅导书",
            "习题集", "试卷", "词典", "英语书", "数学书"],
    "生活用品": ["台灯", "风扇", "收纳", "衣架", "水壶", "床上用品", "床帘", "蚊帐",
            "排插", "插线板", "小推车", "暖壶", "热水壶", "锅", "碗", "杯"],
    "服装鞋帽": ["衣服", "鞋子", "包", "帽子", "外套", "T恤", "衬衫", "裤子",
            "裙子", "羽绒服", "鞋", "运动鞋", "跑鞋"],
    "代购票务": ["代购", "门票", "电影票", "优惠券", "车票", "演出票", "二手票"],
    "其他": [],
}


# ==================== 工具函数 ====================

def _load_config() -> dict:
    """从 config.json 加载二手检测配置"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("secondhand_detection", {})
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("无法加载 config.json，使用默认配置")
        return {}


# ==================== 主类 ====================

class SecondHandDetector:
    """二手物品检测器

    通过多级关键词匹配 + 价格提取 + 物品分类，
    识别群消息中的二手物品交易信息。

    两种模式:
        strict - 仅匹配 P0 + P1 强信号关键词
        loose  - 匹配全部三级关键词
    """

    def __init__(
        self,
        mode: str = "loose",
        min_confidence: float = 0.6,
        p0_keywords: Optional[list[str]] = None,
        p1_keywords: Optional[list[str]] = None,
        p2_keywords: Optional[list[str]] = None,
        price_patterns: Optional[list[str]] = None,
        item_categories: Optional[dict[str, list[str]]] = None,
    ):
        """初始化检测器

        Args:
            mode: 检测模式 "strict" 或 "loose"
            min_confidence: 最低置信度阈值
            p0_keywords: P0 强信号关键词（覆盖配置文件）
            p1_keywords: P1 中信号关键词
            p2_keywords: P2 弱信号关键词
            price_patterns: 价格提取正则列表
            item_categories: 物品分类关键词映射
        """
        # 优先从 config.json 加载
        cfg = _load_config()

        self.mode = mode or cfg.get("mode", "loose")
        self.min_confidence = min_confidence or cfg.get("min_confidence", 0.6)

        self.p0_keywords = p0_keywords or cfg.get("p0_keywords", DEFAULT_P0_KEYWORDS)
        self.p1_keywords = p1_keywords or cfg.get("p1_keywords", DEFAULT_P1_KEYWORDS)
        self.p2_keywords = p2_keywords or cfg.get("p2_keywords", DEFAULT_P2_KEYWORDS)
        self.price_patterns = price_patterns or cfg.get("price_patterns", DEFAULT_PRICE_PATTERNS)
        self.item_categories = item_categories or cfg.get("item_categories", DEFAULT_ITEM_CATEGORIES)

        # precompile
        self._p0_set = set(self.p0_keywords)
        self._p1_set = set(self.p1_keywords)
        self._p2_set = set(self.p2_keywords)
        self._price_regexes = [re.compile(p) for p in self.price_patterns]

        # 物品关键词 -> 类别映射（扁平化）
        self._item_keyword_map: dict[str, str] = {}
        for category, keywords in self.item_categories.items():
            for kw in keywords:
                self._item_keyword_map[kw.lower()] = category

        logger.info(f"SecondHandDetector 初始化完成, mode={self.mode}, "
                     f"min_confidence={self.min_confidence}")

    # ---------- 公开方法 ----------

    def detect(self, text: Union[str, dict]) -> Optional[dict]:
        """检测单条消息是否为二手物品交易

        Args:
            text: 消息文本或标准化的消息 dict（含 "content" 字段）

        Returns:
            None 表示非二手消息；dict 包含:
                is_secondhand: True
                confidence: 置信度 0~1
                matched_keywords: 匹配到的关键词列表
                prices: 提取到的价格列表
                items: 识别到的物品列表
                category: 物品分类
        """
        content = self._extract_text(text)
        if not content or len(content.strip()) < 3:
            return None

        # 关键词匹配
        p0_matches = self._match_keywords(content, self._p0_set)
        p1_matches = self._match_keywords(content, self._p1_set)
        p2_matches = self._match_keywords(content, self._p2_set)

        # 价格提取（提前，mode 判断和置信度都需要）
        prices = self._extract_prices(content)

        # 根据模式决定有效匹配
        if self.mode == "strict":
            if not p0_matches and not p1_matches:
                return None
            matched = p0_matches + p1_matches
        else:  # loose
            if not p0_matches and not p1_matches:
                # 无 P0/P1 时，需要: 2+ P2 关键词 或 (1 P2 + 有价格)
                if len(p2_matches) < 2 and not (len(p2_matches) >= 1 and prices):
                    return None
            matched = p0_matches + p1_matches + p2_matches

        # 计算置信度
        confidence = self._calc_confidence(
            p0_count=len(p0_matches),
            p1_count=len(p1_matches),
            p2_count=len(p2_matches) if self.mode != "strict" else 0,
        )

        # 价格加分
        if prices:
            confidence = min(1.0, confidence + 0.08)

        # 物品识别
        items = self._extract_items(content)

        # 自动分类
        category = self._classify_item(content, items)

        # 物品加分
        if items:
            confidence = min(1.0, confidence + 0.05)

        # loose 模式加成：P2+价格组合有交易信号时大幅提升置信度
        if self.mode == "loose" and not p0_matches and not p1_matches:
            if p2_matches and prices:
                confidence = min(1.0, confidence + 0.35)

        # 检查最低置信度
        if confidence < self.min_confidence:
            return None

        return {
            "is_secondhand": True,
            "confidence": round(confidence, 3),
            "matched_keywords": matched,
            "prices": prices,
            "items": items,
            "category": category,
        }

    def detect_batch(self, messages: list) -> list[dict]:
        """批量检测多条消息

        Args:
            messages: 消息列表，每条为 dict（含 content）或纯文本

        Returns:
            每条消息的检测结果列表，非二手消息为 None
        """
        results = []
        for msg in messages:
            result = self.detect(msg)
            if result is not None:
                # 保留原始消息引用
                result["message"] = msg if isinstance(msg, dict) else {"content": msg}
            results.append(result)
        return results

    def get_high_value(self, messages: list, min_confidence: float = 0.7) -> list[dict]:
        """筛选高置信度二手消息

        Args:
            messages: 消息列表
            min_confidence: 最低置信度阈值，默认 0.7

        Returns:
            过滤后的高置信度检测结果列表（已排除 None）
        """
        results = self.detect_batch(messages)
        return [r for r in results if r and r["confidence"] >= min_confidence]

    # ---------- 内部方法 ----------

    @staticmethod
    def _extract_text(text: Union[str, dict]) -> str:
        """从输入中提取文本内容"""
        if isinstance(text, dict):
            return text.get("content", "")
        return str(text)

    @staticmethod
    def _match_keywords(text: str, keyword_set: set) -> list[str]:
        """在文本中匹配关键词集合，返回匹配到的关键词"""
        matched = []
        for kw in keyword_set:
            if kw in text:
                matched.append(kw)
        return matched

    @staticmethod
    def _calc_confidence(p0_count: int, p1_count: int, p2_count: int) -> float:
        """根据关键词匹配数量计算置信度

        评分规则:
            P0 首匹配 0.55，后续减半: 0.55 + 0.27 + 0.13 ...
            P1 首匹配 0.30，后续减半: 0.30 + 0.15 + 0.07 ...
            P2 首匹配 0.15，后续减半: 0.15 + 0.07 + 0.03 ...
            上限 1.0
        """
        confidence = 0.0

        for i in range(p0_count):
            confidence += 0.55 / (2 ** i)

        for i in range(p1_count):
            confidence += 0.30 / (2 ** i)

        for i in range(p2_count):
            confidence += 0.15 / (2 ** i)

        return min(1.0, confidence)

    def _extract_prices(self, text: str) -> list[str]:
        """提取文本中的价格信息

        返回格式如 ["50", "2500"] 的字符串列表
        兼容带捕获组和不带捕获组的正则模式。
        """
        import re as _re
        found = set()

        for regex in self._price_regexes:
            for match in regex.finditer(text):
                groups = match.groups()
                if len(groups) >= 2:
                    # "原价X现价Y" 模式，取现价部分
                    price_str = groups[1]
                elif len(groups) == 1:
                    price_str = groups[0]
                else:
                    # 无捕获组，从完整匹配中提取数字
                    full = match.group(0)
                    digits = _re.findall(r"\d+(?:\.\d{1,2})?", full)
                    if not digits:
                        continue
                    price_str = digits[0]

                # 过滤明显不合理的价格
                try:
                    val = float(price_str)
                    if 0.01 <= val <= 999999:
                        found.add(price_str.replace(".0", "") if price_str.endswith(".0") else price_str)
                except ValueError:
                    pass

        return sorted(found, key=lambda x: float(x))

    def _extract_items(self, text: str) -> list[str]:
        """从文本中提取可能物品关键词"""
        text_lower = text.lower()
        found = set()
        for kw in self._item_keyword_map:
            if kw in text_lower:
                found.add(kw)
        return sorted(found)

    def _classify_item(self, text: str, items: list[str]) -> str:
        """根据文本和已识别物品判断类别"""
        if not items:
            # 直接用文本匹配关键词
            text_lower = text.lower()
            for kw, category in self._item_keyword_map.items():
                if kw in text_lower:
                    return category
            return "其他"

        # 统计各类别命中数
        category_hits: dict[str, int] = {}
        for item in items:
            cat = self._item_keyword_map.get(item, "其他")
            category_hits[cat] = category_hits.get(cat, 0) + 1

        # 返回命中最多的类别
        return max(category_hits, key=category_hits.get)


# ==================== 自测 ====================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    detector = SecondHandDetector(mode="loose")

    # 测试用例
    test_cases = [
        ("出闲置 iPad Air 5，几乎全新，￥2500，可小刀", True),
        ("原价400现价200出售机械原理教材", True),
        ("转让一张明天电影票 50r 面交", True),
        ("急出 iPhone 15 Pro 128G 黑色 99新 5500元", True),
        ("出闲置台灯和风扇，30块打包带走", True),
        ("今天天气真好", False),
        ("老师说明天交作业", False),
        ("求购一本高等数学教材", False),   # 求购非出售信号
        ("这个50块钱，要的联系我", False),   # 口语化表述无配置关键词
        ("50元包邮出几本四六级真题", True),   # 包邮+价格→loose模式放行
    ]

    print("=" * 60)
    print("SecondHandDetector 自测")
    print("=" * 60)

    for text, expected in test_cases:
        result = detector.detect(text)
        is_detected = result is not None
        status = "✓" if is_detected == expected else "✗"
        print(f"\n{status} 文本: {text[:50]}...")
        print(f"  期望={expected}, 检测={'二手' if is_detected else '非二手'}")
        if result:
            print(f"  置信度: {result['confidence']} | 分类: {result['category']}")
            print(f"  关键词: {result['matched_keywords']}")
            print(f"  价格: {result['prices']}")
            print(f"  物品: {result['items']}")
