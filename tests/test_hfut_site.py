# -*- coding: utf-8 -*-
"""
合工大巡检系统 · pytest 单元测试套件（tests/test_hfut_site.py）
============================================================
全部离线：凡涉及网络的函数都用 monkeypatch 打桩或只测纯函数/解析器，
绝不触碰 news.hfut.edu.cn 等真实站点。

会写盘的函数（generate_daily_summary / save_index / save_state）一律在
测试里把模块级路径常量改到 tmp_path，断言写入落在 tmp_path，
避免覆盖用户真实的 hfut_full_index.json（1001 条 / 400KB）。

分组说明：
  A. hfut_scanner.py 纯函数
  B. hfut_scanner.py 专属提取器（内联 HTML fixture）
  C. generate_daily_summary / save_index / save_state（写盘，路径改 tmp）
  D. hfut_cli.build_summary
  E. scripts/daily_intel_template.py 渲染器
  F. scripts/send_daily_intel.py 不联网部分
"""

import datetime as _dt

import pytest

import hfut_scanner
import hfut_cli
import daily_intel_template
import send_daily_intel


# ── 工具：冻结 datetime.now，使涉及「今日/近7天」的逻辑不依赖运行日期 ──
def _freeze_now(mod, monkeypatch, fixed: _dt.datetime):
    """把被测模块里的 datetime.now() 固定为 fixed。

    注意：datetime 是 C 内置类型，不能对其 .now 方法做 monkeypatch.setattr。
    因此改为「用一个继承自真实 datetime 的子类覆盖模块级 datetime 属性」：
    子类重写了 now()，但 strftime / 构造器 / 与 timedelta 的运算全部继承，
    不影响 parse_date 等逻辑。替换的是模块属性（允许 setattr），不是内置类型。
    """

    class _Fixed(mod.datetime):
        @classmethod
        def now(cls):
            return cls(fixed.year, fixed.month, fixed.day,
                       fixed.hour, fixed.minute, fixed.second)

    monkeypatch.setattr(mod, "datetime", _Fixed)


# ════════════════════════════════════════════════════════════════
# A. hfut_scanner.py 纯函数
# ════════════════════════════════════════════════════════════════

class TestNormalizeUrl:
    """normalize_url() 是全系统去重基石：2026-09-22 因未归一化导致索引虚高 54 条。"""

    def test_http_https_视为同一篇(self):
        # 同路径的 http / https 应归一化成同一个去重键
        a = hfut_scanner.normalize_url("http://news.hfut.edu.cn/a.htm")
        b = hfut_scanner.normalize_url("https://news.hfut.edu.cn/a.htm")
        assert a == b == "https://news.hfut.edu.cn/a.htm"

    def test_主机大小写归一(self):
        a = hfut_scanner.normalize_url("https://NEWS.HFUT.EDU.CN/A")
        b = hfut_scanner.normalize_url("https://news.hfut.edu.cn/A")
        assert a == b == "https://news.hfut.edu.cn/A"

    def test_默认端口80_443被剥离(self):
        # http 先转 https，再去掉 :80/:443 默认端口
        assert hfut_scanner.normalize_url("http://x.com:80/a") == "https://x.com/a"
        assert hfut_scanner.normalize_url("https://x.com:443/a") == "https://x.com/a"

    def test_末位斜杠归一(self):
        assert hfut_scanner.normalize_url("https://x.com/a/") == "https://x.com/a"

    def test_查询串保留(self):
        # 查询串是去重的一部分，必须保留
        assert (hfut_scanner.normalize_url("https://x.com/a?p=1&q=2")
                == "https://x.com/a?p=1&q=2")

    def test_fragment被丢弃(self):
        assert hfut_scanner.normalize_url("https://x.com/a#frag") == "https://x.com/a"

    def test_空串原样返回(self):
        assert hfut_scanner.normalize_url("") == ""


class TestCleanTitle:
    """clean_title() 剥掉聚合页塞进 <a> 内部的日期污染。每个用例对应一种真实来源。"""

    def test_主站聚合页日期_div_塞进a内部_09斜杠212026(self):
        # 来源：主站聚合页 <a> 内含 <div class="notice-list-date">09<font>/</font>21<br><span>2026</span></div>
        assert hfut_scanner.clean_title("09/212026 真标题") == "真标题"

    def test_主站聚合页_09斜杠21_空格_真标题(self):
        assert hfut_scanner.clean_title("09/21 真标题") == "真标题"

    def test_科研院模板_日_年减月_真标题(self):
        # 来源：科研院把 <b>21 2026-08</b> 放在 </a> 之前
        assert hfut_scanner.clean_title("21 2026-08 真标题") == "真标题"

    def test_科研院模板_真标题_年减月减日(self):
        # 来源：科研院模板日期在 </a> 前面、标题之后
        assert hfut_scanner.clean_title("真标题 2026-09-21") == "真标题"

    def test_正常标题不被误伤(self):
        assert hfut_scanner.clean_title("关于举办数学竞赛的通知") == "关于举办数学竞赛的通知"


class TestItemKey:
    """item_key() 应优先用归一化 URL；URL 缺省时退化为标题。"""

    def test_优先用归一化url(self):
        assert (hfut_scanner.item_key({"url": "http://X.com/a/"})
                == "https://x.com/a")

    def test_url缺失时退化为标题(self):
        assert hfut_scanner.item_key({"title": "某通知"}) == "某通知"


class TestParseDate:
    """parse_date() 必须兼容多种分隔符，空串/垃圾串返回 None（防后续 d >= week_ago 抛错）。"""

    def test_标准横线(self):
        assert hfut_scanner.parse_date("2026-09-21") == _dt.datetime(2026, 9, 21)

    def test_斜杠分隔(self):
        assert hfut_scanner.parse_date("2026/9/21") == _dt.datetime(2026, 9, 21)

    def test_点分隔(self):
        assert hfut_scanner.parse_date("2026.9.21") == _dt.datetime(2026, 9, 21)

    def test_中文年月日(self):
        assert hfut_scanner.parse_date("2026年9月21日") == _dt.datetime(2026, 9, 21)

    def test_空串返回None(self):
        assert hfut_scanner.parse_date("") is None

    def test_垃圾串返回None(self):
        assert hfut_scanner.parse_date("这不是日期abc") is None


class TestScoreCategorize:
    """score_item / categorize_item 是「关键通知」筛选与报告分组的基础。"""

    def test_竞赛关键词加分(self):
        assert hfut_scanner.score_item({"title": "关于举办数学建模竞赛的通知"}) >= 10

    def test_分类_竞赛(self):
        assert hfut_scanner.categorize_item(
            {"title": "大学生程序设计竞赛报名"}) == "competition"

    def test_分类_保研(self):
        assert hfut_scanner.categorize_item(
            {"title": "2026年推免保研工作安排"}) == "baoyan"

    def test_分类_奖学金(self):
        assert hfut_scanner.categorize_item(
            {"title": "国家奖学金评选公示"}) == "scholarship"

    def test_分类_基金(self):
        assert hfut_scanner.categorize_item(
            {"title": "国家自然科学基金申报通知"}) == "funding"

    def test_分类_就业(self):
        assert hfut_scanner.categorize_item(
            {"title": "校园招聘宣讲会通知"}) == "career"


class TestLooksLikeJsShell:
    """_looks_like_js_shell() 是招标采购/图书馆两个 SPA 栏目能否抓到数据的开关，误判会丢栏目。"""

    def test_nuxt空壳_3KB_判为True(self):
        # ① 含 <div id="__nuxt"> 的 3KB 空壳 → True
        html = '<div id="__nuxt">' + "loading..." * 200 + "</div>"
        assert len(html) < 60000
        assert hfut_scanner._looks_like_js_shell(html) is True

    def test_含真实详情页锚点_不误判(self):
        # ② 含真实详情页锚点 info/12/3456.htm → False（不能误判成空壳）
        html = '<a href="http://x.hfut.edu.cn/info/12/3456.htm">详情</a>'
        assert hfut_scanner._looks_like_js_shell(html) is False

    def test_大于60KB_判为False(self):
        # ③ 大于 60KB 的页面 → False（视为真实渲染页，而非空壳）
        html = '<div id="__nuxt">' + ("x" * 61000) + "</div>"
        assert len(html) > 60000
        assert hfut_scanner._looks_like_js_shell(html) is False


# ════════════════════════════════════════════════════════════════
# B. hfut_scanner.py 专属提取器（内联 HTML fixture，不联网）
# ════════════════════════════════════════════════════════════════

class TestExtractItemsGeneric:
    """extract_items_generic() 按详情页 URL 形态提列表项，是 2026-09-22 重写后的通用解析器。"""

    HTML = """
    <div class="list">
      <a href="http://news.hfut.edu.cn/2026/0920/c3310a323387/page.htm" title="网站群形态通知标题">网站群形态通知标题</a>
      <a href="https://www.hfut.edu.cn/info/1245/6086.htm" title="老CMS形态通知标题">老CMS形态通知标题</a>
      <a href="http://x/info/999/888.htm" title="English">English</a>
      <a href="http://x/info/777/666.htm" title="短">短</a>
      <a href="http://news.hfut.edu.cn/2026/0920/c3310a323387/page.htm" title="网站群形态通知标题">重复链接</a>
      <a href="http://x/list.htm" title="首页">首页</a>
    </div>
    """

    def test_标题取自title属性且不粘连日期(self):
        items = hfut_scanner.extract_items_generic(self.HTML, "http://news.hfut.edu.cn/")
        titles = {it["title"] for it in items}
        assert "网站群形态通知标题" in titles
        assert "老CMS形态通知标题" in titles

    def test_网站群形态从url还原日期(self):
        items = hfut_scanner.extract_items_generic(self.HTML, "http://news.hfut.edu.cn/")
        hit = [it for it in items if it["title"] == "网站群形态通知标题"]
        assert len(hit) == 1  # 同时验证去重（见下）
        assert hit[0]["date"] == "2026-09-20"

    def test_导航词被过滤(self):
        items = hfut_scanner.extract_items_generic(self.HTML, "http://news.hfut.edu.cn/")
        titles = {it["title"] for it in items}
        assert "English" not in titles  # English 是 _NAV_WORDS 中的导航词
        assert "首页" not in titles

    def test_短标题被丢弃(self):
        items = hfut_scanner.extract_items_generic(self.HTML, "http://news.hfut.edu.cn/")
        titles = {it["title"] for it in items}
        assert "短" not in titles  # 长度 < 4

    def test_同一url只出现一次(self):
        items = hfut_scanner.extract_items_generic(self.HTML, "http://news.hfut.edu.cn/")
        same = [it for it in items if "c3310a323387/page.htm" in it["url"]]
        assert len(same) == 1  # 两条相同 href 的 <a> 合并为 1 条


class TestExtractLib:
    """extract_lib_html()：图书馆 React SPA 渲染后的 HTML。"""

    def test_能提取到条目且还原日期(self):
        html = """
        <div class="list">
          <span class="dbTime"><span>2026</span><span>09-22</span></span>
          <a href="/portal/noticedetail/1748/2014">AI+信息素养大赛报名通知</a>
        </div>
        """
        items = hfut_scanner.extract_lib_html(html, "https://lib.hfut.edu.cn")
        assert len(items) == 1
        assert items[0]["title"] == "AI+信息素养大赛报名通知"
        assert items[0]["date"] == "2026-09-22"

    def test_空壳返回空列表(self):
        html = '<div id="__nuxt">loading...</div>'
        assert hfut_scanner.extract_lib_html(html, "https://lib.hfut.edu.cn") == []


class TestExtractZb:
    """extract_zb_html()：招标采购 Nuxt 真实通知在 detail?id= 链接里。"""

    def test_能提取到条目(self):
        html = ('<div><a href="https://zb.hfut.edu.cn/cms/news/detail?id=123456">'
                '关于某招标采购项目的公告</a></div>')
        items = hfut_scanner.extract_zb_html(html, "https://zb.hfut.edu.cn")
        assert len(items) == 1
        assert items[0]["title"] == "关于某招标采购项目的公告"
        assert items[0]["url"] == "https://zb.hfut.edu.cn/cms/news/detail?id=123456"

    def test_空壳返回空列表(self):
        html = '<div id="__nuxt">loading...</div>'
        assert hfut_scanner.extract_zb_html(html, "https://zb.hfut.edu.cn") == []


class TestExtractXc:
    """extract_xc_html()：宣城校区，日期模板是反序的（日 / 年-月）。"""

    def test_反序日期模板还原为YYYY_MM_DD(self):
        # 重点：2026-09-22 修过的坑。单一 YYYY-MM-DD 正则会漏掉反序模板。
        html = """
        <div class="news_year">10</div><div class="news_days">2026-09</div>
        <a href="http://xc.hfut.edu.cn/c1984a34797/page.htm" title="宣城校区某通知标题">宣城校区某通知标题</a>
        """
        items = hfut_scanner.extract_xc_html(html, "http://xc.hfut.edu.cn")
        assert len(items) == 1
        assert items[0]["title"] == "宣城校区某通知标题"
        assert items[0]["date"] == "2026-09-10"  # 反序：日=10，年-月=2026-09

    def test_url内嵌日期形态(self):
        html = ('<a href="http://xc.hfut.edu.cn/2026/0920/c1984a34797/page.htm" '
                'title="宣城校区另一通知">宣城校区另一通知</a>')
        items = hfut_scanner.extract_xc_html(html, "http://xc.hfut.edu.cn")
        assert len(items) == 1
        assert items[0]["date"] == "2026-09-20"


# ════════════════════════════════════════════════════════════════
# C. 写盘功能（路径改 tmp_path，断言写入落在 tmp_path）
# ════════════════════════════════════════════════════════════════

class TestSaveIndex:
    """save_index() 会覆盖 hfut_full_index.json，必须改到 tmp_path 防止事故。"""

    def test_写入落在tmp_path(self, tmp_path, monkeypatch):
        target = tmp_path / "hfut_full_index.json"
        monkeypatch.setattr(hfut_scanner, "INDEX_FILE", str(target))
        data = [{"title": "X", "url": "https://x.com/1"}]
        hfut_scanner.save_index(data)
        # 显式断言：文件路径确实在 tmp_path 下，且内容正确
        assert target.exists()
        assert target.parent == tmp_path
        import json
        assert json.loads(target.read_text(encoding="utf-8")) == data


class TestSaveState:
    """save_state() 会写 hfut_scan_state.json，必须改到 tmp_path。"""

    def test_写入落在tmp_path且补last_scan_time(self, tmp_path, monkeypatch):
        target = tmp_path / "hfut_scan_state.json"
        monkeypatch.setattr(hfut_scanner, "STATE_FILE", str(target))
        state = {"total_items": 42}
        hfut_scanner.save_state(state)
        import json
        written = json.loads(target.read_text(encoding="utf-8"))
        assert target.parent == tmp_path
        assert written["total_items"] == 42
        assert "last_scan_time" in written  # save_state 应补时间戳


class TestGenerateDailySummary:
    """generate_daily_summary() 写盘 + 生成近7天 HTML（路径改 tmp_path）。"""

    def _run(self, index, monkeypatch, tmp_path):
        target = tmp_path / "hfut_daily_summary.html"
        monkeypatch.setattr(hfut_scanner, "SUMMARY_FILE", str(target))
        _freeze_now(hfut_scanner, monkeypatch, _dt.datetime(2026, 9, 22, 12, 0))
        out = hfut_scanner.generate_daily_summary(index)
        assert out == str(target)
        assert target.exists()
        assert target.parent == tmp_path
        return target.read_text(encoding="utf-8")

    def test_近7天标题出现_更早与无日期被排除(self, tmp_path, monkeypatch):
        index = [
            {"title": "近七天竞赛通知", "url": "https://x.com/1", "date": "2026-09-20",
             "category": "competition", "score": 15, "channel": "科研院-通知公告"},
            {"title": "更早的通知", "url": "https://x.com/2", "date": "2026-09-01",
             "category": "other", "score": 0, "channel": "X"},
            {"title": "无日期通知", "url": "https://x.com/3", "date": "",
             "category": "other", "score": 0, "channel": "Y"},
        ]
        html = self._run(index, monkeypatch, tmp_path)
        # 近7天条目应出现在摘要中
        assert "近七天竞赛通知" in html
        # 早于此窗口 / 无日期的条目不应进入「近7天」区块
        assert "更早的通知" not in html
        assert "无日期通知" not in html

    def test_缺url键条目不抛KeyError(self, tmp_path, monkeypatch):
        """依赖 scanner-dev 健壮性修复（把 item[\"url\"] 改成 item.get(\"url\")）。

        当前实现在生成近7天条目时直接 item[\"url\"] 取值，缺 url 键会抛 KeyError。
        这是验收标准：修复后应能正常生成、且标题出现在摘要里。
        """
        index = [
            {"title": "缺URL的近期通知", "date": "2026-09-20",
             "category": "other", "score": 0, "channel": "Z"},
        ]
        # 未修复时此调用会抛 KeyError；修复后应通过并包含该标题
        html = self._run(index, monkeypatch, tmp_path)
        assert "缺URL的近期通知" in html

    def test_标题中的特殊字符被转义(self, tmp_path, monkeypatch):
        """依赖 scanner-dev 转义修复（标题进 HTML 前需 html.escape）。

        当前实现把 title 直接 f-string 进 HTML，含 & / < 会撑坏排版。
        验收标准：输出里应是 &amp; / &lt;，原文不出现。
        """
        index = [
            {"title": "A & B <C> 通知", "url": "https://x.com/1", "date": "2026-09-20",
             "category": "other", "score": 0, "channel": "X"},
        ]
        html = self._run(index, monkeypatch, tmp_path)
        assert "&amp;" in html
        assert "&lt;" in html
        # 注入原文不应原样出现
        assert "A & B <C> 通知" not in html


# ════════════════════════════════════════════════════════════════
# D. hfut_cli.build_summary（纯函数，不写盘）
# ════════════════════════════════════════════════════════════════

class TestBuildSummary:
    def _freeze(self, monkeypatch):
        _freeze_now(hfut_cli, monkeypatch, _dt.datetime(2026, 9, 22, 12, 0))

    def test_包含关键统计字段(self, monkeypatch):
        self._freeze(monkeypatch)
        index, state = [], {
            "total_items": 100,
            "channel_results": {"科研院-通知公告": 3, "研究生院-通知公告": 2,
                                 "图书馆-通知公告": 0},
            "last_scan_time": "2026-09-22 08:00",
        }
        out = hfut_cli.build_summary(index, state, new_count=5)
        assert "总索引: 100 条" in out
        assert "今日新增: 5 条" in out
        assert "覆盖栏目: 2/3" in out  # 成功 2 / 总数 3

    def test_关键通知按栏目分组(self, monkeypatch):
        self._freeze(monkeypatch)
        index = [
            {"title": "关于举办数学建模竞赛的通知", "date": "2026-09-20", "score": 15,
             "channel": "科研院-通知公告", "url": "https://x.com/1"},
            {"title": "2026年推免保研工作安排", "date": "2026-09-21", "score": 12,
             "channel": "研究生院-通知公告", "url": "https://x.com/2"},
        ]
        state = {"total_items": 2, "channel_results": {"科研院-通知公告": 1,
                                                          "研究生院-通知公告": 1}}
        out = hfut_cli.build_summary(index, state, new_count=0)
        # 按栏目分组格式：▎栏目名（N）
        assert "▎科研院-通知公告（1）" in out
        assert "▎研究生院-通知公告（1）" in out

    def test_browser_skipped不出现在未取到栏目(self, monkeypatch):
        """依赖 cli-dev B1 修复：被主动跳过的浏览器栏目不应列入「⚠ 未取到栏目」。

        当前 build_summary 的 fail_channels 直接取 channel_results 中 v==0 的项，
        未排除 state["browser_skipped"]，因此该栏目会被误报。验收标准：修复后不出现。
        """
        self._freeze(monkeypatch)
        state = {
            "total_items": 0,
            "channel_results": {"招标采购-通知公告": 0, "图书馆-通知公告": 5},
            "browser_skipped": ["招标采购-通知公告"],
        }
        out = hfut_cli.build_summary([], state, new_count=0)
        # 被跳过的浏览器栏目不应出现在「未取到栏目」里
        assert "招标采购-通知公告" not in out.split("⚠ 未取到栏目")[-1] \
            if "⚠ 未取到栏目" in out else True
        # 更强的断言：整行不应包含该栏目名（除非在别的语境，这里没有）
        assert "⚠ 未取到栏目" not in out or "招标采购-通知公告" not in out

    def test_failed_channels为空时以之为准(self, monkeypatch):
        # channel_results 为空时，应回退使用 state["failed_channels"]
        self._freeze(monkeypatch)
        state = {"total_items": 0, "channel_results": {},
                 "failed_channels": ["某栏目"]}
        out = hfut_cli.build_summary([], state, new_count=0)
        assert "⚠ 未取到栏目(1): 某栏目" in out


# ════════════════════════════════════════════════════════════════
# E. scripts/daily_intel_template.py 渲染器
# ════════════════════════════════════════════════════════════════

MIN_DATA = {
    "date": "2026-09-23",
    "ddls": [{"deadline": "9月25日 12:00 截止",
              "title": "中国国际大学生创新大赛国赛报名",
              "url": "https://example.com/ddl1"}],
    "hfut": [{"channel": "科研院-通知公告", "title": "自然科学基金申报",
              "url": "https://example.com/hfut1", "tag": "funding"}],
    "github": [{"name": "Bytebot", "desc": "某某机器人",
                "url": "https://github.com/x/Bytebot", "stars": "8,213"}],
    "baoyan": [{"title": "夏令营报名", "desc": "某夏令营",
                "url": "https://example.com/baoyan1"}],
}


class TestTemplateRender:
    def test_正常路径结构完整且每条ddl带链接(self):
        html = daily_intel_template.render(dict(MIN_DATA))
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        # 每条 DDL 都带 <a href=
        assert '<a class="ddl-link" href="https://example.com/ddl1"' in html
        # hfut / github / baoyan 的链接也都在
        assert 'href="https://example.com/hfut1"' in html
        assert 'href="https://github.com/x/Bytebot"' in html
        assert 'href="https://example.com/baoyan1"' in html

    def test_校验_ddl缺url抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "y"}]  # 无 url
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_url为井号抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "y", "url": "#"}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_url为空串抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "y", "url": ""}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_url为javascript_void抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "y", "url": "javascript:void(0)"}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_url非http_s开头抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "y", "url": "ftp://example.com/x"}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_ddl缺title抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "url": "https://example.com/x"}]  # 无 title
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_ddls非数组抛错(self):
        d = dict(MIN_DATA)
        d["ddls"] = "不是数组"
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_转义防注入_原文不出现(self):
        d = dict(MIN_DATA)
        d["ddls"] = [{"deadline": "x", "title": "A & B <script>alert(1)</script>",
                      "url": "https://example.com/x"}]
        html = daily_intel_template.render(d)
        # 转义后形式存在
        assert "&amp;" in html
        assert "&lt;script&gt;" in html
        # 注入原文不应原样出现
        assert "<script>alert(1)</script>" not in html
        assert "A & B <script>" not in html


class TestTemplateLint:
    def test_合规html返回空(self):
        html = daily_intel_template.render(dict(MIN_DATA))
        assert daily_intel_template.lint_html(html) == []

    def test_ddl_li无链接被检出(self):
        html = '<div class="ddls"><ul><li>某通知无链接文本</li></ul></div></div>'
        problems = daily_intel_template.lint_html(html)
        assert problems  # 非空
        assert "DDL 第 1 条" in problems[0]

    def test_全文出现href井号被检出(self):
        html = '<a href="#">点击</a>'
        problems = daily_intel_template.lint_html(html)
        assert problems
        # lint_html 用 repr 输出占位链接，形如 存在占位/空链接: href='#'
        assert "存在占位/空链接" in problems[0]
        assert "#" in problems[0]

    def test_空字符串被检出(self):
        problems = daily_intel_template.lint_html("")
        assert problems
        assert "正文为空" in problems[0]


# ════════════════════════════════════════════════════════════════
# F. scripts/send_daily_intel.py 不联网部分
# ════════════════════════════════════════════════════════════════

class TestSendDailyIntel:
    def test_load_config文件不存在抛SystemExit(self, tmp_path, monkeypatch):
        missing = tmp_path / "no_such_config.json"
        monkeypatch.setattr(send_daily_intel, "CONFIG_FILE", str(missing))
        with pytest.raises(SystemExit):
            send_daily_intel.load_config()

    def test_收件人格式可疑时main抛SystemExit(self, tmp_path, monkeypatch):
        """收件人格式校验逻辑（内嵌在 main 中）：可疑地址应被拦截、绝不发信。"""
        cfg = tmp_path / "hfut_email_config.json"
        cfg.write_text('{"recipients": ["ok@x.com"]}', encoding="utf-8")
        html_file = tmp_path / "body.html"
        html_file.write_text("<p>hi</p>", encoding="utf-8")
        monkeypatch.setattr(send_daily_intel, "CONFIG_FILE", str(cfg))
        monkeypatch.setattr("sys.argv", [
            "send_daily_intel.py", "--to", "bad@nodot",
            "--html", str(html_file), "--dry-run",
        ])
        with pytest.raises(SystemExit):
            send_daily_intel.main()

    def test_合法收件人dry_run不发送(self, tmp_path, monkeypatch):
        """合法收件人 + --dry-run：应走完校验、返回 0，且绝不真正发信（离线）。"""
        cfg = tmp_path / "hfut_email_config.json"
        cfg.write_text('{"recipients": ["ok@x.com"]}', encoding="utf-8")
        html_file = tmp_path / "body.html"
        html_file.write_text("<p>hi</p>", encoding="utf-8")
        monkeypatch.setattr(send_daily_intel, "CONFIG_FILE", str(cfg))
        monkeypatch.setattr("sys.argv", [
            "send_daily_intel.py", "--to", "ok@x.com",
            "--html", str(html_file), "--dry-run",
        ])
        assert send_daily_intel.main() == 0


# ════════════════════════════════════════════════════════════════
# G. 标题选择链回归（2026-09-23 新增）
#    背景：线上索引里 20+ 条标题粘连了整段摘要，例如
#    「计算机与信息学院第四届"院长奖学金"评审通知 为全面贯彻党的教育方针，
#      落实立德树人根本任务，积极培育和践行社会主义核心价值观……（数百字）」
#    根因：旧回退链是「title 属性 → class 含 title 的元素 → 整个 inner」，
#          而 ci.hfut.edu.cn 把标题放在 **class 不含 "title" 字样**的 <h5> 里，
#          第 2 级落空后第 3 级把标题+摘要一起取走。
#    fixture 全部照抄真实页面结构，不要"美化"，否则测不出问题。
# ════════════════════════════════════════════════════════════════

# 计算机学院 ci.hfut.edu.cn/ggxw/tzgg.htm —— 锚点无 title 属性，标题在 <h5> 里
_HTML_CI_H5 = """
<ul>
  <li>
    <a href="../info/1063/16610.htm">
      <div class="media">
        <div class="date"><b>22</b><br> 2026-09</div>
        <div class="media-body">
          <h5 class="mt-2 mb-3">计算机与信息学院第四届“院长奖学金”评审通知</h5>
          <span class="news-text d-none d-sm-block"> 为全面贯彻党的教育方针，落实立德树人根本任务，
            积极培育和践行社会主义核心价值观，深入挖掘和宣传表彰研究生先进典型，充分发挥
            博士研究生的榜样示范和科研引领作用，学院面向全体在籍全日制博士研究生设立“院长奖学金”。
            现进行第四届“院长奖学金”的评选工作，具体评选对象、名额、办法如下：一、评选对象
            合肥工业大学正式学籍的非在职全日制博士研究生（最长年限为4年，硕博连读为5年）。</span>
        </div>
      </div>
    </a>
  </li>
</ul>
"""

# 主站 www.hfut.edu.cn/xwdt/tzgg.htm —— title 属性存在，但锚点里标题出现两遍
_HTML_MAIN_DUP = """
<li>
  <a href="http://international.hfut.edu.cn/info/1026/6339.htm" target="_blank"
     title="2026-2027学年春季学期澳门城市大学交换项目招生通知">
    <div class="notice-list-date">09<font>/</font>23<br><span>2026</span></div>
    <div class="notice-tt txt-elise">2026-2027学年春季学期澳门城市大学交换项目招生通知</div>
  </a>
</li>
<li>
  <a href="http://kyy.hfut.edu.cn/info/1002/2199.htm" target="_blank">
    <div class="notice-tt txt-elise">关于2019年度相关证书材料归档工作的通知</div>
    <div class="notice-p">关于2019年度相关证书材料归档工作的通知</div>
  </a>
</li>
"""

# 教务部 jwc.hfut.edu.cn/xwdt.htm —— class 里就带 title / text（原有正确路径，作护栏）
_HTML_JWC_TITLE_CLASS = """
<a href="info/1245/6089.htm" class="l3-news--item" id="line_u7_0">
  <div class="l3-news--date">
    <div class="l3-news--day">20</div>
    <div class="l3-news--month">2026-09</div>
  </div>
  <div class="l3-news--main">
    <div class="l3-news--title">我校举办“未来实训中心”专题研讨会暨联盟成立大会</div>
    <div class="l3-news--text">7月18日，学校举办“未来实训中心”专题研讨会暨联盟成立大会，来自……</div>
  </div>
</a>
"""

# 摘要开头的特征词：标题里出现这些，基本可以断定摘要被粘进来了
_SUMMARY_LEAK_MARKERS = ("为全面贯彻", "各位同学", "根据教育部", "现将有关事项通知如下",
                         "落实立德树人", "积极培育和践行")


class TestTitlePickingRegression:
    """G 组：标题选择链。防止 <h5> 装标题的模板把整段摘要粘进标题。"""

    def test_计算机学院_h5装标题_不粘摘要(self):
        """ci.hfut.edu.cn 形态：标题在无语义 class 的 <h5> 里，摘要紧跟其后。

        这条用例在修复前是**红的**（会返回「标题 + 数百字摘要」）。
        """
        items = hfut_scanner.extract_items_generic(_HTML_CI_H5, "https://ci.hfut.edu.cn/ggxw/tzgg.htm")
        assert items, "应至少提取到 1 条"
        title = items[0]["title"]
        assert title == "计算机与信息学院第四届“院长奖学金”评审通知", f"标题被污染: {title[:80]!r}"
        for marker in _SUMMARY_LEAK_MARKERS:
            assert marker not in title, f"标题里混入了摘要片段 {marker!r}"
        assert len(title) < 40, f"标题长度 {len(title)} 明显偏长，疑似粘了摘要"

    def test_计算机学院_顺带还原出日期(self):
        """同一 fixture 里 <div class="date"><b>22</b><br> 2026-09</div> 应还原为 2026-09-22。

        这条在修复前也是**红的**（旧 _extract_date_from_ctx 的 class 白名单不含 "date"）。
        """
        items = hfut_scanner.extract_items_generic(_HTML_CI_H5, "https://ci.hfut.edu.cn/ggxw/tzgg.htm")
        assert items[0]["date"] == "2026-09-22"

    def test_主站_title属性存在_不重复(self):
        """title 属性优先，且不得出现「标题 标题」式自重复。"""
        items = hfut_scanner.extract_items_generic(_HTML_MAIN_DUP, "https://www.hfut.edu.cn/xwdt/tzgg.htm")
        by_url = {i["url"]: i["title"] for i in items}
        t1 = by_url["http://international.hfut.edu.cn/info/1026/6339.htm"]
        assert t1 == "2026-2027学年春季学期澳门城市大学交换项目招生通知"

    def test_主站_锚点内标题重复两次_只留一份(self):
        """无 title 属性的那条：inner 里标题写了两遍，必须折叠成一份。"""
        items = hfut_scanner.extract_items_generic(_HTML_MAIN_DUP, "https://www.hfut.edu.cn/xwdt/tzgg.htm")
        by_url = {i["url"]: i["title"] for i in items}
        t2 = by_url["http://kyy.hfut.edu.cn/info/1002/2199.htm"]
        assert t2 == "关于2019年度相关证书材料归档工作的通知", f"标题重复未折叠: {t2!r}"

    def test_教务部_class装title_摘要不混入(self):
        """护栏：教务部这条原有正确路径不能被改坏（标题 class 带 title、摘要 class 带 text）。"""
        items = hfut_scanner.extract_items_generic(_HTML_JWC_TITLE_CLASS, "https://jwc.hfut.edu.cn/xwdt.htm")
        assert len(items) == 1
        it = items[0]
        assert it["title"] == "我校举办“未来实训中心”专题研讨会暨联盟成立大会"
        assert it["date"] == "2026-09-20"
        assert "来自" not in it["title"], "摘要被粘进标题了"

    def test_纯标题_不被误伤(self):
        """正常标题（含书名号/冒号/顿号）不应被去重复或截断逻辑改坏。"""
        html = '<a href="info/1063/16517.htm" title="关于举办计算机与信息学院第十七届科普创意创新大赛暨安徽省“双百”大赛院内选拔赛的通知"></a>'
        items = hfut_scanner.extract_items_generic(html, "https://ci.hfut.edu.cn/ggxw/tzgg.htm")
        assert items[0]["title"] == "关于举办计算机与信息学院第十七届科普创意创新大赛暨安徽省“双百”大赛院内选拔赛的通知"


# ════════════════════════════════════════════════════════════════
# H. 日期还原回归（2026-09-23 新增）
#    背景：线上 1020 条里 259 条（25%）没有日期，「近 7 天」筛选直接把它们全部过滤掉
#          → 这些条目**永远进不了每日情报**（用户抱怨"官网各栏目没有信息"的真正原因之一）。
#    根因：_extract_date_from_ctx() 只认 2 种模板 —— 完整 YYYY-MM-DD，以及
#          class 必须是 news_year/l3-news--day 的拆分模板。实测至少漏 3 种真实模板。
# ════════════════════════════════════════════════════════════════

class TestExtractDateFromCtx:
    """H 组：日期还原。正向用例证明 4 种真实模板都能还原，
    护栏用例证明"还原不出来时宁可为空，也不能瞎猜"。"""

    def test_新闻网_中文年月日(self):
        """news.hfut.edu.cn 的 <i>2026年09月23日</i>。

        注意原始 _RE_DATE_NEAR 的分隔符集合是 [-/\\.]，**不含「年月日」**；
        这类条目过去只靠 URL 里嵌的 /YYYY/MMDD/ 蒙对，换域名就丢日期。
        """
        assert hfut_scanner._extract_date_from_ctx("<i>2026年09月23日</i>") == "2026-09-23"

    def test_宣城与教务部_反序拆分模板(self):
        """class="news_year"（日）+ class="news_days"（年-月），命名反直觉，日在前。"""
        ctx = '<div class="news_year">10</div><div class="news_days">2026-09</div>'
        assert hfut_scanner._extract_date_from_ctx(ctx) == "2026-09-10"

    def test_主站_月日年(self):
        """www.hfut.edu.cn 的 <div class="notice-list-date">09<font>/</font>23<br><span>2026</span></div>。

        去标签后是 `09 / 23 2026`（月/日/年），修复前**返回空**。
        这里顺带验证「标签替换成空格而不是删掉」—— 删掉会粘成 `09/232026` 而匹配失败。
        """
        ctx = '<div class="notice-list-date">09<font>/</font>23<br><span>2026</span></div>'
        assert hfut_scanner._extract_date_from_ctx(ctx) == "2026-09-23"

    def test_计算机学院_日年月(self):
        """ci.hfut.edu.cn 的 <div class="date"><b>22</b><br> 2026-09</div>。

        去标签后是 `22 2026-09`（日/年-月），修复前**返回空** ——
        旧实现把 class 写死成 news_year/l3-news--day 白名单，`class="date"` 直接落空。
        """
        ctx = '<div class="date"><b>22</b><br> 2026-09</div>'
        assert hfut_scanner._extract_date_from_ctx(ctx) == "2026-09-22"

    def test_护栏_只有页码与浏览量(self):
        """上下文中只有页码/浏览量数字时，必须返回空，不能瞎猜一个日期。"""
        assert hfut_scanner._extract_date_from_ctx(
            '<span class="page">共 3 页</span> 阅读量 1024 次') == ""

    def test_护栏_非法日月返回空(self):
        """日 45 / 月 19 这类非法组合必须丢弃（_mk_date 的合法性校验）。"""
        assert hfut_scanner._extract_date_from_ctx(
            '<div class="date"><b>45</b><br> 2026-19</div>') == ""

    def test_护栏_异常年份返回空(self):
        """1999 年（早于合工大网站改版）视为噪声，丢弃。"""
        assert hfut_scanner._extract_date_from_ctx(
            '<div class="date"><b>22</b><br> 1999-09</div>') == ""

    def test_护栏_纯空串(self):
        assert hfut_scanner._extract_date_from_ctx("") == ""


# ════════════════════════════════════════════════════════════════
# I. 全局输出流不被劫持（2026-09-23 新增）
#    背景：hfut_scanner / hfut_cli / hfut_report_gen / hfut_email 四个模块曾在
#          import 期执行 sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)，
#          把**宿主的**输出流换成包在其 buffer 之上的新包装器。宿主（pytest）
#          随后关闭自己的捕获缓冲 → 包装器指向已关闭资源 →
#          `ValueError: I/O operation on closed file`，整个测试进程在收集阶段崩溃、
#          一个用例都跑不了（只有 `lost sys.stderr` 一行线索，极难排查）。
#    这类"库模块污染宿主全局状态"的行为必须被回归用例钉死。
# ════════════════════════════════════════════════════════════════

class TestNoGlobalStreamHijack:
    """I 组：import 生产模块不得替换宿主 sys.stdout/stderr 对象。"""

    _PROBE = (
        "import sys, io\n"
        "_real = sys.stdout            # 先留住真实 stdout，否则结论会打水漂\n"
        "sys.stdout = io.StringIO()    # 模拟宿主流：没有 reconfigure、也不是 TextIOWrapper\n"
        "sys.stderr = io.StringIO()\n"
        "sys.path.insert(0, r'{site}')\n"
        "import hfut_scanner, hfut_cli, hfut_report_gen, hfut_email\n"
        "assert isinstance(sys.stdout, io.StringIO), 'sys.stdout 被替换成了 ' + type(sys.stdout).__name__\n"
        "assert isinstance(sys.stderr, io.StringIO), 'sys.stderr 被替换成了 ' + type(sys.stderr).__name__\n"
        "_real.write('OK')\n"
    )

    def test_import不替换宿主输出流(self):
        """在子进程里验证（避免污染本测试进程）。"""
        import os
        import subprocess
        import sys

        site = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hfut_site")
        code = self._PROBE.format(site=site)
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (
            f"import 生产模块后宿主输出流被替换了：\n{r.stderr.strip()}")
        assert "OK" in r.stdout


# ════════════════════════════════════════════════════════════════
# J. 「JS 未启用」兜底页识别（2026-09-23 新增）
#    背景：图书馆 lib.hfut.edu.cn 连续两次扫描都在 failed_channels 里出现。
#    排查发现它不是网络故障 —— CDP 渲染**返回了 120636 字符、HTTP 200 的页面**，
#    长度远超 `len(html) > 200` 的成功阈值，于是被判定为"渲染成功";
#    但页面里只有一句 noscript 兜底文案
#    （"We're sorry but 图书馆管理服务门户 doesn't work properly without JavaScript
#      enabled. Please enable it to continue."），Vue 应用根本没挂载，
#    提取器拿到 0 条 → 最终表现成"这个栏目今天没内容"，
#    把**基础设施故障伪装成内容为空**，报告里看不出任何异常。
#    _looks_like_js_required() 用来把这种情况识别成"渲染失败"，进 failed_channels。
# ════════════════════════════════════════════════════════════════

class TestLooksLikeJsRequired:
    """J 组：SPA 未挂载的兜底页必须被判为失败，不能算"今天没内容"。"""

    def test_英文兜底文案被判为True(self):
        html = ("<body><noscript>We're sorry but 图书馆管理服务门户 doesn't work "
                "properly without JavaScript enabled. Please enable it to continue."
                "</noscript></body>")
        assert hfut_scanner._looks_like_js_required(html) is True

    def test_中文兜底文案被判为True(self):
        assert hfut_scanner._looks_like_js_required("请开启 JavaScript 后继续访问") is True

    def test_正常渲染页不被误判(self):
        """真实的招标采购渲染页（572912 字符、18 条内容）不能被误判成兜底页。

        误判的代价很直接：这个栏目会被判失败、当天数据全丢。
        """
        html = ('<div class="news-list"><a href="/cms/detail/1">'
                '<h4>合肥工业大学2026年学生月饼采购中标结果公告</h4>'
                '<span>2026-09-22</span></a></div>')
        assert hfut_scanner._looks_like_js_required(html) is False

    def test_空串与None不炸(self):
        assert hfut_scanner._looks_like_js_required("") is False
        assert hfut_scanner._looks_like_js_required(None) is False


# ════════════════════════════════════════════════════════════════
# K. 邮件脚本的参数把关与干跑安全（2026-09-23 新增）
#    背景（真实事故）：`python hfut_email.py --help` 既没打印帮助，**还给全部收件人
#    真发了一封巡检报告**。根因是 main() 只识别 --setup/--test/--text-file=/--html-file=，
#    其余参数一律落到"正常发送巡检报告"分支 —— "未知参数即执行默认动作"对有副作用的
#    脚本就是事故源。修复后：--help 打印用法；未知参数 / 无参数一律 exit 2 且不发信。
#    另外 --dry-run 曾被加进白名单却没实现拦截（同样导致误发），现已在
#    send_email / _send_plain_email 两处前置返回，这里用打桩证明它真的不发。
# ════════════════════════════════════════════════════════════════

class TestEmailArgGuard:
    """K 组：邮件脚本不得因为「参数不认识」而误发邮件。"""

    @pytest.fixture()
    def _no_smtp(self, monkeypatch):
        """把 smtplib 的两个入口换成「一被调用就炸」，从而证明没有发信。"""
        import smtplib

        def _boom(*a, **kw):
            raise AssertionError("干跑/参数校验路径不应触达 smtplib —— 说明真的发信了")

        monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)
        monkeypatch.setattr(smtplib, "SMTP", _boom)

    def test_未知参数被拒且不发信(self, monkeypatch, _no_smtp, capsys):
        import hfut_email

        monkeypatch.setattr("sys.argv", ["hfut_email.py", "--bogus"])
        with pytest.raises(SystemExit) as ei:
            hfut_email.main()
        assert ei.value.code == 2
        assert "未知参数" in capsys.readouterr().out

    def test_help打印用法且不发信(self, monkeypatch, _no_smtp, capsys):
        """`--help` 是最容易被误用的参数 —— 必须只打印用法。"""
        import hfut_email

        monkeypatch.setattr("sys.argv", ["hfut_email.py", "--help"])
        hfut_email.main()          # 不应抛 SystemExit
        out = capsys.readouterr().out
        assert "用法" in out
        assert "hfut_email.py" in out

    def test_无参数被拒不发信(self, monkeypatch, _no_smtp):
        import hfut_email

        monkeypatch.setattr("sys.argv", ["hfut_email.py"])
        with pytest.raises(SystemExit) as ei:
            hfut_email.main()
        assert ei.value.code == 2

    def test_dry_run不触达smtp(self, monkeypatch, _no_smtp, capsys, tmp_path):
        """--dry-run 必须在两个发信函数里前置返回（曾因漏实现而误发）。"""
        import hfut_email

        cfg = tmp_path / "hfut_email_config.json"
        cfg.write_text('{"sender":"a@qq.com","password":"x","recipients":["b@x.com"]}',
                       encoding="utf-8")
        monkeypatch.setattr(hfut_email, "CONFIG_FILE", str(cfg))
        monkeypatch.setattr(hfut_email, "INDEX_FILE", str(tmp_path / "idx.json"))
        monkeypatch.setattr(hfut_email, "STATE_FILE", str(tmp_path / "state.json"))
        (tmp_path / "idx.json").write_text(
            '[{"title":"测试通知","url":"https://x/1.htm","date":"2026-09-23",'
            '"channel":"测试栏目","score":12}]', encoding="utf-8")
        (tmp_path / "state.json").write_text(
            '{"total_items":1,"channel_results":{"测试栏目":1}}', encoding="utf-8")

        monkeypatch.setattr("sys.argv", ["hfut_email.py", "--new=1", "--dry-run"])
        hfut_email.main()          # 触达 smtplib 会直接 AssertionError
        assert "DRY-RUN" in capsys.readouterr().out


# ════════════════════════════════════════════════════════════════
# L. 赛事日历 / 成长机会 两栏目 + 倒计时（2026-09-23 新增）
#    背景：用户要求《每日情报》新增与"官网/GitHub/保研"并列的两个栏目：
#      · 赛事日历（A/B 类竞赛）——「一天又一天的计时，还有 3 个月时开始提醒」；
#      · 成长机会 —— 省市官网/教育厅、人才培养计划、基金、企业培养计划、大厂活动。
#    这组用例锁死四类风险：
#      ① 新栏目能渲染、旧数据（无新两键）不能因此渲染失败；
#      ② 倒计时档位/文案必须按传入的 date 计算，绝不依赖真实当天日期（否则测试漂移）；
#      ③ 新条目的 url 仍受"每条必须带可点击链接"的硬约束；
#      ④ lint 对带嵌套 <div> 的 comp/opp 条目既不能误报、也不能漏报。
# ════════════════════════════════════════════════════════════════

# 固定"今天"，使倒计时断言不随运行日期漂移
_TODAY = "2026-09-23"

NEW_DATA = {
    "date": _TODAY,
    "ddls": [{"deadline": "9月25日 12:00 截止", "title": "创新大赛国赛报名",
              "url": "https://example.com/ddl1"}],
    "hfut": [{"channel": "科研院-通知公告", "title": "自然科学基金申报",
              "url": "https://example.com/hfut1", "tag": "funding"}],
    "competitions": [
        {"name": "全国大学生数学建模竞赛", "level": "A", "deadline": "2026-11-20",
         "deadline_label": "报名截止", "official_url": "https://www.mcm.edu.cn/",
         "organizer": "中国工业与应用数学学会", "note": "三人一队",
         "prep_links": [{"label": "历年真题", "url": "https://www.mcm.edu.cn/prep"}]},
        {"name": "蓝桥杯", "level": "B", "deadline": "2026-10-10",
         "official_url": "https://dasai.lanqiao.cn/"},
    ],
    "opportunities": [
        {"name": "安徽省大学生创新创业训练计划", "url": "https://jyt.ah.gov.cn/",
         "org": "安徽省教育厅", "type": "人才培养计划", "region": "安徽省",
         "deadline": "2026-10-31", "desc": "省级大创项目申报"},
        {"name": "华为天才少年", "url": "https://career.huawei.com/",
         "org": "华为", "type": "企业培养计划", "desc": "顶尖应届生计划"},
    ],
    "github": [{"name": "Bytebot", "desc": "某某机器人",
                "url": "https://github.com/x/Bytebot", "stars": "8,213"}],
    "baoyan": [{"title": "夏令营报名", "desc": "某夏令营",
                "url": "https://example.com/baoyan1"}],
}


class TestTemplateNewSections:
    """L 组：赛事日历 / 成长机会 两栏目与倒计时。"""

    def test_新两栏目最小数据可正常渲染(self):
        """含 2 赛事 + 2 机会的数据应渲染出完整 HTML，且每条都带官方链接。"""
        html = daily_intel_template.render(dict(NEW_DATA))
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        assert "赛事日历（A/B 类）" in html
        assert "成长机会" in html
        # 两条赛事 + 两条机会的链接都在
        for url in ("https://www.mcm.edu.cn/", "https://dasai.lanqiao.cn/",
                    "https://jyt.ah.gov.cn/", "https://career.huawei.com/"):
            assert f'href="{url}"' in html
        # 备考资料直达链接（prep_links）也要出现
        assert 'href="https://www.mcm.edu.cn/prep"' in html

    @pytest.mark.parametrize("deadline,exp_days,exp_urg,exp_text", [
        ("2026-09-23", 0, "critical", "今天截止"),   # 今天
        ("2026-10-07", 14, "critical", "还有 14 天"),  # 临界档上沿
        ("2026-10-23", 30, "urgent", "还有 30 天"),    # 紧迫档上沿
        ("2026-12-22", 90, "lead", "还有 90 天"),      # 提醒窗口上沿（3 个月）
        ("2026-12-23", 91, "far", "还有 91 天"),       # 出窗口
        ("2026-09-22", -1, "expired", "已结束"),       # 已过期
    ])
    def test_倒计时按传入日期计算不漂移(self, deadline, exp_days, exp_urg, exp_text):
        """days_until / comp_urgency / 渲染文案必须只依赖传入的 today（固定 2026-09-23）。"""
        assert daily_intel_template.days_until(deadline, _TODAY) == exp_days
        assert daily_intel_template.comp_urgency(exp_days) == exp_urg
        html = daily_intel_template.render({
            "date": _TODAY,
            "competitions": [{"name": "某赛事", "level": "A",
                              "deadline": deadline, "official_url": "https://x.cn/"}],
        })
        assert exp_text in html

    def test_倒计时_非法日期返回none且不抛错(self):
        """deadline 解析失败时 days_until 返回 None（不炸渲染），countdown 文案为空。"""
        assert daily_intel_template.days_until("不是日期", _TODAY) is None
        assert daily_intel_template.comp_urgency(None) == "none"
        assert daily_intel_template._countdown_text(None) == ""
        html = daily_intel_template.render({
            "date": _TODAY,
            "competitions": [{"name": "无日期赛事", "level": "A",
                              "official_url": "https://x.cn/"}]})
        assert "无日期赛事" in html

    def test_校验_赛事缺url抛错(self):
        """赛事没有 official_url / url 时必须抛 TemplateDataError（硬规矩）。"""
        d = dict(NEW_DATA)
        d["competitions"] = [{"name": "无链接赛事", "deadline": "2026-10-01"}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_机会缺url抛错(self):
        """成长机会没有 url 时同样必须抛错。"""
        d = dict(NEW_DATA)
        d["opportunities"] = [{"name": "无链接机会"}]
        with pytest.raises(daily_intel_template.TemplateDataError):
            daily_intel_template.render(d)

    def test_校验_赛事official_url可由url兜底(self):
        """official_url 缺省时应回退到 url（两者都是合法来源字段）。"""
        html = daily_intel_template.render({
            "date": _TODAY,
            "competitions": [{"name": "X", "url": "https://fallback.cn/"}]})
        assert 'href="https://fallback.cn/"' in html

    def test_转义防注入_赛事与机会标题(self):
        """标题里的 & / <script> 必须转义，原文不得出现在输出里。"""
        d = dict(NEW_DATA)
        d["competitions"] = [{"name": "A & B <script>alert(1)</script>",
                              "official_url": "https://x.cn/"}]
        d["opportunities"] = [{"name": "C & D <script>alert(2)</script>",
                               "url": "https://y.cn/"}]
        html = daily_intel_template.render(d)
        assert "&amp;" in html and "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html
        assert "<script>alert(2)</script>" not in html

    def test_lint_合规两栏目返回空(self):
        """正常渲染出的 comp/opp 条目内部有多层嵌套 div，lint 不得误报。"""
        html = daily_intel_template.render(dict(NEW_DATA))
        assert daily_intel_template.lint_html(html) == []

    def test_lint_检出赛事条目无链接(self):
        html = '<div class="comp"><div class="name">没链接的赛事</div></div>'
        problems = daily_intel_template.lint_html(html)
        assert problems and "赛事" in problems[0]

    def test_lint_检出机会条目无链接(self):
        html = '<div class="opp"><div class="name">没链接的机会</div></div>'
        problems = daily_intel_template.lint_html(html)
        assert problems and "成长机会" in problems[0]

    def test_lint_不因后续区块有链接而漏报末条(self):
        """回归：末条 comp 无链接、但后文 baoyan 卡片有链接时，仍须被检出。

        旧实现按「本块起点 → 下一块起点」切片，末条区间会延伸到文末，
        把后面区块的链接算进来，于是漏报 —— 这个用例专门锁死该假阴性。
        """
        html = ('<div class="content">'
                '<div class="comp"><div class="links">'
                '<a href="https://a.cn/">官方主页</a></div></div>'
                '<div class="comp"><div class="top">'
                '<span class="name">末条无链接赛事</span></div></div></div>'
                '<div class="card"><div class="url">'
                '<a href="https://baoyan.cn/">x</a></div></div>')
        problems = daily_intel_template.lint_html(html)
        assert any("赛事 第 2 条" in p for p in problems)

    def test_回归_旧数据无新两键也能渲染(self):
        """关键回归：老的数据文件只有 ddls/hfut/github/baoyan，
        不能因为新增两个栏目就渲染失败（缺键应按空数组处理）。"""
        old = {"date": "2026-09-23",
               "ddls": [{"deadline": "x", "title": "t", "url": "https://e.cn/1"}],
               "hfut": [{"channel": "c", "title": "t", "url": "https://e.cn/2"}],
               "github": [{"name": "n", "desc": "d", "url": "https://e.cn/3"}],
               "baoyan": [{"title": "t", "desc": "d", "url": "https://e.cn/4"}]}
        html = daily_intel_template.render(old)   # 不应抛错
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        # 新栏目以空态占位出现，不影响旧内容
        assert "90 天窗口内暂无" in html
        assert "今日未发现新的成长机会" in html
        assert daily_intel_template.lint_html(html) == []

    def test_回归_全空数组可渲染(self):
        """六个键都给空数组也必须出一个合法空壳页面。"""
        html = daily_intel_template.render({
            "date": "2026-09-23", "ddls": [], "hfut": [], "github": [], "baoyan": [],
            "competitions": [], "opportunities": []})
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")



