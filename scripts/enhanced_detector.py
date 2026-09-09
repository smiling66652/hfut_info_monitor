"""
增强型智能检测器 - EnhancedDetector
=====================================

三类检测器整合，在现有 filter.py / secondhand_detector.py 基础上扩展：

    CompetitionDetector  - 创业/比赛/保研机会检测
    SecondHandDetector   - 二手物品检测（复用已有模块并增强）
    UsefulInfoDetector   - 一般有价值信息检测

主入口类 EnhancedDetector 统一调度三类检测器，
输出标准化结果供 daily_digest_v2 消费。

使用示例:
    from scripts.enhanced_detector import EnhancedDetector

    detector = EnhancedDetector()

    results = detector.detect({"content": "挑战杯报名通知，截止时间3月15日",
                               "source": "qq_group", "group_id": "743058932"})
    # results = [{"category": "competition", "priority": "HIGH", ...}, ...]

    batch = detector.detect_batch(messages)
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger("HFUT_Monitor.EnhancedDetector")


# ==================== 关键词体系 ====================

COMPETITION_KEYWORDS = [
    "比赛", "竞赛", "报名", "大创", "挑战杯", "互联网+", "互联网＋",
    "数学建模", "电赛", "机器人", "智能车", "机械创新", "结构设计",
    "保研", "推免", "夏令营", "实验室招新", "导师招生", "科创",
    "创新创业", "科研", "论文", "答辩", "国赛", "省赛", "校赛",
    "立项", "结题", "项目申报", "学科竞赛", "机械设计",
    "工程训练", "创新大赛", "节能减排", "程序大赛", "程序设计",
    "电子设计", "物理竞赛", "化学竞赛", "英语竞赛", "演讲比赛",
    "辩论赛", "职业规划", "三创赛",
]

SECONDHAND_KEYWORDS = [
    "出售", "转让", "出闲置", "低价出", "急出", "甩卖", "二手", "闲置",
    "99新", "几乎全新", "仅拆封", "包邮", "自取", "面交", "可小刀",
    "价格", "降价", "便宜出", "白菜价", "跳楼价", "亏本出",
    "换新出", "多余出", "毕业出", "离校出", "搬家出",
]

HIGH_VALUE_KEYWORDS = [
    "手机", "电脑", "笔记本", "平板", "iPad", "相机", "显卡",
    "显示器", "主机", "iPhone", "MacBook", "ThinkPad",
    "AirPods", "Apple Watch", "单反", "微单", "镜头",
    "投影仪", "打印机", "音响", "耳机", "智能手表",
]

USEFUL_INFO_KEYWORDS = [
    "讲座", "学术报告", "实习", "招聘", "志愿者", "活动", "考试",
    "通知", "公告", "宣讲会", "交流会", "培训", "辅导",
    "经验分享", "干货", "资料", "资源", "教程",
]

# 结构化信息提取模式
DEADLINE_PATTERNS = [
    r"截止时间[：:]\s*(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日号]?)",
    r"截止日期[：:]\s*(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日号]?)",
    r"截止[：:]\s*(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日号]?)",
    r"报名时间[：:]\s*(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日号]?)",
    r"ddl[：:]\s*(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日号]?)",
    r"(\d{1,2}月\d{1,2}[日号])\s*截止",
    r"截止[：:]\s*(\d{1,2}[-./]\d{1,2})",
]

REGISTRATION_PATTERNS = [
    r"报名方式[：:]\s*(.+)",
    r"报名链接[：:]\s*(https?://\S+)",
    r"参赛方式[：:]\s*(.+)",
    r"报名邮箱[：:]\s*(\S+@\S+)",
    r"填写问卷[：:]\s*(https?://\S+)",
]

REQUIREMENT_PATTERNS = [
    r"参赛要求[：:]\s*(.+)",
    r"参赛对象[：:]\s*(.+)",
    r"面向[：:]\s*(.+)",
]


def _load_enhanced_config() -> dict:
    """从 config.json 加载增强检测配置"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("enhanced_detection", {})
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("无法加载 config.json enhanced_detection 配置，使用默认值")
        return {}


# ==================== CompetitionDetector ====================

class CompetitionDetector:
    """创业/比赛/保研机会检测器

    检测 QQ 群/空间/公众号消息中的竞赛报名、大创、保研推免、
    实验室招新等信息，识别截止时间、报名方式等结构化字段。

    Attributes:
        keywords: 竞赛相关关键词列表
        priority_groups: 高优先级群号集合（学术竞赛类群）
        deadline_patterns: 截止时间正则列表
        registration_patterns: 报名方式正则列表
        requirement_patterns: 参赛要求正则列表
    """

    def __init__(
        self,
        keywords: Optional[list[str]] = None,
        priority_groups: Optional[list[str]] = None,
    ):
        """初始化竞赛检测器

        Args:
            keywords: 自定义关键词列表，默认从 config.json 加载
            priority_groups: 高优先级群号列表
        """
        cfg = _load_enhanced_config().get("competition", {})

        self.keywords: list[str] = (
            keywords
            or cfg.get("keywords")
            or COMPETITION_KEYWORDS
        )
        self.priority_groups: set[str] = set(
            priority_groups
            or cfg.get("priority_groups", [])
        )

        # 预编译正则
        self._deadline_re = [re.compile(p) for p in DEADLINE_PATTERNS]
        self._registration_re = [re.compile(p) for p in REGISTRATION_PATTERNS]
        self._requirement_re = [re.compile(p) for p in REQUIREMENT_PATTERNS]

        self._kw_set = set(self.keywords)

        logger.info(
            "CompetitionDetector 初始化完成, keywords=%d, priority_groups=%d",
            len(self.keywords),
            len(self.priority_groups),
        )

    def detect(self, text: Union[str, dict]) -> Optional[dict]:
        """检测单条消息是否为竞赛/比赛/保研相关信息

        Args:
            text: 消息文本或标准化 dict（含 content、source、group_id 等）

        Returns:
            None 表示不相关；dict 包含类别、优先级、结构化信息等
        """
        content = self._extract_text(text)
        if not content or len(content.strip()) < 4:
            return None

        # 关键词匹配
        matched_kws = [kw for kw in self._kw_set if kw in content]
        if not matched_kws:
            return None

        # 提取结构化信息
        deadline = self._extract_deadline(content)
        registration = self._extract_registration(content)
        requirements = self._extract_requirements(content)

        # 计算置信度（基于匹配关键词数量和强度）
        strong_kws = {"保研", "推免", "夏令营", "挑战杯", "大创", "竞赛", "报名"}
        strong_matches = [kw for kw in matched_kws if kw in strong_kws]

        confidence = min(1.0, len(strong_matches) * 0.25 + len(matched_kws) * 0.08)

        # 有截止时间加分
        if deadline:
            confidence = min(1.0, confidence + 0.15)
        if registration:
            confidence = min(1.0, confidence + 0.10)

        # 优先级判定
        is_high_priority = False
        if isinstance(text, dict):
            group_id = text.get("group_id", "")
            if group_id and str(group_id) in self.priority_groups:
                is_high_priority = True
                confidence = min(1.0, confidence + 0.05)

        priority = "HIGH" if (confidence >= 0.5 or is_high_priority) else "MEDIUM"

        return {
            "category": "competition",
            "priority": priority,
            "confidence": round(confidence, 3),
            "matched_keywords": matched_kws,
            "deadline": deadline,
            "registration": registration,
            "requirements": requirements,
        }

    def detect_batch(self, messages: list) -> list[Optional[dict]]:
        """批量检测多条消息"""
        return [self.detect(msg) for msg in messages]

    # ---------- 内部方法 ----------

    @staticmethod
    def _extract_text(text: Union[str, dict]) -> str:
        """从输入中提取文本内容"""
        if isinstance(text, dict):
            return text.get("content", "") or text.get("text", "")
        return str(text)

    def _extract_deadline(self, text: str) -> Optional[str]:
        """提取截止时间"""
        for regex in self._deadline_re:
            match = regex.search(text)
            if match:
                return match.group(1).strip()
        return None

    def _extract_registration(self, text: str) -> Optional[str]:
        """提取报名方式"""
        for regex in self._registration_re:
            match = regex.search(text)
            if match:
                return match.group(1).strip()[:200]
        return None

    def _extract_requirements(self, text: str) -> Optional[str]:
        """提取参赛要求"""
        for regex in self._requirement_re:
            match = regex.search(text)
            if match:
                return match.group(1).strip()[:200]
        return None


# ==================== SecondHandDetector (增强版) ====================

class SecondHandDetector:
    """二手物品检测器（增强版）

    在现有 SecondHandDetector 的基础上增加：
    - 高价值物品识别（电子产品标记）
    - 二手交易群高敏感度模式
    - 价格区间提取

    内部委托给现有的 SecondHandDetector 做基础检测，
    本类只负责增强逻辑。
    """

    def __init__(
        self,
        highlight_groups: Optional[list[str]] = None,
        high_value_keywords: Optional[list[str]] = None,
    ):
        """初始化增强二手检测器

        Args:
            highlight_groups: 二手交易群号列表（高敏感度）
            high_value_keywords: 高价值物品关键词
        """
        cfg = _load_enhanced_config().get("secondhand", {})

        self.highlight_groups: set[str] = set(
            highlight_groups
            or cfg.get("highlight_groups", [])
        )
        self.high_value_keywords: list[str] = (
            high_value_keywords
            or cfg.get("high_value_keywords")
            or HIGH_VALUE_KEYWORDS
        )
        self._hv_set = set(self.high_value_keywords)

        # 延迟加载基础检测器（避免 import 循环）
        self._base_detector = None

        logger.info(
            "SecondHandDetector(增强) 初始化完成, highlight_groups=%d, hv_keywords=%d",
            len(self.highlight_groups),
            len(self.high_value_keywords),
        )

    @property
    def base_detector(self):
        """延迟加载基础二手检测器"""
        if self._base_detector is None:
            try:
                # 动态导入，避免循环依赖
                import sys
                _scripts_dir = str(Path(__file__).resolve().parent)
                if _scripts_dir not in sys.path:
                    sys.path.insert(0, _scripts_dir)

                from secondhand_detector import SecondHandDetector as _Base

                self._base_detector = _Base(mode="loose")
                logger.info("基础 SecondHandDetector 加载成功")
            except ImportError as e:
                logger.error("无法导入基础 SecondHandDetector: %s", e)
                self._base_detector = None
        return self._base_detector

    def detect(self, text: Union[str, dict]) -> Optional[dict]:
        """检测单条消息是否为二手物品交易（增强版）

        Args:
            text: 消息文本或标准化 dict

        Returns:
            None 或包含增强字段的检测结果 dict
        """
        content = self._extract_text(text)
        if not content or len(content.strip()) < 3:
            return None

        # 调用基础检测器
        base = self.base_detector
        if base is None:
            return None

        result = base.detect(text)
        if result is None:
            return None

        # --- 增强逻辑 ---

        # 1. 高价值物品识别
        high_value_items = [
            item for item in result.get("items", [])
            if item.lower() in self._hv_set
        ]
        # 也用文本直接匹配高价值关键词
        text_lower = content.lower()
        for hv_kw in self._hv_set:
            if hv_kw.lower() in text_lower and hv_kw not in high_value_items:
                high_value_items.append(hv_kw)

        is_high_value = len(high_value_items) > 0

        # 2. 二手交易群加成
        is_highlight_group = False
        if isinstance(text, dict):
            group_id = str(text.get("group_id", "") or "")
            if group_id and group_id in self.highlight_groups:
                is_highlight_group = True
                # 交易群内降低检测门槛
                result["confidence"] = min(1.0, result["confidence"] + 0.10)

        # 3. 价值排序分数（用于后续摘要排序）
        prices = result.get("prices", [])
        max_price = 0.0
        try:
            if prices:
                max_price = max(float(p) for p in prices)
        except (ValueError, TypeError):
            pass

        # 优先级：高价值物品 → HIGH，否则按置信度判定
        if is_high_value or max_price >= 500:
            priority = "HIGH"
        elif result["confidence"] >= 0.7:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        return {
            "category": "secondhand",
            "priority": priority,
            "confidence": round(result["confidence"], 3),
            "matched_keywords": result.get("matched_keywords", []),
            "prices": prices,
            "items": result.get("items", []),
            "item_category": result.get("category", "其他"),
            "is_high_value": is_high_value,
            "high_value_items": high_value_items,
            "is_highlight_group": is_highlight_group,
            "max_price": max_price,
        }

    def detect_batch(self, messages: list) -> list[Optional[dict]]:
        """批量检测多条消息"""
        return [self.detect(msg) for msg in messages]

    def get_high_value(self, messages: list) -> list[dict]:
        """筛选高价值二手消息"""
        results = self.detect_batch(messages)
        return [r for r in results if r and r.get("is_high_value")]

    @staticmethod
    def _extract_text(text: Union[str, dict]) -> str:
        """从输入中提取文本内容"""
        if isinstance(text, dict):
            return text.get("content", "") or text.get("text", "")
        return str(text)


# ==================== UsefulInfoDetector ====================

class UsefulInfoDetector:
    """一般有价值信息检测器

    检测讲座通知、学术活动、实习招聘、志愿活动、
    考试通知等对学生的有用信息。
    """

    def __init__(self, keywords: Optional[list[str]] = None):
        """初始化有用信息检测器

        Args:
            keywords: 自定义关键词列表
        """
        cfg = _load_enhanced_config().get("useful_info", {})

        self.keywords: list[str] = (
            keywords
            or cfg.get("keywords")
            or USEFUL_INFO_KEYWORDS
        )
        self._kw_set = set(self.keywords)

        # 子类别映射
        self._subcategory_map: dict[str, str] = {
            "讲座": "学术讲座",
            "学术报告": "学术讲座",
            "宣讲会": "招聘宣讲",
            "实习": "实习机会",
            "招聘": "招聘信息",
            "志愿者": "志愿活动",
            "活动": "校园活动",
            "考试": "考试通知",
            "通知": "重要通知",
            "培训": "技能培训",
            "交流会": "交流活动",
            "经验分享": "经验分享",
            "干货": "学习资源",
            "资料": "学习资源",
            "资源": "学习资源",
            "教程": "学习资源",
            "辅导": "学习辅导",
        }

        logger.info(
            "UsefulInfoDetector 初始化完成, keywords=%d",
            len(self.keywords),
        )

    def detect(self, text: Union[str, dict]) -> Optional[dict]:
        """检测单条消息是否有价值

        Args:
            text: 消息文本或标准化 dict

        Returns:
            None 或检测结果 dict
        """
        content = self._extract_text(text)
        if not content or len(content.strip()) < 4:
            return None

        # 关键词匹配
        matched_kws = [kw for kw in self._kw_set if kw in content]
        if not matched_kws:
            return None

        # 确认不是竞赛/二手（避免重复分类）
        # 简单检查：如果有很强的竞赛/二手信号则跳过
        strong_competition = {"保研", "推免", "夏令营", "大创", "竞赛", "挑战杯", "电赛"}
        strong_secondhand = {"出售", "转让", "出闲置", "二手", "闲置"}
        if any(kw in content for kw in strong_competition):
            return None  # 留给 CompetitionDetector
        if any(kw in content for kw in strong_secondhand):
            return None  # 留给 SecondHandDetector

        # 判定子类别
        subcategory = "其他信息"
        for kw in matched_kws:
            if kw in self._subcategory_map:
                subcategory = self._subcategory_map[kw]
                break

        confidence = min(1.0, len(matched_kws) * 0.15)
        priority = "MEDIUM" if confidence >= 0.4 else "LOW"

        return {
            "category": "useful_info",
            "priority": priority,
            "confidence": round(confidence, 3),
            "matched_keywords": matched_kws,
            "subcategory": subcategory,
        }

    def detect_batch(self, messages: list) -> list[Optional[dict]]:
        """批量检测多条消息"""
        return [self.detect(msg) for msg in messages]

    @staticmethod
    def _extract_text(text: Union[str, dict]) -> str:
        """从输入中提取文本内容"""
        if isinstance(text, dict):
            return text.get("content", "") or text.get("text", "")
        return str(text)


# ==================== EnhancedDetector（主入口） ====================

class EnhancedDetector:
    """增强型智能检测器（统一入口）

    整合三类检测器，按优先级依次检测：
    1. CompetitionDetector — 竞赛/保研（最高优先级）
    2. SecondHandDetector — 二手物品
    3. UsefulInfoDetector — 一般有价值信息

    每条消息最多匹配一个类别（防止重复分类）。
    """

    def __init__(self, config: Optional[dict] = None):
        """初始化增强检测器

        Args:
            config: 可选的配置字典，覆盖 config.json
        """
        self.config = config or {}
        self.competition_detector = CompetitionDetector()
        self.secondhand_detector = SecondHandDetector()
        self.useful_info_detector = UsefulInfoDetector()

        logger.info("EnhancedDetector 初始化完成（3 类检测器就绪）")

    def detect(self, text: Union[str, dict]) -> list[dict]:
        """检测单条消息，返回所有匹配的类别结果

        Args:
            text: 消息文本或标准化 dict

        Returns:
            检测结果列表（通常最多 1 个），每条为 dict
        """
        results: list[dict] = []

        # 按优先级依次检测
        competition_result = self.competition_detector.detect(text)
        if competition_result:
            results.append(competition_result)
            return results  # 竞赛优先，命中即返回

        secondhand_result = self.secondhand_detector.detect(text)
        if secondhand_result:
            results.append(secondhand_result)
            return results

        useful_result = self.useful_info_detector.detect(text)
        if useful_result:
            results.append(useful_result)

        return results

    def detect_batch(self, messages: list) -> list[dict]:
        """批量检测多条消息

        Args:
            messages: 消息列表

        Returns:
            所有检测到的非 None 结果列表，每条附加原始消息引用
        """
        all_results: list[dict] = []
        for msg in messages:
            detection_results = self.detect(msg)
            for result in detection_results:
                # 附加原始消息引用
                if isinstance(msg, dict):
                    result["message"] = msg
                else:
                    result["message"] = {"content": msg}
                all_results.append(result)
        return all_results

    def detect_and_categorize(
        self, messages: list
    ) -> dict[str, list[dict]]:
        """批量检测并按类别分组

        Args:
            messages: 消息列表

        Returns:
            {"competition": [...], "secondhand": [...], "useful_info": [...]}
        """
        categorized: dict[str, list[dict]] = {
            "competition": [],
            "secondhand": [],
            "useful_info": [],
        }

        results = self.detect_batch(messages)
        for result in results:
            cat = result.get("category", "")
            if cat in categorized:
                categorized[cat].append(result)

        return categorized

    def get_highlights(
        self, messages: list, min_confidence: float = 0.5
    ) -> list[dict]:
        """获取高置信度的亮点消息

        Args:
            messages: 消息列表
            min_confidence: 最低置信度阈值

        Returns:
            按优先级排序的高置信度结果
        """
        results = self.detect_batch(messages)
        highlights = [r for r in results if r.get("confidence", 0) >= min_confidence]

        # 排序：priority(HIGH>MEDIUM>LOW) → confidence desc
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        highlights.sort(
            key=lambda r: (
                priority_order.get(r.get("priority", "LOW"), 3),
                -r.get("confidence", 0),
            )
        )
        return highlights

    def get_status(self) -> dict:
        """获取检测器状态（供 main_controller status 模式使用）"""
        return {
            "competition_detector": {
                "keywords": len(self.competition_detector.keywords),
                "priority_groups": len(self.competition_detector.priority_groups),
            },
            "secondhand_detector": {
                "highlight_groups": len(self.secondhand_detector.highlight_groups),
                "high_value_keywords": len(self.secondhand_detector.high_value_keywords),
                "base_detector_loaded": self.secondhand_detector.base_detector is not None,
            },
            "useful_info_detector": {
                "keywords": len(self.useful_info_detector.keywords),
            },
        }


# ==================== 自测 ====================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    detector = EnhancedDetector()

    test_cases = [
        # 竞赛类
        {
            "content": "挑战杯报名通知，截止时间：2026年3月15日，报名方式：填写问卷https://example.com，参赛要求：全校本科生",
            "source": "qq_group",
            "group_id": "743058932",
        },
        {
            "content": "2026年保研夏令营开始报名了，请有意向的同学关注教务处通知",
            "source": "qq_group",
            "group_id": "1095778372",
        },
        # 二手类
        {
            "content": "出闲置 iPad Air 5，几乎全新，￥2500，可小刀，面交",
            "source": "qq_group",
            "group_id": "703671976",
        },
        {
            "content": "毕业出 MacBook Pro M3 16G 512G 99新 8000元",
            "source": "qq_group",
            "group_id": "625100963",
        },
        # 有用信息类
        {
            "content": "【讲座通知】明晚7点在学术报告厅有关于人工智能的学术报告",
            "source": "qq_group",
            "group_id": "696122330",
        },
        # 非相关
        {
            "content": "今天天气真好",
            "source": "qq_group",
            "group_id": "696122330",
        },
    ]

    print("=" * 70)
    print("EnhancedDetector 自测")
    print("=" * 70)

    for i, msg in enumerate(test_cases, 1):
        results = detector.detect(msg)
        print(f"\n[{i}] {msg['content'][:50]}...")
        if results:
            for r in results:
                print(f"    类别: {r['category']} | 优先级: {r['priority']} | "
                      f"置信度: {r['confidence']}")
                if r.get("deadline"):
                    print(f"    截止时间: {r['deadline']}")
                if r.get("is_high_value"):
                    print(f"    高价值物品: {r.get('high_value_items')}")
                print(f"    匹配关键词: {r.get('matched_keywords', [])[:5]}")
        else:
            print("    ❌ 未检测到有价值信息")

    print("\n" + "=" * 70)
    print("检测器状态:")
    status = detector.get_status()
    for name, info in status.items():
        print(f"  {name}: {info}")
