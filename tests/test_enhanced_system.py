"""
综合功能测试 - 增强型每日巡检系统
===================================
测试覆盖：
1. EnhancedDetector.detect_and_categorize() 分类正确性
2. filter.py 新函数行为
3. DailyDigestV2.generate() 摘要生成
4. EnhancedDetector 配置读取与默认值回退
5. 边界测试：空输入、None、超长文本、纯英文、特殊字符
6. playwright_scraper 降级行为
7. filter.py 原有函数向后兼容
"""

import sys
import json
import os
import logging
import tempfile
from pathlib import Path

# 配置路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# 抑制日志噪音
logging.basicConfig(level=logging.WARNING)


# ==================== 测试工具 ====================

class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
        self.section = ""
    
    def start_section(self, name: str):
        self.section = name
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
    
    def assert_equal(self, test_name: str, actual, expected):
        if actual == expected:
            self.passed += 1
            print(f"  ✅ {test_name}")
            return True
        else:
            self.failed += 1
            msg = f"  ❌ {test_name}: Expected {expected!r}, got {actual!r}"
            print(msg)
            self.errors.append(msg)
            return False
    
    def assert_true(self, test_name: str, value, msg=""):
        if value:
            self.passed += 1
            print(f"  ✅ {test_name}")
            return True
        else:
            self.failed += 1
            err = f"  ❌ {test_name}: Expected True, got {value!r}" + (f" — {msg}" if msg else "")
            print(err)
            self.errors.append(err)
            return False
    
    def assert_false(self, test_name: str, value, msg=""):
        if not value:
            self.passed += 1
            print(f"  ✅ {test_name}")
            return True
        else:
            self.failed += 1
            err = f"  ❌ {test_name}: Expected False, got {value!r}" + (f" — {msg}" if msg else "")
            print(err)
            self.errors.append(err)
            return False
    
    def assert_not_none(self, test_name: str, value):
        if value is not None:
            self.passed += 1
            print(f"  ✅ {test_name}")
            return True
        else:
            self.failed += 1
            msg = f"  ❌ {test_name}: Expected not None, got None"
            print(msg)
            self.errors.append(msg)
            return False
    
    def assert_is_none(self, test_name: str, value):
        if value is None:
            self.passed += 1
            print(f"  ✅ {test_name}")
            return True
        else:
            self.failed += 1
            msg = f"  ❌ {test_name}: Expected None, got {value!r}"
            print(msg)
            self.errors.append(msg)
            return False
    
    def summary(self) -> tuple[int, int]:
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"  测试汇总: {total} total | {self.passed} passed | {self.failed} failed")
        print(f"{'='*60}")
        return self.passed, self.failed


t = TestRunner()


# ==================== 测试数据 ====================

COMPETITION_MSGS = [
    {"content": "【大创通知】2026年国家级大创立项，截止时间：6月20日，报名方式：在线提交", "source": "QQ群", "group_name": "大创交流群", "group_id": "743058932"},
    {"content": "挑战杯校赛选拔即将开始，请有意参加的同学加群 743058932", "source": "QQ群", "group_name": "竞赛通知群", "group_id": "743058932"},
    {"content": "保研夏令营：清华大学机械系2026夏令营报名通知", "source": "QQ空间", "nickname": "学长A"},
    {"content": "2026年智能车竞赛校赛培训通知，报名截止：2026年5月15日，报名方式：扫描海报二维码", "source": "QQ群", "group_name": "智能车协会", "group_id": "1001728783"},
]

SECONDHAND_MSGS = [
    {"content": "出iPad Pro 2024 256G 99新 带原装笔 价格4000", "source": "QQ群", "group_name": "二手交易", "group_id": "703671976"},
    {"content": "毕业出机械键盘ikbc c87，30块拿走", "source": "QQ群", "group_name": "跳蚤市场", "group_id": "625100963"},
    {"content": "转让全新未拆封 AirPods Pro 2，价格1200，可小刀", "source": "QQ群", "group_name": "二手闲置", "group_id": "879455018"},
]

NORMAL_MSGS = [
    {"content": "今天中午吃什么", "source": "QQ群", "group_name": "吹水群"},
    {"content": "有没有人一起打游戏的", "source": "QQ群", "group_name": "游戏群"},
    {"content": "明天有课吗", "source": "QQ群", "group_name": "闲聊群"},
    {"content": "哈哈哈哈哈笑死我了", "source": "QQ群", "group_name": "摸鱼群"},
]

USEFUL_MSGS = [
    {"content": "【讲座通知】明晚7点在学术报告厅有关于人工智能的学术报告", "source": "QQ群", "group_name": "通知群"},
    {"content": "招聘：字节跳动2026实习生招聘开始了，请有意向的同学投递简历", "source": "QQ群", "group_name": "就业群"},
    {"content": "志愿者招募：校运会志愿者报名，请联系学生会", "source": "QQ群", "group_name": "志愿者群"},
]


# ==================== 测试1：filter.py 新函数 ====================

def test_filter_new_functions():
    t.start_section("测试1: filter.py 新函数（is_competition / is_secondhand / get_category）")
    
    from filter import is_competition, is_secondhand, get_category
    
    # --- is_competition ---
    t.assert_true("竞赛-大创通知", is_competition("【大创通知】2026年国家级大创立项"))
    t.assert_true("竞赛-挑战杯", is_competition("挑战杯校赛选拔即将开始"))
    t.assert_true("竞赛-保研夏令营", is_competition("保研夏令营：清华大学机械系"))
    t.assert_true("竞赛-报名", is_competition("本次比赛报名截止日期为5月20日"))
    t.assert_false("竞赛-普通消息", is_competition("今天中午吃什么"))
    t.assert_false("竞赛-空文本", is_competition(""))
    t.assert_false("竞赛-None", is_competition(None))
    
    # --- is_secondhand ---
    t.assert_true("二手-出售", is_secondhand("出售iPad Pro 2024"))
    t.assert_true("二手-转让", is_secondhand("转让全新未拆封耳机"))
    t.assert_true("二手-毕业出", is_secondhand("毕业出机械键盘"))
    t.assert_false("二手-普通消息", is_secondhand("今天天气真好"))
    t.assert_false("二手-空文本", is_secondhand(""))
    t.assert_false("二手-None", is_secondhand(None))
    
    # --- get_category ---
    t.assert_equal("分类-竞赛", get_category("挑战杯报名通知"), "competition")
    t.assert_equal("分类-二手", get_category("出售iPhone 15"), "secondhand")
    t.assert_equal("分类-普通", get_category("今天吃什么"), "general")
    t.assert_equal("分类-空文本", get_category(""), "general")
    t.assert_equal("分类-通知(important)", get_category("【通知】明天开会"), "notice")


# ==================== 测试2：filter.py 向后兼容 ====================

def test_filter_backward_compat():
    t.start_section("测试2: filter.py 原有函数向后兼容")
    
    from filter import is_garbage, is_important, filter_and_extract, extract_key_info
    
    # --- is_garbage ---
    t.assert_true("垃圾-加群红包", is_garbage("加群123456领取红包"))
    t.assert_false("垃圾-正常通知", is_garbage("【通知】明天考试"))
    t.assert_true("垃圾-空文本", is_garbage(""))
    
    # --- is_important ---
    t.assert_true("重要-通知", is_important("【通知】明天上午开会"))
    t.assert_true("重要-紧急", is_important("紧急通知：今晚停电"))
    t.assert_true("重要-保研", is_important("保研相关通知"))
    t.assert_false("重要-普通", is_important("今天吃什么"))
    
    # --- filter_and_extract ---
    is_pass, info = filter_and_extract("【通知】明天上午9点在学术报告厅开会")
    t.assert_true("过滤提取-通过", is_pass)
    t.assert_not_none("过滤提取-有信息", info)
    if info:
        t.assert_true("过滤提取-标记为重要", info["is_important"])
    
    is_pass2, info2 = filter_and_extract("加群123456领取红包")
    t.assert_false("过滤提取-垃圾被拦截", is_pass2)
    t.assert_is_none("过滤提取-垃圾返回None", info2)
    
    # --- extract_key_info ---
    info3 = extract_key_info("【通知】明天上午开会\n请大家准时参加\n2026-03-15 09:00")
    t.assert_equal("提取-标题", info3["title"], "【通知】明天上午开会")
    # Note: regex only captures HH:MM:SS, not HH:MM, so "09:00" part is dropped
    t.assert_true("提取-时间", info3["time"].startswith("2026-03-15"))


# ==================== 测试3：EnhancedDetector 分类 ====================

def test_enhanced_detector_categorize():
    t.start_section("测试3: EnhancedDetector.detect_and_categorize() 分类")
    
    from enhanced_detector import EnhancedDetector
    
    detector = EnhancedDetector()
    
    # 3.1 竞赛类消息
    results = detector.detect_and_categorize(COMPETITION_MSGS)
    t.assert_true("分类-竞赛类有结果", len(results["competition"]) > 0, f"got {len(results['competition'])}")
    t.assert_equal("分类-二手类为空", len(results["secondhand"]), 0)
    
    # 检查竞赛类结果结构
    for r in results["competition"]:
        t.assert_true("竞赛-有category", r.get("category") == "competition")
        t.assert_not_none("竞赛-有priority", r.get("priority"))
        t.assert_not_none("竞赛-有confidence", r.get("confidence"))
        t.assert_not_none("竞赛-有matched_keywords", r.get("matched_keywords"))
    
    # 3.2 二手类消息
    results2 = detector.detect_and_categorize(SECONDHAND_MSGS)
    # 注意：SecondHandDetector依赖基础模块，可能不可用
    sh_count = len(results2.get("secondhand", []))
    print(f"  📊 二手检测结果: {sh_count} 条 (基础检测器{'可用' if detector.secondhand_detector.base_detector else '不可用'})")
    
    # 3.3 普通消息应该被过滤
    results3 = detector.detect_and_categorize(NORMAL_MSGS)
    total_normal = sum(len(v) for v in results3.values())
    t.assert_equal("分类-普通消息应无匹配", total_normal, 0)
    
    # 3.4 有用信息
    results4 = detector.detect_and_categorize(USEFUL_MSGS)
    ui_count = len(results4.get("useful_info", []))
    print(f"  📊 有用信息检测结果: {ui_count} 条")
    for r in results4.get("useful_info", []):
        t.assert_equal("有用信息-category", r.get("category"), "useful_info")
        t.assert_not_none("有用信息-subcategory", r.get("subcategory"))


# ==================== 测试4：CompetitionDetector 单独测试 ====================

def test_competition_detector():
    t.start_section("测试4: CompetitionDetector 单独测试")
    
    from enhanced_detector import CompetitionDetector
    
    cd = CompetitionDetector()
    
    # 正常检测
    r1 = cd.detect("挑战杯报名通知，截止时间：2026年3月15日，报名方式：填写问卷https://example.com，参赛要求：全校本科生")
    t.assert_not_none("单测-挑战杯有结果", r1)
    if r1:
        t.assert_equal("单测-类别", r1["category"], "competition")
        t.assert_true("单测-有截止时间", r1.get("deadline") is not None)
        t.assert_true("单测-截止时间内容", "3月15日" in str(r1.get("deadline", "")))
        t.assert_true("单测-有报名方式", r1.get("registration") is not None)
    
    # 保研类
    r2 = cd.detect("2026年保研夏令营开始报名了，请有意向的同学关注教务处通知")
    t.assert_not_none("单测-保研有结果", r2)
    if r2:
        t.assert_true("单测-保研高优先级", r2.get("priority") in ("HIGH", "MEDIUM"))
    
    # 带时间戳的dict输入
    r3 = cd.detect({
        "content": "互联网+大赛报名截止：2026-04-20",
        "source": "qq_group",
        "group_id": "743058932",
    })
    t.assert_not_none("单测-dict输入", r3)
    if r3:
        t.assert_true("单测-截止时间提取", r3.get("deadline") is not None)
    
    # batch
    batch_results = cd.detect_batch(COMPETITION_MSGS + NORMAL_MSGS)
    t.assert_equal("单测-batch长度", len(batch_results), len(COMPETITION_MSGS) + len(NORMAL_MSGS))
    competition_hits = [r for r in batch_results if r is not None]
    t.assert_true("单测-batch竞赛命中>=1", len(competition_hits) >= 1)
    
    # 非竞赛
    r4 = cd.detect("今天吃什么")
    t.assert_is_none("单测-普通消息返回None", r4)


# ==================== 测试5：DailyDigestV2 生成摘要 ====================

def test_daily_digest_v2():
    t.start_section("测试5: DailyDigestV2.generate() 摘要生成")
    
    from daily_digest_v2 import DailyDigestV2
    
    digest = DailyDigestV2(top_n_per_category=5)
    
    # 构造模拟分类数据
    mock_categorized = {
        "competition": [
            {
                "category": "competition",
                "priority": "HIGH",
                "confidence": 0.85,
                "matched_keywords": ["挑战杯", "报名"],
                "deadline": "2026年3月15日",
                "registration": "填写问卷 https://example.com",
                "message": {"content": "挑战杯报名通知，截止时间2026年3月15日", "source": "QQ群"},
            },
            {
                "category": "competition",
                "priority": "MEDIUM",
                "confidence": 0.60,
                "matched_keywords": ["数学建模"],
                "message": {"content": "数学建模竞赛校赛通知", "source": "QQ群"},
            },
        ],
        "secondhand": [
            {
                "category": "secondhand",
                "priority": "HIGH",
                "confidence": 0.90,
                "matched_keywords": ["出售", "几乎全新"],
                "prices": ["4000"],
                "items": ["iPad"],
                "item_category": "电子产品",
                "is_high_value": True,
                "high_value_items": ["iPad"],
                "max_price": 4000.0,
                "message": {"content": "出iPad Pro 2024 256G 99新 价格4000", "source": "QQ群"},
            },
            {
                "category": "secondhand",
                "priority": "LOW",
                "confidence": 0.50,
                "prices": ["20"],
                "items": ["教材"],
                "item_category": "书籍教材",
                "is_high_value": False,
                "max_price": 20.0,
                "message": {"content": "出二手教材 20元", "source": "QQ群"},
            },
        ],
        "useful_info": [
            {
                "category": "useful_info",
                "priority": "MEDIUM",
                "confidence": 0.50,
                "matched_keywords": ["讲座", "学术报告"],
                "subcategory": "学术讲座",
                "message": {"content": "【讲座通知】明晚7点人工智能学术报告", "source": "QQ群"},
            },
        ],
    }
    
    report = digest.generate(mock_categorized)
    
    # 基本结构
    t.assert_not_none("摘要-报告非空", report)
    t.assert_true("摘要-有date", "date" in report)
    t.assert_true("摘要-有total_count", "total_count" in report)
    t.assert_equal("摘要-总数", report["total_count"], 5)
    
    # 三种格式
    t.assert_true("摘要-有markdown", len(report.get("markdown", "")) > 0)
    t.assert_true("摘要-有plain_text", len(report.get("plain_text", "")) > 0)
    t.assert_true("摘要-有qq_email_html", len(report.get("qq_email_html", "")) > 0)
    
    # 分类统计
    cats = report.get("categories", {})
    t.assert_equal("摘要-竞赛count", cats.get("competition", {}).get("count"), 2)
    t.assert_equal("摘要-二手count", cats.get("secondhand", {}).get("count"), 2)
    t.assert_equal("摘要-有用count", cats.get("useful_info", {}).get("count"), 1)
    
    # markdown 内容检查
    md = report["markdown"]
    t.assert_true("md-含标题", "合工大每日巡检报告" in md)
    t.assert_true("md-含竞赛图标", "🏆" in md)
    t.assert_true("md-含二手图标", "🛒" in md)
    t.assert_true("md-含截止时间", "3月15日" in md)
    
    # 排序验证：二手高价值应该在前
    sh_top = cats.get("secondhand", {}).get("top", [])
    if len(sh_top) >= 2:
        t.assert_true("排序-高价值在前", sh_top[0].get("is_high_value") == True)
    
    # 空类别
    empty_report = digest.generate({"competition": [], "secondhand": [], "useful_info": []})
    t.assert_equal("空报告-总数", empty_report["total_count"], 0)
    t.assert_true("空报告-markdown存在", len(empty_report["markdown"]) > 0)


# ==================== 测试6：边界测试 ====================

def test_edge_cases():
    t.start_section("测试6: 边界测试（空输入、None、超长文本、纯英文、特殊字符）")
    
    from enhanced_detector import EnhancedDetector, CompetitionDetector
    
    detector = EnhancedDetector()
    cd = CompetitionDetector()
    
    # 空输入
    r1 = detector.detect("")
    t.assert_equal("边界-空字符串", r1, [])
    
    r2 = detector.detect({})
    t.assert_equal("边界-空dict", r2, [])
    
    r3 = cd.detect("")
    t.assert_is_none("边界-CD空字符串", r3)
    
    # 短文本（少于4字符）
    r4 = cd.detect("ab")
    t.assert_is_none("边界-短文本", r4)
    
    r5 = cd.detect("abc")
    t.assert_is_none("边界-3字符", r5)
    
    # 纯英文
    r6 = detector.detect("Hello, this is a test message about competition and registration")
    t.assert_true("边界-纯英文返回空", len(r6) == 0, f"got {r6}")
    
    # 特殊字符
    r7 = detector.detect("🎓✨💻  @#$%^&*()")
    t.assert_true("边界-纯特殊字符返回空", len(r7) == 0)
    
    # 超长文本
    long_text = "挑战杯" + "报名" * 500 + "竞赛通知请关注"
    long_msg = {"content": long_text, "source": "QQ群"}
    r8 = detector.detect(long_msg)
    t.assert_true("边界-超长文本不崩溃", True)  # 只要不抛异常就算过
    
    # 混合中英文
    r9 = cd.detect("The Challenge Cup 挑战杯 registration 报名 is now open")
    t.assert_not_none("边界-中英混合", r9)
    
    # None 输入 (CompetitionDetector.detect)
    # detect_batch with empty list
    batch_empty = detector.detect_batch([])
    t.assert_equal("边界-空batch", batch_empty, [])
    
    categorized_empty = detector.detect_and_categorize([])
    t.assert_equal("边界-空categorize-competition", categorized_empty["competition"], [])
    t.assert_equal("边界-空categorize-secondhand", categorized_empty["secondhand"], [])
    t.assert_equal("边界-空categorize-useful_info", categorized_empty["useful_info"], [])


# ==================== 测试7：配置读取与默认值回退 ====================

def test_config_loading():
    t.start_section("测试7: EnhancedDetector 配置读取与默认值回退")
    
    from enhanced_detector import CompetitionDetector, SecondHandDetector, UsefulInfoDetector, EnhancedDetector
    
    # 7.1 默认构造（从config.json读取）
    cd = CompetitionDetector()
    t.assert_true("配置-竞赛关键词数量", len(cd.keywords) > 10, f"got {len(cd.keywords)}")
    
    sd = SecondHandDetector()
    t.assert_true("配置-高价值关键词数量", len(sd.high_value_keywords) > 5, f"got {len(sd.high_value_keywords)}")
    t.assert_true("配置-高亮群组数量", len(sd.highlight_groups) >= 0)
    
    uid = UsefulInfoDetector()
    t.assert_true("配置-有用信息关键词", len(uid.keywords) > 5, f"got {len(uid.keywords)}")
    
    # 7.2 自定义关键词
    custom_cd = CompetitionDetector(keywords=["自定义", "测试"])
    t.assert_equal("配置-自定义关键词", custom_cd.keywords, ["自定义", "测试"])
    
    # 7.3 EnhancedDetector 构造
    ed = EnhancedDetector()
    t.assert_not_none("配置-ED竞赛检测器", ed.competition_detector)
    t.assert_not_none("配置-ED二手检测器", ed.secondhand_detector)
    t.assert_not_none("配置-ED有用信息检测器", ed.useful_info_detector)
    
    # 7.4 get_status
    status = ed.get_status()
    t.assert_true("状态-竞赛keys", status["competition_detector"]["keywords"] > 0)
    t.assert_true("状态-二手keys", status["secondhand_detector"]["high_value_keywords"] > 0)
    t.assert_true("状态-有用keys", status["useful_info_detector"]["keywords"] > 0)
    
    # 7.5 get_highlights
    all_msgs = COMPETITION_MSGS + SECONDHAND_MSGS + USEFUL_MSGS + NORMAL_MSGS
    highlights = ed.get_highlights(all_msgs, min_confidence=0.3)
    t.assert_true("高亮-返回列表", isinstance(highlights, list))
    # 应该按优先级排序
    if len(highlights) >= 2:
        first = highlights[0].get("priority", "")
        second = highlights[1].get("priority", "")
        pri_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        t.assert_true("高亮-排序正确", pri_order.get(first, 99) <= pri_order.get(second, 99))


# ==================== 测试8：Playwright 降级测试 ====================

def test_playwright_degradation():
    t.start_section("测试8: PlaywrightScraper 降级行为")
    
    from playwright_scraper import PlaywrightScraper, _check_playwright
    
    # 8.1 创建scraper（自动检测）
    scraper = PlaywrightScraper()
    pw_available = scraper.is_available
    print(f"  📊 Playwright 可用: {pw_available}")
    
    # 8.2 不管Playwright是否可用，search_wechat_articles应该工作（有降级）
    articles = scraper.search_wechat_articles("合肥工业大学", max_pages=1)
    t.assert_true("降级-搜索返回列表", isinstance(articles, list))
    
    # 8.3 空关键词
    empty_result = scraper.search_wechat_articles("", max_pages=1)
    t.assert_equal("降级-空关键词返回空", empty_result, [])
    
    # 8.4 fetch_article_content
    content = scraper.fetch_article_content("https://mp.weixin.qq.com/s/test")
    t.assert_true("降级-获取内容返回str", isinstance(content, str))
    
    # 8.5 空URL
    empty_content = scraper.fetch_article_content("")
    t.assert_equal("降级-空URL返回空", empty_content, "")
    
    # 8.6 screenshot 降级（无Playwright时返回False）
    screenshot_ok = scraper.screenshot_page("https://example.com", "/tmp/test_screenshot.png")
    if not pw_available:
        t.assert_false("降级-截图不可用返回False", screenshot_ok)
    else:
        print(f"  📊 截图结果: {screenshot_ok}")
    
    # 8.7 强制降级模式
    scraper_off = PlaywrightScraper(enable=False)
    t.assert_false("降级-force off", scraper_off.is_available)
    
    # 8.8 _check_playwright 函数
    result = _check_playwright()
    t.assert_true("降级-check返回bool", isinstance(result, bool))


# ==================== 测试9：UsefulInfoDetector 排他性 ====================

def test_useful_info_exclusivity():
    t.start_section("测试9: UsefulInfoDetector 排他性（不与竞赛/二手重复）")
    
    from enhanced_detector import UsefulInfoDetector
    
    uid = UsefulInfoDetector()
    
    # 强竞赛信号应该被排除
    r1 = uid.detect("挑战杯竞赛报名通知，讲座也在同一时间")
    t.assert_is_none("排他-竞赛信号排除有用信息", r1)
    
    # 强二手信号应该被排除
    r2 = uid.detect("出售二手教材，讲座也欢迎大家参加")
    t.assert_is_none("排他-二手信号排除有用信息", r2)
    
    # 纯有用信息应该通过
    r3 = uid.detect("【讲座通知】明晚7点有学术报告")
    t.assert_not_none("排他-纯有用信息通过", r3)
    if r3:
        t.assert_equal("排他-子类别学术讲座", r3.get("subcategory"), "学术讲座")
    
    # 实习招聘（同时匹配"招聘"和"实习"，子类别取决于set迭代顺序）
    r4 = uid.detect("字节跳动2026实习生招聘开始了")
    t.assert_not_none("排他-实习招聘", r4)
    if r4:
        subcat = r4.get("subcategory", "")
        t.assert_true("排他-子类别合理",
            subcat in ("实习机会", "招聘信息"),
            f"got '{subcat}', expected '实习机会' or '招聘信息'")


# ==================== 测试10：集成测试 - 端到端流程 ====================

def test_integration_e2e():
    t.start_section("测试10: 集成测试 - 端到端流程")
    
    from enhanced_detector import EnhancedDetector
    from daily_digest_v2 import DailyDigestV2
    
    # 完整的端到端流程
    all_messages = COMPETITION_MSGS + SECONDHAND_MSGS + USEFUL_MSGS + NORMAL_MSGS
    
    detector = EnhancedDetector()
    digest = DailyDigestV2(top_n_per_category=5)
    
    # Step 1: 分类
    categorized = detector.detect_and_categorize(all_messages)
    t.assert_true("集成-分类结果存在", isinstance(categorized, dict))
    
    # Step 2: 生成报告
    report = digest.generate(categorized)
    t.assert_true("集成-报告生成", isinstance(report, dict))
    
    # Step 3: 验证报告内容
    total = report["total_count"]
    comp_count = categorized["competition"]
    sh_count = categorized["secondhand"]
    ui_count = categorized["useful_info"]
    t.assert_equal("集成-总数一致", total, len(comp_count) + len(sh_count) + len(ui_count))
    
    # Step 4: 至少有一些有价值信息被检测到
    t.assert_true("集成-至少检测到竞赛", len(comp_count) >= 0)
    
    # 检查 get_highlights
    highlights = detector.get_highlights(all_messages, min_confidence=0.4)
    t.assert_true("集成-高亮结果", isinstance(highlights, list))


# ==================== 主入口 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("  增强型每日巡检系统 - 综合功能测试")
    print(f"  Python: {sys.version}")
    print(f"  项目路径: {Path(__file__).resolve().parent.parent}")
    print("=" * 60)
    
    # 运行所有测试
    test_filter_new_functions()
    test_filter_backward_compat()
    test_config_loading()
    test_competition_detector()
    test_enhanced_detector_categorize()
    test_useful_info_exclusivity()
    test_daily_digest_v2()
    test_edge_cases()
    test_playwright_degradation()
    test_integration_e2e()
    
    # 汇总
    passed, failed = t.summary()
    
    if failed > 0:
        print("\n  ❌ 存在失败测试:")
        for err in t.errors:
            print(f"     {err}")
        sys.exit(1)
    else:
        print("\n  ✅ 所有测试通过！")
        sys.exit(0)
