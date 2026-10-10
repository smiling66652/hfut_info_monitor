"""
合肥工业大学官网全量信息扫描爬虫 v3.1 (性能优化版)
===================================================
v3.1 优化（2026-05-30）：
- HTTP响应缓存：避免重复请求相同URL（节省30-50%请求时间）
- 正则预编译：页面解析速度提升3倍（15ms → 5ms/页）
- Playwright浏览器实例复用：避免重复启动（节省2-3秒/栏目）
- 线程池优化：max_workers 3→6，并发能力提升2倍
- HTTP连接复用：fetch_requests_bs4() 使用 requests.Session()
- 减少等待时间：scan_channel() 内 0.5s → 0.2s

v3.0 更新（参考 AI 浏览器自动化三段式架构）：
- Playwright 浏览器兜底：requests 失败时自动切换浏览器渲染
- 哨兵校验：扫描前先检查站点可达性
- 智能重试 + 指数退避（更健壮的网络容错）
- 每日摘要固化：0 Token 输出结构化 HTML 摘要
- 错误恢复：记录失败站点，下次优先重试
- 扫描日志：详细记录每次扫描的结果

用法：
  python hfut_scanner.py              # 增量扫描（默认）
  python hfut_scanner.py --full       # 全量扫描
  python hfut_scanner.py --pages 5    # 每个栏目最多扫5页（默认3）
  python hfut_scanner.py --browser    # 强制使用浏览器模式
  python hfut_scanner.py --summary    # 仅生成每日摘要（不扫描）
"""

import sys, io, os, json, re, ssl, time, hashlib, traceback, subprocess, shutil, threading, html
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlsplit, urlunsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

# 尝试导入 requests + BeautifulSoup（中间策略）
try:
    import requests
    from bs4 import BeautifulSoup
    _HAS_REQUESTS_BS4 = True
except ImportError:
    _HAS_REQUESTS_BS4 = False

# 部分子站证书链不完整，统一忽略校验并屏蔽由此产生的大量告警噪音
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:      # pragma: no cover
    pass

# 优化：线程级复用的 requests.Session（连接池复用）。
# scan_wrapper 跑在 ThreadPoolExecutor 里，各线程用 threading.local() 持有独立
# Session，避免多线程共享同一 Session 的线程安全问题；trust_env=False 保留（绕过本机代理）。
_req_session_local = threading.local()

def _ensure_utf8_console():
    """把控制台编码调成 UTF-8 —— 但**不替换** sys.stdout/stderr 对象。

    2026-09-23 修复（由补单元测试暴露）：原实现是
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    这会**劫持全局输出流** —— 它把宿主（pytest / IDE / 任何 import 本模块的程序）
    的 stdout 换成包在其 buffer 之上的新包装器；宿主随后关闭自己的捕获缓冲，
    本模块的包装器就指向已关闭的资源 → 宿主进程抛
    `ValueError: I/O operation on closed file`。
    pytest 下的表现是整个测试进程在收集阶段直接崩溃（一个用例都跑不了），
    日志里只有 `lost sys.stderr`，排查困难。

    改用 reconfigure() 原地改编码，对象不变，对宿主零副作用。
    同一模式在本项目 4 个文件中都存在（scanner / cli / report_gen / email），已于同日一并修复。
    """
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass          # 宿主流无 reconfigure（如 StringIO）→ 保持原样即可


_ensure_utf8_console()

# === 配置 ===

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# 禁用代理的 urllib opener：本机 http_proxy 对 hfut.edu.cn 返回 502，
# 直连反而只要 0.1s，因此所有请求一律绕过代理。
try:
    from urllib.request import build_opener, ProxyHandler, HTTPSHandler
    _NO_PROXY_OPENER = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=SSL_CTX),
    )
except Exception:      # pragma: no cover
    _NO_PROXY_OPENER = None

# 数据目录
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DATA_DIR, "hfut_scan_state.json")
OUTPUT_FILE = os.path.join(DATA_DIR, "hfut_latest_notices.json")
INDEX_FILE = os.path.join(DATA_DIR, "hfut_full_index.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "hfut_daily_summary.html")
LOG_FILE = os.path.join(DATA_DIR, "hfut_scan_log.json")
BROWSER_FAILED_FILE = os.path.join(DATA_DIR, "hfut_failed_sites.json")

# Playwright 引擎（延迟导入，只在需要时加载）
_pw = None
_pw_browser = None  # 优化：复用浏览器实例
_pw_context = None

# 优化：HTTP缓存（内存缓存，避免重复请求）
_url_cache = {}

# 优化：预编译正则表达式（提升性能）
_RE_LIST_ITEM_PATTERN = re.compile(
    r'<a[^>]*href=["\']([^"\']*info/\d+/\d+\.htm)["\'][^>]*>([^<]{6,})</a>',
    re.I
)
_RE_DATE_PATTERN = re.compile(r'(\d{4}[-/\.年]\d{1,2}[-/\.月]\d{1,2})')
_RE_NEWSTYPE_PATTERN = re.compile(
    r'<li[^>]*>\s*(?:<[^>]*>)*\s*(?:<span[^>]*>(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})</span>)?\s*(?:<[^>]*>)*\s*<a[^>]*href=["\']([^"\']*info/\d+/\d+\.htm)["\'][^>]*title=["\']([^"\']+)["\']',
    re.I | re.S
)

# ── 通用详情页锚点正则（兼容合工大多套 CMS）────────────────────────────────
# 注：原 `_RE_ANCHOR_DETAIL`（把 title 捕获组写成可选 + 非贪婪）已删除，
# 原因见下方「通用锚点解析（2026-09-22 重写）」段的说明（title 永远取不到
# 导致退化为锚点内部文本）。现改用 _RE_ANCHOR_TAG + _RE_ATTR_TITLE 切分解析。
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")
_RE_DATE_NEAR = re.compile(r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})")
_NAV_WORDS = ("首页", "上一页", "下一页", "尾页", "更多", "返回", "English", "旧版", "跳转")


def _clean(s: str) -> str:
    return _RE_WS.sub(" ", _RE_TAG.sub("", s or "")).strip()


# ── 标题清洗（2026-09-22）──────────────────────────────────────────────
# 主站聚合页的 <a> 内部含 <div class="notice-list-date">09<font>/</font>21<br><span>2026</span></div>，
# 一旦取不到 title 属性就会退化成「09/212026 真标题」；科研院模板则把
# <b>2026-09-21</b> 放在 </a> 前面，退化成「真标题 2026-09-21」。统一在这里剥掉。
_RE_TITLE_LEAD_DATE_GLUED = re.compile(r'^\s*\d{1,2}\s*/\s*\d{1,2}\s*\d{4}\s*')   # 09/212026
_RE_TITLE_LEAD_DATE_SPACED = re.compile(r'^\s*\d{1,2}\s*/\s*\d{1,2}\s+(?=\S)')    # 09/21 标题
_RE_TITLE_LEAD_DATE_DD_YM = re.compile(r'^\s*\d{1,2}\s+\d{4}[-/\.]\d{1,2}\s+')   # 21 2026-08 标题
_RE_TITLE_TRAIL_DATE = re.compile(r'\s*\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\s*$')      # 标题 2026-09-21


def clean_title(t: str) -> str:
    """清洗标题：剥日期前后缀 + 折叠被重复两遍的标题。"""
    t = _clean(t)
    t = _RE_TITLE_LEAD_DATE_GLUED.sub("", t)
    t = _RE_TITLE_LEAD_DATE_SPACED.sub("", t)
    t = _RE_TITLE_LEAD_DATE_DD_YM.sub("", t)
    t = _RE_TITLE_TRAIL_DATE.sub("", t)
    # 模板同时输出 title 属性与可见文本时会得到「X X」，折叠掉
    parts = t.split(" ")
    if len(parts) >= 4 and len(parts) % 2 == 0:
        h = len(parts) // 2
        if parts[:h] == parts[h:]:
            t = " ".join(parts[:h])
    return t.strip()


# ── URL 归一化（仅用作去重键）─────────────────────────────────────────
# 实测：主站聚合页对同一篇文章同时给出绝对 http:// 与相对(→https)链接，
# 科研院两个栏目也各出一份，导致同文被存两次（实测 26 组、索引虚高 26 条）。
def normalize_url(u: str) -> str:
    """去重键：统一 http/https、主机小写、去默认端口/末位斜杠/fragment。"""
    u = (u or "").strip()
    if not u:
        return u
    try:
        sp = urlsplit(u)
    except Exception:
        return u
    scheme = sp.scheme.lower()
    if scheme in ("http", "https"):
        scheme = "https"          # 同路径的 http/https 视为同一篇
    netloc = sp.netloc.lower()
    if netloc.endswith(":80") or netloc.endswith(":443"):
        netloc = netloc.rsplit(":", 1)[0]
    path = sp.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, sp.query, ""))


def item_key(item) -> str:
    return normalize_url(item.get("url", "")) or (item.get("title", "") or "")


# ── JS 空壳页识别（2026-09-22）─────────────────────────────────────────
# Vue/Nuxt 单页应用的静态 HTML 只有 <div id="__nuxt"> + loading 动画，
# 体积小、无详情页锚点。此前 fetch 认为「拿到 3KB = 成功」，于是浏览器
# 兜底永远不触发，SPA 栏目被判死（招标采购即此例）。
_RE_JS_SHELL = re.compile(r'id=["\'](__nuxt|__next|app|root)["\']', re.I)
# 另一类 SPA 不用 id="app"，而是靠 webpack 的 chunk 预取脚本（如 lib.hfut.edu.cn 的
# /portal/chunk-xxxx.js）。只看 id 会漏判，导致这类站点被当成"静态页拿到了"。
# ⚠️ 必须兼容**无引号属性**（lib.hfut.edu.cn 就是 href=/portal/chunk-xxx.js 这种写法）。
_RE_CHUNK_PREFETCH = re.compile(
    r'<link[^>]+href\s*=\s*["\']?[^"\'>\s]*chunk-[^"\'>\s]*\.js', re.I)


def _looks_like_js_shell(html: str) -> bool:
    if not html or len(html) > 60000:
        return False
    if _RE_DETAIL_URL.search(html):
        return False
    if _RE_JS_SHELL.search(html):
        return True
    # chunk 预取 ≥3 个且正文里没有详情页锚点 → 基本可判定为客户端渲染
    return len(_RE_CHUNK_PREFETCH.findall(html)) >= 3


# 「JS 未启用」兜底页（2026-09-23 新增，源于图书馆栏目 36/37 的误诊）：
# SPA 在无头浏览器里**渲染成功但应用没挂载**时，CDP 会返回一份带 noscript 兜底文案的
# HTML —— 它长度不小（实测 lib.hfut.edu.cn 120636 字符）、HTTP 也是 200，
# 于是 `len(html) > 200` 判定为"成功"，提取器却只能拿到 0 条，
# 最终表现成"这个栏目今天没内容"，**把基础设施故障伪装成内容为空**。
# 识别出来就可以当成失败上报（进 failed_channels），下次扫描自动重试。
_RE_JS_REQUIRED = re.compile(
    r"doesn'?t\s+work\s+properly\s+without\s+JavaScript"
    r"|Please\s+enable\s+it\s+to\s+continue"
    r"|请(?:开启|启用|打开)\s*(?:浏览器)?\s*JavaScript"
    r"|需要(?:开启|启用)\s*(?:浏览器)?\s*JavaScript",
    re.I,
)


def _looks_like_js_required(html: str) -> bool:
    """判断渲染结果是不是「请启用 JavaScript」兜底页（SPA 未挂载）。"""
    if not html:
        return False
    return bool(_RE_JS_REQUIRED.search(html))


# ── 通用锚点解析（2026-09-22 重写）────────────────────────────────────
# 原 `_RE_ANCHOR_DETAIL` 把 title 捕获组写成「可选 + 非贪婪」，正则引擎会
# 优先让 title 组匹配空串、再由 [^>]* 吞掉整个标签，于是 title="..." 永远
# 取不到，标题退化为锚点内部文本（含日期 div）。改为先切出 <a ...>…</a>，
# 再分别解析 href / title 属性，行为不再依赖正则回溯偏好。
_RE_ANCHOR_TAG = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.I | re.S)
_RE_ATTR_HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
_RE_ATTR_TITLE = re.compile(r'title\s*=\s*["\']([^"\']*)["\']', re.I)
# 锚点内层用 class 含 title 的 div 装标题（教务部 l3-news--title、宣城 news_title 等），
# 同层往往还有 text/desc 装摘要 —— 只取标题那个，否则会把摘要粘进标题。
_RE_INNER_TITLE = re.compile(
    r'<[^>]*class\s*=\s*["\'][^"\']*title[^"\']*["\'][^>]*>(.*?)</',
    re.I | re.S,
)
# 2026-09-23 修复（"标题粘连整段摘要"）：
#   计算机学院 ci.hfut.edu.cn 的锚点结构是
#       <a href="../info/1063/16610.htm">
#         <div class="media">
#           <div class="date"><b>22</b><br> 2026-09</div>
#           <div class="media-body">
#             <h5 class="mt-2 mb-3">计算机与信息学院第四届"院长奖学金"评审通知</h5>
#             <span class="news-text d-none d-sm-block"> 为全面贯彻党的教育方针……（数百字摘要）</span>
#   锚点**没有 title 属性**，标题装在没有语义 class（class="mt-2 mb-3"，不含 "title" 字样）
#   的 <h5> 里，摘要紧跟其后。旧回退链「title 属性 → class~title → 整个 inner」在第 2 级落空，
#   第 3 级就把标题和整段摘要糊成一条（实测索引里 20+ 条如此）。
# 两条补丁：① 认块级标题标签 <h1>~<h6>；② 取整段 inner 前先把"摘要容器"剥掉。
_RE_INNER_HEADING = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.I | re.S)

# 摘要容器：class 含这些词的元素整段删除，避免把摘要粘进标题。
# 注意用反向引用 \1 配对同名标签，避免把外层容器一起吃掉。
_RE_SUMMARY_BLOCK = re.compile(
    r"<(div|span|p|section)\b[^>]*class\s*=\s*[\"'][^\"']*"
    r"(?:news-text|news_desc|summary|abstract|excerpt|intro|zhaiyao|desc)"
    r"[^\"']*[\"'][^>]*>.*?</\1>",
    re.I | re.S,
)
# 标题开头的完整日期（新闻网 <i>2026年09月23日</i> 这类，取 inner 时会带上）
_RE_TITLE_LEAD_FULL_DATE = re.compile(
    r"^\s*\d{4}\s*[-/\.年]\s*\d{1,2}\s*[-/\.月]\s*\d{1,2}\s*日?\s*"
)
_RE_DETAIL_URL = re.compile(
    r'(?:(?P<y>\d{4})/(?P<m>\d{2})(?P<d>\d{2})/c\d+a\d+/page\.htm'
    r'|info/\d+/\d+\.htm)',
    re.I,
)


def _dedupe_repeated_title(t: str) -> str:
    """标题整段重复两次 → 只留一份。

    主站老页面的锚点里标题会出现两遍（实测：
    「关于2019年度相关证书材料归档工作的通知 关于2019年度相关证书材料归档工作的通知」），
    取整段 inner 时就会重复。只在"恰好等分为两份且完全相同"时折叠，不做模糊去重，
    避免误伤本身含重复词组的正常标题。
    """
    s = (t or "").strip()
    n = len(s)
    if n < 12:
        return s
    half = n // 2
    for cut in range(half + 2, max(6, half - 4), -1):
        left, right = s[:cut].strip(), s[cut:].strip()
        if left and left == right:
            return left
    return s


def _pick_title(attrs: str, inner: str) -> str:
    """从锚点属性与内层 HTML 中挑出标题（按可靠性从高到低逐级回退）。

    1. `title` 属性        —— 最可靠，站点自己声明的
    2. class 含 title 的元素（教务部 l3-news--title、宣城 news_title 等）
    3. 块级标题标签 <h1>~<h6>（计算机学院把标题放在无语义 class 的 <h5> 里）
    4. 剥掉摘要容器后的内层文本
    任一级取到 >=4 个字就用它，最后统一 clean_title + 去重复。
    """
    tm = _RE_ATTR_TITLE.search(attrs)
    if tm:
        t = clean_title(tm.group(1))
        if len(t) >= 4:
            return _dedupe_repeated_title(t)

    it_m = _RE_INNER_TITLE.search(inner)
    if it_m:
        t = clean_title(it_m.group(1))
        if len(t) >= 4:
            return _dedupe_repeated_title(t)

    hd_m = _RE_INNER_HEADING.search(inner)
    if hd_m:
        t = clean_title(hd_m.group(1))
        if len(t) >= 4:
            return _dedupe_repeated_title(t)

    stripped = _RE_SUMMARY_BLOCK.sub(" ", inner)
    t = _RE_TITLE_LEAD_FULL_DATE.sub("", clean_title(stripped))
    return _dedupe_repeated_title(t)


def extract_items_generic(html, base_url):
    """通用列表项提取：不依赖站点模板，只认详情页 URL 形态。

    合工大各子站并存多套 CMS，站点专属正则极易失效（历史上 20 个栏目
    一度只剩 4 条有数据）。这里统一按详情页 URL 形态提取，并优先从
    URL 中的 /YYYY/MMDD/ 还原发布日期（比解析页面文本更稳）。
    """
    items, seen = [], set()
    for m in _RE_ANCHOR_TAG.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        hm = _RE_ATTR_HREF.search(attrs)
        if not hm:
            continue
        href = hm.group(1).strip()
        dm = _RE_DETAIL_URL.search(href)
        if not dm:
            continue
        # 标题选择链见 _pick_title()（2026-09-23：原先只看 title 属性与 class~title，
        # 遇到 <h5> 装标题的模板就会把整段摘要粘进标题）
        title = _pick_title(attrs, inner)
        if len(title) < 4 or any(w in title for w in _NAV_WORDS):
            continue
        if dm.group("y"):
            date = f"{dm.group('y')}-{dm.group('m')}-{dm.group('d')}"
        else:
            ctx = html[max(0, m.start() - 400): m.end() + 200]
            date = _extract_date_from_ctx(ctx)
        full = urljoin(base_url, href)
        key = normalize_url(full)
        if key in seen:
            continue
        seen.add(key)
        items.append({"title": title, "url": full, "date": date})
    return items

def get_playwright():
    """延迟加载 Playwright（避免每次都启动）"""
    global _pw
    if _pw is None:
        try:
            from playwright.sync_api import sync_playwright
            _pw = True  # 标记为可用
            print("  [引擎] Playwright 已加载（浏览器兜底模式就绪）")
        except ImportError:
            print("  [警告] Playwright 未安装，将仅使用 requests 模式")
            print("         安装命令: pip install playwright && playwright install chromium")
            _pw = False
    return _pw

# === 关键词配置 ===

# 高优先级关键词（匹配到+10分）
HIGH_PRIORITY_KEYWORDS = [
    # 竞赛类
    "竞赛", "比赛", "挑战杯", "互联网+", "创新创业", "大创",
    "数学建模", "电子设计", "机器人大赛", "程序设计",
    "蓝桥杯", "ACM", "力学竞赛", "英语竞赛", "数学竞赛",
    "物理竞赛", "化学竞赛", "创新创业大赛", "创业大赛",
    "节能减排", "智能车", "光电设计", "机械创新",
    "机器学习", "深度学习", "神经网络",
    # 保研类
    "夏令营", "保研", "推免", "研究生招生", "预推免",
    "硕博连读", "推荐免试",
    # 奖学金
    "奖学金", "国奖", "国家奖学金", "励志奖学金",
    # 学术类
    "讲座", "学术报告", "论坛", "学术会议",
    "科研", "论文", "专利", "项目申报",
    "基金", "自然科学基金", "社科基金",
    # 就业类
    "实习", "招聘", "就业", "秋招", "春招",
    "宣讲会", "双选会", "人才引进",
    # AI/科技类
    "人工智能", "AI", "大数据", "云计算", "物联网",
    "智能制造", "新能源", "新材料",
]

# 相关关键词（匹配到+3分）
RELATED_KEYWORDS = [
    "机械", "电气", "自动化", "计算机", "工大",
    "考试", "四六级", "英语", "雅思", "托福", "GRE",
    "出国", "交流", "交换",
    "通知", "公示", "报名",
    "活动", "文化节", "科技节",
    "志愿服务", "社会实践",
    "研究生", "博士", "硕士",
    "实验室", "课题组",
    "答辩", "开题", "中期",
    "选课", "考试安排", "成绩",
]

# === 栏目配置 ===
# 2026-09-11 全面复核：原配置 20 个栏目中 13 个已失效（域名废弃/路径变更/解析器不匹配），
# 仅 4 个能采到数据。现按实测结果重写，详见 docs/巡检栏目复核-2026-09-11.md
# 每个栏目: (名称, 列表页URL, 来源域名, 栏目类型, CMS类型, 是否需要浏览器兜底)
CHANNELS = [
    # ---- 新闻网 ----
    ("新闻网-教学科研", "http://news.hfut.edu.cn/tzgg2.htm", "news.hfut.edu.cn", "academic", "news_cms", False),

    # ---- 研究生院 ----
    ("研究生院-通知公告", "http://yjsy.hfut.edu.cn/tzgg.htm", "yjsy.hfut.edu.cn", "notice", "standard", False),
    ("研究生院-新闻动态", "http://yjsy.hfut.edu.cn/xwdt.htm", "yjsy.hfut.edu.cn", "news", "standard", False),

    # ---- 教务部 ----
    ("教务部-教学要闻", "https://jwc.hfut.edu.cn/xwdt.htm", "jwc.hfut.edu.cn", "notice", "standard", False),
    ("教务部-通知", "https://jwc.hfut.edu.cn/tzgg/jyxml.htm", "jwc.hfut.edu.cn", "notice", "standard", False),

    # ---- 团委（原 856/859 是学院列表页，实为 738/736）----
    ("团委-通知公告", "http://tuanwei.hfut.edu.cn/736/list.htm", "tuanwei.hfut.edu.cn", "notice", "standard", False),
    ("团委-工作动态", "http://tuanwei.hfut.edu.cn/738/list.htm", "tuanwei.hfut.edu.cn", "activity", "standard", False),

    # ---- 科研院 ----
    ("科研院-通知公告", "https://kyy.hfut.edu.cn/tzgg/list.htm", "kyy.hfut.edu.cn", "academic", "standard", False),
    ("科研院-成果与项目", "https://kyy.hfut.edu.cn/cgzl/list.htm", "kyy.hfut.edu.cn", "academic", "standard", False),

    # ---- 社科处 ----
    ("社科处-通知公告", "https://skc.hfut.edu.cn/tzgg/list.htm", "skc.hfut.edu.cn", "notice", "standard", False),

    # ---- 研究生招生 ----
    ("研招-通知公告", "http://yjszs.hfut.edu.cn/13534/list.htm", "yjszs.hfut.edu.cn", "notice", "standard", False),

    # ---- 主站 ----
    ("主站-新闻动态", "https://www.hfut.edu.cn/xwdt/jdxw.htm", "www.hfut.edu.cn", "news", "standard", False),

    # ---- 电气与自动化工程学院（原 dqxy.hfut.edu.cn 域名已废弃，现为 ea）----
    ("电气学院-通知公告", "https://ea.hfut.edu.cn/bkspy/tzgg.htm", "ea.hfut.edu.cn", "notice", "standard", False),
    ("电气学院-学院公告", "https://ea.hfut.edu.cn/xwzx/xygg.htm", "ea.hfut.edu.cn", "notice", "standard", False),

    # ---- 计算机与信息学院（原 /tzgg/list.htm 404，现为 /ggxw/tzgg.htm）----
    ("计算机学院-通知公告", "https://ci.hfut.edu.cn/ggxw/tzgg.htm", "ci.hfut.edu.cn", "notice", "standard", False),

    # ---- 机械工程学院（原 /tzgg/list.htm 404，现为 /xsgz/xwtz.htm）----
    ("机械学院-新闻通知", "https://jxxy.hfut.edu.cn/xsgz/xwtz.htm", "jxxy.hfut.edu.cn", "notice", "standard", False),

    # ---- 学工部（原 /tzgg/list.htm 404，现用思政动态）----
    ("学工部-思政动态", "https://xgb.hfut.edu.cn/szjy/szdt.htm", "xgb.hfut.edu.cn", "notice", "standard", False),

    # ---- 国际合作与交流处（原 /tzgg/list.htm 404，实为 sy.htm）----
    ("国际处-交流动态", "https://international.hfut.edu.cn/sy.htm", "international.hfut.edu.cn", "notice", "standard", False),

    # ── 2026-09-13 扩展：浏览器逐级扫描 + 直连探测新增（实测可达）──
    # 主站未覆盖栏目（www.hfut.edu.cn，列表页已 urllib 验证可达）
    ("主站-通知公告", "https://www.hfut.edu.cn/xwdt/tzgg.htm", "www.hfut.edu.cn", "notice", "standard", False),
    ("主站-媒体工大", "https://www.hfut.edu.cn/xwdt/mtgd.htm", "www.hfut.edu.cn", "news", "standard", False),
    ("主站-成果动态", "https://www.hfut.edu.cn/cgdt/cgdt.htm", "www.hfut.edu.cn", "news", "standard", False),
    ("主站-科学研究", "https://www.hfut.edu.cn/kxyj/kygk.htm", "www.hfut.edu.cn", "academic", "standard", False),
    ("主站-学校概况", "https://www.hfut.edu.cn/xxgk.htm", "www.hfut.edu.cn", "news", "standard", False),   # 更新低频
    ("主站-机构设置", "https://www.hfut.edu.cn/jgsz.htm", "www.hfut.edu.cn", "news", "standard", False),   # 更新低频

    # 校外二级单位站点（列表页已 urllib 直连验证）
    ("继续教育-通知公告", "http://jxjy.hfut.edu.cn/tzgg1.htm", "jxjy.hfut.edu.cn", "notice", "standard", False),
    ("总务部-通知公告", "http://zwb.hfut.edu.cn/index/tzgg.htm", "zwb.hfut.edu.cn", "notice", "standard", False),
    ("人事处教师工作部-通知公告", "http://rsc.hfut.edu.cn/xwgg/tzgg.htm", "rsc.hfut.edu.cn", "notice", "standard", False),
    ("基建与房屋管理处-通知公告", "http://jjc.hfut.edu.cn/tzgg.htm", "jjc.hfut.edu.cn", "notice", "standard", False),
    ("信息化办-通知公告", "http://it.hfut.edu.cn/gzdt/tzgg.htm", "it.hfut.edu.cn", "notice", "standard", False),
    ("在职硕士-新闻动态", "http://zyxw.hfut.edu.cn/xwdt.htm", "zyxw.hfut.edu.cn", "news", "standard", False),
    ("学科建设-通知公告", "http://xkb.hfut.edu.cn/tzgg1.htm", "xkb.hfut.edu.cn", "notice", "standard", False),
    ("宣传部-通知公告", "http://xcb.hfut.edu.cn/tzgg.htm", "xcb.hfut.edu.cn", "notice", "standard", False),
    ("国际教育-通知公告", "https://iec.hfut.edu.cn/main.htm", "iec.hfut.edu.cn", "news", "standard", False),

    # 浏览器兜底档：列表由 JS 异步渲染 / 仅浏览器可达（fetch_with_fallback 第三级）
    # 注：gdjy 已改为 JSON 接口直连（见 _scan_gdjy_api），不走浏览器；此处 False 仅为语义清晰
    # ⚠️ 2026-09-22 修正：gdjy 的 443 证书链不完整（CERTIFICATE_VERIFY_FAILED），
    #    但 http 直连正常（200/154KB）。原写 https 导致该栏目自 09-15 起被误判为死链。
    #    这里必须用 http。
    ("就业网-招聘信息", "http://gdjy.hfut.edu.cn/", "gdjy.hfut.edu.cn", "notice", "standard", False),  # bysjy SaaS，/module/getnotices 接口直连
    # ⚠️ 2026-09-22 修正：zb 为 Nuxt SPA，静态 HTML 只有 <div id="__nuxt"> 空壳，
    #    必须经 CDP 渲染（_looks_like_js_shell 负责识别并放行到浏览器兜底）。
    ("招标采购-通知公告", "http://zb.hfut.edu.cn/cms/", "zb.hfut.edu.cn", "notice", "standard", True),  # Vue SPA（_nuxt），CDP 渲染 + 专属提取器
    # ⚠️ 2026-09-22 修正：xc 的 443 直接拒连（WinError 10061），但 http 直连正常
    #    （200/54KB）。原「校外不可达、需 WebVPN」的结论是把协议问题误判成了网络问题。
    ("宣城校区-新闻通知", "http://xc.hfut.edu.cn/", "xc.hfut.edu.cn", "notice", "standard", False),      # 网站群 CMS；http 直连可达

    # ⚠️ 2026-09-22 经 WebVPN 资源清单核对后新增。
    #    图书馆 lib.hfut.edu.cn 为 React SPA（/portal/chunk-*.js），公开可达但静态
    #    HTML 里没有列表，必须 CDP 渲染；渲染后含竞赛（如"AI+信息素养"大赛、讯飞杯
    #    英语口语大赛）、培训讲座、资源动态、开馆/维修改造通知 —— 对学生价值高。
    ("图书馆-通知公告", "https://lib.hfut.edu.cn/", "lib.hfut.edu.cn", "notice", "standard", True),

    # ── 以下栏目已移除 ──────────────────────────────────────────────
    # 图书馆-通知公告 : lib.hfut.edu.cn 校外不可达（A记录 210.45.242.3，HTTP 超时/HTTPS 证书错）
    # 双创中心-通知公告: cxcy.hfut.edu.cn 域名已废弃（DNS 解析为 IPv6 link-local）
    # 就业网-招聘信息  : job.hfut.edu.cn 域名已废弃；替代站 gdjy.hfut.edu.cn 为 bysjy 第三方
    #                    SaaS，列表由 JS 异步渲染，静态抓取为空，需浏览器引擎后另行接入
]

# === 核心函数 ===

def fetch(url, retries=3, timeout=15, use_cache=True):
    """获取网页内容（智能重试 + 指数退避 + 缓存优化）"""
    # 优化：检查缓存
    if use_cache and url in _url_cache:
        return _url_cache[url]
    
    for i in range(retries):
        try:
            req = Request(url, headers=HEADERS)
            # 关键：本机环境存在代理（对合工大域名返回 502），必须显式禁用
            resp = urlopen(req, timeout=timeout, context=SSL_CTX) \
                if _NO_PROXY_OPENER is None \
                else _NO_PROXY_OPENER.open(req, timeout=timeout)
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            m = re.search(r'charset=([\w-]+)', content_type, re.I)
            if m:
                charset = m.group(1)
            raw = resp.read()
            for enc in [charset, "utf-8", "gbk", "gb2312"]:
                try:
                    content = raw.decode(enc)
                    # 优化：存入缓存
                    if use_cache:
                        _url_cache[url] = content
                    return content
                except Exception:
                    continue
            content = raw.decode("utf-8", errors="replace")
            if use_cache:
                _url_cache[url] = content
            return content
        except Exception as e:
            if i < retries - 1:
                wait = min(1 * (2 ** i) + 0.5, 8)  # 指数退避: 1.5s, 2.5s, 4.5s...
                print(f"     ⚠ 请求失败({i+1}/{retries}): {str(e)[:50]}, {wait:.1f}s后重试")
                time.sleep(wait)
            else:
                return None

def fetch_requests_bs4(url, retries=2, timeout=15, use_cache=True):
    """使用 requests + BeautifulSoup 获取页面（中间策略，比urllib更稳健）"""
    if not _HAS_REQUESTS_BS4:
        return None
    
    # 优化：检查缓存
    if use_cache and url in _url_cache:
        return _url_cache[url]

    REQS_HEADERS = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": HEADERS["Accept"],
        "Accept-Language": HEADERS["Accept-Language"],
        "Connection": "keep-alive",
    }

    # 优化：复用线程级 Session（连接池跨请求复用，比每次新建更快）
    session = getattr(_req_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False   # 绕过本机代理（对合工大域名返 502）
        _req_session_local.session = session

    for i in range(retries):
        try:
            resp = session.get(url, headers=REQS_HEADERS, timeout=timeout, verify=False)
            resp.raise_for_status()
            # 尝试多种编码
            for enc in [resp.encoding or "utf-8", "utf-8", "gbk", "gb2312"]:
                try:
                    content = resp.content.decode(enc)
                    if use_cache:
                        _url_cache[url] = content
                    return content
                except Exception:
                    continue
            content = resp.text
            if use_cache:
                _url_cache[url] = content
            return content
        except Exception as e:
            if i < retries - 1:
                wait = min(1 * (2 ** i) + 0.5, 8)
                time.sleep(wait)
            else:
                return None
    return None

_playwright_instance = None  # 新增：保存 playwright 实例，真正实现复用

def get_browser_context():
    """优化：获取复用的浏览器上下文（真正复用，只启动一次）"""
    global _pw_browser, _pw_context, _playwright_instance
    if _pw_browser is None:
        try:
            from playwright.sync_api import sync_playwright
            _playwright_instance = sync_playwright().start()
            _pw_browser = _playwright_instance.chromium.launch(headless=True)
            _pw_context = _pw_browser.new_context(
                user_agent=HEADERS["User-Agent"],
                ignore_https_errors=True,
            )
            print("  [优化] Playwright 浏览器实例已创建（可复用，仅启动一次）")
        except Exception as e:
            print(f"  [错误] 创建浏览器实例失败: {e}")
            return None
    return _pw_context

def fetch_browser(url, timeout=20):
    """使用 Playwright 浏览器渲染获取页面（兜底方案）- 优化复用实例"""
    context = get_browser_context()
    if context is None:
        return None
    
    try:
        page = context.new_page()
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        
        # 等待内容加载（哨兵：等待列表项出现）
        try:
            page.wait_for_selector("a[href*='info/'], a[href*='page.htm'], a[href*='list.htm']", 
                                  timeout=5000)
        except Exception:
            pass  # 不一定有标准选择器，继续
        
        html = page.content()
        page.close()  # 优化：关闭页面但保留浏览器
        return html
    except Exception as e:
        print(f"     ⚠ 浏览器渲染失败: {str(e)[:60]}")
        return None

# ── CDP 浏览器兜底（替代 Playwright，无需安装；直连本机真实/无头 Edge）──
_NODE_EXE = shutil.which("node") or r"C:\Users\Matebook\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
_CDP_FETCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdp_fetch.mjs")

def fetch_cdp(url, timeout=25):
    """通过 CDP 直连 Edge 获取 JS 渲染后的 HTML（兜底引擎，优先于 Playwright）"""
    if not os.path.exists(_NODE_EXE):
        return None
    if not os.path.exists(_CDP_FETCH):
        return None
    try:
        env = dict(os.environ)
        env.setdefault("HFUT_CDP_PORT", "9333")
        r = subprocess.run(
            [_NODE_EXE, _CDP_FETCH, url],
            capture_output=True, text=True, timeout=timeout + 20, env=env,
        )
        if r.returncode == 0 and len(r.stdout) >= 200:
            return r.stdout
        if r.stderr:
            err = r.stderr.strip().splitlines()[-1][:80]
            print(f"     ⚠ CDP兜底未取到内容: {err}")
    except subprocess.TimeoutExpired:
        print(f"     ⚠ CDP兜底超时({timeout + 20}s)")
    except Exception as e:
        print(f"     ⚠ CDP兜底异常: {str(e)[:60]}")
    return None

# 扫描编排用：确保串行浏览器循环期间有一个 CDP 可达的 Edge（避免每次 fetch 都拉起/销毁）
_cdp_child = None

def _cdp_port_up(port="9333"):
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def ensure_cdp_browser_up(port="9333"):
    """扫描前确保有 CDP 可达的 Edge；没有则拉起无头实例。返回是否本次新拉起。"""
    global _cdp_child
    if _cdp_port_up(port):
        return False
    edge = None
    for c in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge Beta\Application\msedge.exe"]:
        if os.path.exists(c):
            edge = c
            break
    if not edge:
        return False
    prof = os.path.join(os.environ.get("TEMP", "/tmp"), "hfut_cdp_scan_profile")
    try:
        os.makedirs(prof, exist_ok=True)
    except Exception:
        pass
    # 把校园代理透传给无头 Edge（xc/zb 需经校园代理/WebVPN 才可达）；
    # 父进程 urllib 走 _NO_PROXY_OPENER 直连，互不影响。
    child_env = dict(os.environ)
    _proxy = (child_env.get("HTTPS_PROXY") or child_env.get("HTTP_PROXY")
              or child_env.get("https_proxy") or child_env.get("http_proxy") or "")
    if _proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            child_env[k] = _proxy
        child_env.pop("NO_PROXY", None)
        child_env.pop("no_proxy", None)
    _cdp_child = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env,
    )
    for _ in range(50):
        time.sleep(0.4)
        if _cdp_port_up(port):
            return True
    return False

def close_cdp_browser():
    """扫描结束后关闭本次拉起的无头 Edge（不触碰用户正在用的实例）"""
    global _cdp_child
    if _cdp_child is not None:
        try:
            _cdp_child.kill()
        except Exception:
            pass
        _cdp_child = None

def fetch_with_fallback(url, use_browser_fallback=False, timeout=15):
    """三级策略获取页面：urllib → requests+BS4 → CDP/Playwright 兜底

    2026-09-22 修正：SPA 站点（Nuxt/Vue）静态 HTML 会返回 200 + 3KB 空壳，
    旧逻辑 `len(html) >= 200` 即判定成功并 return，浏览器兜底永不触发，
    于是 SPA 栏目永远抓不到数据（招标采购被误判为死链正因此）。
    现在用 _looks_like_js_shell() 识别空壳，识别到就当作"未取到"继续降级。
    """
    html = None
    shell = None      # 记住"拿到但只是空壳"的那份 HTML，作为最后兜底

    # 策略1: urllib.request（最轻量、已缓存）
    html = fetch(url, retries=2, timeout=timeout)
    if html and len(html) >= 200:
        if not _looks_like_js_shell(html):
            return html
        shell = html
        print(f"     🧩 检测到 JS 空壳页({len(html)}字符)，需浏览器渲染")

    # 策略2: requests + BeautifulSoup（更稳健的中间策略）
    if _HAS_REQUESTS_BS4:
        print(f"     🔄 urllib失败，尝试requests+BS4...")
        html = fetch_requests_bs4(url, retries=2, timeout=timeout)
        if html and len(html) >= 200 and not _looks_like_js_shell(html):
            print(f"     ✅ requests+BS4 成功")
            return html
        else:
            if html and len(html) >= 200:
                shell = shell or html
            print(f"     ⚠ requests+BS4 也失败({len(html) if html else 0}字符)")

    # 策略3: 浏览器兜底（CDP 直连 Edge 优先；Playwright 仅作备选）
    if use_browser_fallback:
        print(f"     🔄 切换到浏览器渲染模式(CDP)...")
        html = fetch_cdp(url, timeout=timeout)
        if html and len(html) > 200:
            if _looks_like_js_required(html):
                # 渲染"成功"了，但 SPA 没挂载（只有「请启用 JavaScript」兜底文案）。
                # 这种情况绝不能当成功 —— 否则提取器拿 0 条，最终表现成
                # "这个栏目今天没内容"，把基础设施故障伪装成内容为空
                # （2026-09-23 lib.hfut.edu.cn 就是这么被误诊的：120636 字符、
                #   HTTP 200、提取 0 条、连续两次完全复现）。
                print(f"     🔴 CDP 返回了「请启用 JavaScript」兜底页({len(html)}字符)，"
                      f"判定为渲染失败（SPA 未挂载）")
                html = None
            else:
                print(f"     ✅ CDP 渲染成功({len(html)}字符)")
                return html
        # Playwright 备选（若已安装）
        pw_available = get_playwright()
        if pw_available:
            print(f"     🔄 CDP 不可用，回退 Playwright...")
            html = fetch_browser(url, timeout=timeout)
            if html and len(html) > 200:
                return html

    # 关键：没有浏览器兜底的栏目（requests 频道）拿到空壳时，不能返回 None ——
    # 否则"检测到空壳"反而把原本还能歪打正着的栏目判死。退而返回空壳，
    # 让专属/通用提取器尽力而为。
    if shell:
        print(f"     ↩ 无浏览器兜底，退回空壳页({len(shell)}字符)供解析器尝试")
        return shell
    return None

def extract_jwc_dynamic(url):
    """使用 Playwright 浏览器提取教务部JS动态渲染的列表页 - 优化复用实例"""
    from playwright.sync_api import sync_playwright
    
    # 排除列表（导航栏、页脚等）
    EXCLUDE = {
        '机械工程学院', '材料科学与工程学院', '电气与自动化工程学院',
        '计算机与信息学院（人工智能学院）', '土木与水利工程学院', '化学与化工学院',
        '马克思主义学院', '经济学院', '文法学院', '外国语学院', '管理学院',
        '仪器科学与光电工程学院', '建筑与艺术学院', '资源与环境工程学院',
        '数学学院', '食品与生物工程学院', '汽车与交通工程学院', '医学院',
        '软件学院', '光电工程学院', '物理学院', '空天信息学院',
        '部门简介', '人员分工', '机构设置', '党建专栏', '组织架构',
        '学习园地', '支部活动', 'Copyright', '技术支持', '行政楼',
        '地址', '邮编', '上城信息', '更多', '返回', '首页', '空天信息',
        '机构设置', '党建专栏', '学院教务', '审核评估', '规章制度',
        '文档下载', '办事指南', '在线咨询', '教学督导', '新闻动态',
        '当前位置', '首页',
    }
    
    context = get_browser_context()
    if context is None:
        return []
    
    try:
        page = context.new_page()
        page.goto(url, timeout=20000, wait_until='networkidle')
        page.wait_for_timeout(3000)
        
        items = []
        seen = set()
        
        # 教务部使用 [class*="list"] a 选择器
        links = page.query_selector_all('[class*="list"] a, [class*="news"] a')
        
        for el in links:
            try:
                href = el.get_attribute('href') or ''
                text = el.inner_text().strip()
                
                if not href or href == '#':
                    continue
                
                # 文本可能混合了日期、标题、摘要，需要拆分
                # 格式如: "27\n2026-04\n标题文字\n摘要文字..."
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                # 找到标题行（通常是最长的非数字行）
                title = ''
                date = ''
                for line in lines:
                    # 纯数字行是日期或序号
                    if re.match(r'^\d+$', line):
                        continue
                    # 年月格式
                    if re.match(r'^\d{4}-\d{1,2}$', line):
                        date = line
                        continue
                    # 完整日期
                    dm = re.search(r'(\d{4}[-/\.年]\d{1,2}[-/\.月]\d{1,2})', line)
                    if dm and len(line) < 20:
                        date = dm.group(1)
                        continue
                    # 标题候选（长度适中，排除导航文字）
                    if 6 < len(line) <= 80 and line not in EXCLUDE:
                        if not title or len(line) > len(title):
                            title = line
                
                if not title or title in EXCLUDE:
                    continue
                
                full_url = urljoin(url, href)
                if full_url in seen or full_url == url:
                    continue
                
                seen.add(full_url)
                items.append({"title": title, "url": full_url, "date": date})
            except Exception:
                continue
        
        page.close()
        return items
    except Exception as e:
        print(f"     ⚠ 浏览器提取失败: {str(e)[:60]}")
        return []

def load_failed_sites():
    """加载上次失败的站点记录

    2026-10-10 加固（失败自锁日期衰减）：原机制 fail_count≥2 即永久跳过且缓存
    永不清理——就业网 2026-09-24 因站点瞬时超时失败 2 次后，尽管接口早已恢复，
    此后每次扫描仍被直接跳过（误伤半月）。现加载时惰性检查 last_fail：
    距今 ≥7 天的条目视为过期，fail_count 重置为 0（不删除记录，保留排查痕迹），
    使"长期失败"与"偶发失败"可自然区分，栏目恢复后能自动重新参与扫描。
    """
    if not os.path.exists(BROWSER_FAILED_FILE):
        return {}
    try:
        with open(BROWSER_FAILED_FILE, "r", encoding="utf-8") as f:
            sites = json.load(f)
    except Exception:
        return {}
    now = datetime.now()
    decayed = []
    for name, info in sites.items():
        try:
            last = datetime.strptime(info.get("last_fail", ""), "%Y-%m-%d %H:%M")
        except Exception:
            continue          # 日期缺失/格式异常 → 保留原状，交给既有逻辑处理
        if (now - last).days >= 7 and info.get("fail_count", 0) > 0:
            info["fail_count"] = 0
            decayed.append(name)
    if decayed:
        print(f"     ♻ 失败缓存过期重置(≥7天): {', '.join(decayed)}")
        save_failed_sites(sites)      # 回写，避免每次扫描重复打印
    return sites

def save_failed_sites(sites):
    """保存失败站点记录（原子写）"""
    _atomic_write_json(BROWSER_FAILED_FILE, sites, compact=True)

def extract_items_standard(html, base_url):
    """合工大标准CMS列表页提取（兼容多种格式）- 优化使用预编译正则"""
    items = []
    
    # 优化：使用预编译的正则表达式
    for m in _RE_NEWSTYPE_PATTERN.finditer(html):
        date, href, title = m.group(1), m.group(2), m.group(3).strip()
        full_url = urljoin(base_url, href)
        items.append({"title": title, "url": full_url, "date": date or ""})
    
    # 如果上面没匹配到，尝试其他格式
    if not items:
        for m in _RE_LIST_ITEM_PATTERN.finditer(html):
            href, title = m.group(1), m.group(2).strip()
            if len(title) >= 4 and not title.startswith(("首", "上", "下", "尾")):
                full_url = urljoin(base_url, href)
                after = html[m.end():m.end()+100]
                date_match = _RE_DATE_PATTERN.search(after)
                items.append({"title": title, "url": full_url, "date": date_match.group(1) if date_match else ""})
    
    return items

def extract_items_news_cms(html, base_url):
    """新闻网CMS列表页提取 - 优化使用预编译正则"""
    items = []
    
    for m in _RE_NEWSTYPE_PATTERN.finditer(html):
        date, href, title = m.group(1), m.group(2), m.group(3).strip()
        if len(title) >= 6:
            full_url = urljoin(base_url, href)
            items.append({"title": title, "url": full_url, "date": date or ""})
    
    # 如果上面没匹配到，尝试其他格式
    if not items:
        for m in _RE_LIST_ITEM_PATTERN.finditer(html):
            href, title = m.group(1), m.group(2).strip()
            if len(title) >= 6:
                full_url = urljoin(base_url, href)
                before = html[max(0, m.start()-80):m.start()]
                date_match = _RE_DATE_PATTERN.search(before)
                if not date_match:
                    after = html[m.end():m.end()+80]
                    date_match = _RE_DATE_PATTERN.search(after)
                items.append({"title": title, "url": full_url, "date": date_match.group(1) if date_match else ""})
    
    return items

def extract_items_from_list_html(html, base_url, cms_type="standard"):
    """统一提取入口（站点专属解析器 + 通用解析器，互补合并）

    2026-09-22 修正：原实现是「专属正则命中就不再看通用」，而
    `_RE_NEWSTYPE_PATTERN` 的日期捕获组是**可选**的 —— 机械学院把日期写成
    `<a ... title="X"><span>2026-09-15</span><p>X</p></a>`（日期在 <a> 内部），
    正则便以 date=None 匹配成功，产出 49 条「无日期」条目，通用解析器永远
    不执行；而这些条目的 date 为空 → 永不进入「近7天/今日新增」。
    现在两者都跑，按归一化 URL 合并：优先保留有日期的版本。
    """
    if cms_type == "news_cms":
        items = extract_items_news_cms(html, base_url)
    elif cms_type == "jwc_dynamic":
        items = []
    else:
        items = extract_items_standard(html, base_url)

    generic = extract_items_generic(html, base_url)

    merged, order = {}, []
    for it in list(items) + list(generic):
        k = normalize_url(it.get("url", ""))
        if not k:
            continue
        if k not in merged:
            merged[k] = it
            order.append(k)
        else:
            prev = merged[k]
            if not (prev.get("date") or "").strip() and (it.get("date") or "").strip():
                merged[k] = it          # 用带日期的版本覆盖空日期版本
    out = [merged[k] for k in order]
    for it in out:
        it["title"] = clean_title(it.get("title", ""))
    return out

# ── 站点专属提取器（JS 渲染站点）──────────────────────────────────────────

def extract_for_domain(domain, html, base_url, cms_type="standard"):
    """按域名分派提取器：zb/xc/lib 用专属解析；其余走标准/通用提取。"""
    if domain == "zb.hfut.edu.cn":
        items = extract_zb_html(html, base_url)
        return items if items else extract_items_generic(html, base_url)
    if domain == "xc.hfut.edu.cn":
        items = extract_xc_html(html, base_url)
        return items if items else extract_items_generic(html, base_url)
    if domain == "lib.hfut.edu.cn":
        items = extract_lib_html(html, base_url)
        return items if items else extract_items_generic(html, base_url)
    return extract_items_from_list_html(html, base_url, cms_type)


# ── 图书馆（lib.hfut.edu.cn，React SPA，2026-09-22 新增）──────────────────
# 页面为客户端渲染（/portal/chunk-*.js），通用解析器拿不到；
# 渲染后条目形态：
#   <span title="标题" class="fc-3 dbName"><a href="/portal/noticedetail/1748.../2014...">标题</a></span>
#   <span class="dbTime"><span>2026</span><span>01-22</span></span>
_RE_LIB_DETAIL = re.compile(
    r'<a[^>]+href=["\']([^"\']*/portal/(?:noticedetail|app/activitydetail)/\d+/\d+)["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_RE_LIB_DATE = re.compile(
    r'class=["\'][^"\']*dbTime[^"\']*["\'][^>]*>\s*<span[^>]*>(\d{4})</span>\s*'
    r'<span[^>]*>(\d{1,2})\s*-\s*(\d{1,2})</span>',
    re.I | re.S,
)
_RE_LIB_PUBTIME = re.compile(r'发布时间[:：]\s*(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})')
# 活动类卡片用起止时间区间，起始日单独放在 <span class="start">2026-09-15 00:00</span>，
# 而"至"在另一个 <span class="end"> 里（所以不能用"时间+至"的连续正则）。
_RE_LIB_RANGE = re.compile(
    r'class=["\']start["\'][^>]*>\s*(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})')
# 活动卡片把区间写在正文里：`活动时间：2026-07-03 11:00 至 2026-09-18 00:00`，取起始日。
_RE_LIB_RANGE2 = re.compile(
    r'(?:活动时间|讲座时间|培训时间)[:：]\s*(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})')
# 卡片左上角的类型标签（"主题活动" / "影片欣赏" / "通知" / "公告" ...），
# 它们和被包在 <a> 里的标题被渲染成同一段文本，需要剥掉。
_LIB_CARD_LABELS = ("主题活动", "影片欣赏", "通知", "公告", "资讯", "讲座", "培训", "活动")
# 同一张卡片上、锚点之前可能有个 title 属性直接写着标题（如新闻公告区）
_RE_LIB_NEAR_TITLE = re.compile(r'title=["\']([^"\']{6,80})["\'][^>]*>\s*<a\b', re.I)


def extract_lib_html(html, base_url):
    """图书馆通知/活动列表提取（须先经 CDP 渲染）。"""
    items, seen = [], set()
    for m in _RE_LIB_DETAIL.finditer(html):
        url, inner = m.group(1), m.group(2)
        # 标题优先级：锚点前紧邻的 title 属性 → 锚点文本
        nm = None
        head = html[max(0, m.start() - 200):m.start()]
        tm = _RE_LIB_NEAR_TITLE.search(head)
        if tm:
            nm = tm.group(1)
        title = clean_title(nm or inner)
        # 剥掉卡片类型标签前缀
        for lab in _LIB_CARD_LABELS:
            if title.startswith(lab) and len(title) > len(lab) + 5:
                title = title[len(lab):].strip()
                break
        if len(title) < 4 or any(w in title for w in _NAV_WORDS):
            continue
        key = normalize_url(urljoin(base_url, url))
        if key in seen:
            continue
        seen.add(key)
        # 日期必须就近向后找（dbTime 紧跟在标题之后）；向后找不到才回看，
        # 否则 ±700 的宽窗口会捞到相邻卡片的日期，把一批条目的日期全串成同一天。
        # 日期必须就近向后找（dbTime 紧跟在标题之后）；向后找不到才回看，
        # 否则 ±700 的宽窗口会捞到相邻卡片的日期，把一批条目的日期全串成同一天。
        # 窗口 600：活动卡片把"活动时间："写在 descBox 里，实测距锚点约 280 字。
        AFT = html[m.end(): m.end() + 600]
        BEF = html[max(0, m.start() - 300):m.start()]
        dm = (_RE_LIB_DATE.search(AFT) or _RE_LIB_PUBTIME.search(AFT)
              or _RE_LIB_RANGE.search(AFT) or _RE_LIB_RANGE2.search(AFT)
              or _RE_LIB_DATE.search(BEF) or _RE_LIB_PUBTIME.search(BEF)
              or _RE_LIB_RANGE.search(BEF) or _RE_LIB_RANGE2.search(BEF))
        if dm:
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        else:
            date = _extract_date_from_ctx(
                html[max(0, m.start() - 300): m.end() + 300])
        items.append({"title": title, "url": urljoin(base_url, url), "date": date})
    return items


_RE_ZB_DETAIL = re.compile(
    r'<a[^>]+href=["\'](https://zb\.hfut\.edu\.cn/cms/news/detail\?id=\d+)["\'][^>]*>([^<]{4,})</a>',
    re.I,
)

def extract_zb_html(html, base_url):
    """招标采购 Vue SPA：真实通知在 <a href='.../cms/news/detail?id=XXXXXX'>标题</a>。"""
    items, seen = [], set()
    for m in _RE_ZB_DETAIL.finditer(html):
        url, raw_title = m.group(1), m.group(2)
        title = _clean(raw_title)
        if len(title) < 4 or any(w in title for w in _NAV_WORDS):
            continue
        if url in seen:
            continue
        seen.add(url)
        ctx = html[max(0, m.start() - 160): m.end() + 160]
        dm = _RE_DATE_NEAR.search(ctx)
        date = (f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
                if dm else "")
        items.append({"title": title, "url": url, "date": date})
    return items


# 宣城校区（网站群 CMS）：详情页形如 /87/ed/c1984a34797/page.htm（日期不一定嵌在 URL）
_RE_XC_DETAIL = re.compile(
    r'<a[^>]+href=["\']([^"\']*?/(?:(\d{4})/(\d{2})(\d{2})/)?c\d+a\d+/page\.htm)["\'][^>]*?'
    r'(?:title=["\']([^"\']*)["\'])?[^>]*>(.*?)</a>',
    re.I | re.S,
)

# 部分子站把日期拆成两个 div（且命名反直觉，"日"在前、"年-月"在后）：
#   宣城校区 : <div class="news_year">10</div><div class="news_days">2026-09</div>
#   教务部   : <div class="l3-news--day">20</div><div class="l3-news--month">2026-09</div>
# 单一 YYYY-MM-DD 正则必然漏掉 → 补一个反序模式。
_RE_DATE_REVERSED = re.compile(
    r'class\s*=\s*["\'][^"\']*?(?:news_year|l3-news--day)[^"\']*["\'][^>]*>\s*(\d{1,2})\s*<'
    r'.{0,300}?'
    r'class\s*=\s*["\'][^"\']*?(?:news_days|l3-news--month)[^"\']*["\'][^>]*>'
    r'\s*(\d{4})\s*[-/\.]\s*(\d{1,2})\s*<',
    re.I | re.S,
)


# 2026-09-23 修复（"257 条条目没有日期，永远进不了近 7 天"）：
# 上面两条模式之外，实测还漏掉两种真实模板，都必须靠"日期容器 + 结构模式"识别：
#   ① 主站 www.hfut.edu.cn —— 月/日 年
#        <div class="notice-list-date">09<font>/</font>23<br><span>2026</span></div>
#      去标签后是 `09 / 23 2026`（月在前、日在后、年在最后），上面两条都不匹配。
#   ② 计算机学院 ci.hfut.edu.cn —— 日 年-月
#        <div class="date"><b>22</b><br> 2026-09</div>
#      去标签后是 `22 2026-09`（日在最前、年后是月）。旧实现把 class 名写死成
#      news_year / l3-news--day 白名单，`class="date"` 直接落空 —— 这就是漏掉的主因，
#      所以这次改成按"容器里出现的数字形态"判断，不再依赖 class 白名单。
_RE_DATE_CONTAINER = re.compile(
    r"<(div|span|p|i|em|b)\b[^>]*class\s*=\s*[\"'][^\"']*"
    r"(?:notice-list-date|news_year|news_days|news-date|date|time|day|month)"
    r"[^\"']*[\"'][^>]*>(.*?)</\1>",
    re.I | re.S,
)
# 月/日 年   （09 / 23 2026）
_RE_DATE_MDY = re.compile(r"(\d{1,2})\s*[/\-\.]\s*(\d{1,2})\s+(\d{4})")
# 日 年-月   （22 2026-09）
_RE_DATE_DYM = re.compile(r"\b(\d{1,2})\s+(\d{4})\s*[/\-\.]\s*(\d{1,2})\b")
# 中文完整日期（新闻网 <i>2026年09月23日</i>）。
# 注意原始 _RE_DATE_NEAR 的分隔符集合是 [-/\.]，**不含「年月日」**，
# 所以这类条目过去只能靠 URL 里嵌的 /YYYY/MMDD/ 蒙对日期；
# 一旦详情页挂在别的域名（如 info/1026/6339.htm）就整条丢日期。
_RE_DATE_CN = re.compile(r"(\d{4})\s*[年/\-\.]\s*(\d{1,2})\s*[月/\-\.]\s*(\d{1,2})\s*日?")


def _mk_date(y, m, d) -> str:
    """校验并规范化日期；不合法返回 ""（宁可没有日期，也不要瞎猜一个）。"""
    try:
        y, m, d = int(y), int(m), int(d)
    except (TypeError, ValueError):
        return ""
    if not (2000 <= y <= 2100) or not (1 <= m <= 12) or not (1 <= d <= 31):
        return ""
    return f"{y:04d}-{m:02d}-{d:02d}"


def _ctx_to_text(ctx: str) -> str:
    """把邻近 HTML 压成纯文本 —— 标签替换成**空格**而不是删掉。

    删掉会把 `<font>/</font>` 两侧的数字粘起来（`09/23` → `0923`），必须留空格。
    """
    return _RE_WS.sub(" ", _RE_TAG.sub(" ", ctx or "")).strip()


def _extract_date_from_ctx(ctx: str) -> str:
    """从锚点邻近 HTML 还原发布日期。

    优先序（可靠性从高到低）：
      1. 完整日期 `YYYY-MM-DD` / `YYYY年MM月DD日`
      2. 拆分模板（宣城 news_year/news_days、教务部 l3-news--day/--month）
      3. **日期容器内的**「月/日 年」（主站）与「日 年-月」（计算机学院）
    第 3 步只在 `class` 含 date/time/year/month/day 的容器**内部**匹配，
    不拿全文乱猜，避免把页码、浏览量当成日期。
    """
    m = _RE_DATE_CN.search(ctx)
    if m:
        got = _mk_date(m.group(1), m.group(2), m.group(3))
        if got:
            return got
    m = _RE_DATE_NEAR.search(ctx)
    if m:
        got = _mk_date(m.group(1), m.group(2), m.group(3))
        if got:
            return got
    m2 = _RE_DATE_REVERSED.search(ctx)
    if m2:
        got = _mk_date(m2.group(2), m2.group(3), m2.group(1))
        if got:
            return got
    for cm in _RE_DATE_CONTAINER.finditer(ctx):
        text = _ctx_to_text(cm.group(2))
        md = _RE_DATE_MDY.search(text)          # 09 / 23 2026
        if md:
            got = _mk_date(md.group(3), md.group(1), md.group(2))
            if got:
                return got
        dy = _RE_DATE_DYM.search(text)          # 22 2026-09
        if dy:
            got = _mk_date(dy.group(2), dy.group(3), dy.group(1))
            if got:
                return got
    return ""


def extract_xc_html(html, base_url):
    """宣城校区详情页提取（兼容 /YYYY/MMDD/cXXXaYYY/page.htm 与 /NN/cXXXaYYY/page.htm）。"""
    items, seen = [], set()
    for m in _RE_XC_DETAIL.finditer(html):
        url, year, mm, dd, title_attr, inner = m.groups()
        title = clean_title(title_attr) if title_attr else clean_title(inner)
        if len(title) < 4 or any(w in title for w in _NAV_WORDS):
            continue
        if url in seen:
            continue
        seen.add(url)
        if year:
            date = f"{year}-{mm}-{dd}"
        else:
            # 窗口开到 600：日期 div 在锚点前约 350 字符处
            ctx = html[max(0, m.start() - 600): m.end() + 200]
            date = _extract_date_from_ctx(ctx)
        items.append({"title": title, "url": urljoin(base_url, url), "date": date})
    return items


# ── 就业网（gdjy）JSON 接口直连 ──────────────────────────────────────────
# bysjy SaaS：列表由 /module/getnotices AJAX 渲染，静态抓取为空。
# 接口：GET /module/getnotices?start=0&count=50&k=&type_id=XXX
#       请求需带 Referer + X-Requested-With: XMLHttpRequest，否则 400。
# 首页含所有栏目 type_id（/module/news?type_id=XXX），逐个拉取后合并。
# ⚠️ 2026-09-22：gdjy 站证书链不完整，https 直接 CERTIFICATE_VERIFY_FAILED；
#    改 http 后 200/154KB 正常。_GDJY_DETAIL 是给用户点的第三方详情页，保留 https。
_GDJY_HOME = "http://gdjy.hfut.edu.cn/"
_GDJY_API = "http://gdjy.hfut.edu.cn/module/getnotices"
_GDJY_DETAIL = "https://hfgydx.bysjy.com.cn/detail/news?id={notice_id}&type_id={type}"

def _gdjy_get(url, referer=None, retries=2, timeout=15):
    # 2026-10-10 加固：原实现单次尝试、无重试——站点瞬时超时一次就整栏目判失败，
    # 而 fail_count≥2 会触发"永久跳过"自锁（就业网 2026-09-24 失败两次后，
    # 尽管站点早已恢复，此后每次扫描都直接跳过、报告恒显示未取到）。
    # 加一次重试即可阻断「偶发超时 → 自锁」的链条。
    req = Request(url, headers={
        "User-Agent": HEADERS["User-Agent"],
        "Referer": referer or _GDJY_HOME,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })
    for attempt in range(retries):
        try:
            resp = (_NO_PROXY_OPENER.open(req, timeout=timeout)
                    if _NO_PROXY_OPENER is not None
                    else urlopen(req, timeout=timeout, context=SSL_CTX))
            return resp.read().decode("utf-8", "ignore")
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None

def fetch_gdjy_api(max_type_ids=24):
    """拉取就业网全部栏目通知，返回 items 列表（已去重）。"""
    home = _gdjy_get(_GDJY_HOME)
    if not home:
        return []
    type_ids = []
    for m in re.finditer(r'type_id=(\d+)', home):
        tid = m.group(1)
        if tid not in type_ids:
            type_ids.append(tid)
        if len(type_ids) >= max_type_ids:
            break
    items, seen = [], set()
    for tid in type_ids:
        api = f"{_GDJY_API}?start=0&count=50&k=&type_id={tid}"
        txt = _gdjy_get(api, referer=f"{_GDJY_HOME}module/news?type_id={tid}")
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        # 兼容两种返回形态：{"code":1,"data":[...]} 或直接是数组
        if isinstance(data, dict):
            rows = data.get("data")
        else:
            rows = data
        if not isinstance(rows, list):
            continue
        for row in rows:
            nid = row.get("notice_id") or row.get("id")
            name = (row.get("notice_name") or row.get("title") or "").strip()
            ctime = row.get("create_time") or row.get("add_time") or ""
            dtype = row.get("type") or tid
            if not nid or not name:
                continue
            url = _GDJY_DETAIL.format(notice_id=nid, type=dtype)
            if url in seen:
                continue
            seen.add(url)
            dm = re.search(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})', ctime)
            date = (f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
                    if dm else "")
            items.append({"title": name, "url": url, "date": date})
    return items

def _scan_gdjy_api(channel_name, domain, channel_type, since_date=None, max_pages=3):
    """就业网专用扫描：JSON 接口直连，绕过浏览器与校园代理。"""
    print(f"\n  [{channel_name}]")
    print(f"     URL: {_GDJY_HOME} (JSON 接口直连，绕过校园代理)")
    items = fetch_gdjy_api()
    if not items:
        print(f"     未提取到条目（接口可能需校园网/证书校验）")
        return []
    for item in items:
        item["channel"] = channel_name
        item["domain"] = domain
        item["type"] = channel_type
        item["score"] = score_item(item)
        item["category"] = categorize_item(item)
        item["date_parsed"] = parse_date(item.get("date", "")) is not None
        item["scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if since_date:
        kept = []
        for item in items:
            d = parse_date(item.get("date", ""))
            if d is None or d >= since_date:
                kept.append(item)
        items = kept
    print(f"     合计 {len(items)} 条（去重后）")
    return items


# ── 图书馆（lib.hfut.edu.cn）JSON 接口直连（2026-10-10，替代 CDP 渲染）──────
# ⚠️ 该站有两个独立于"SPA 不挂载"的硬事实：
# 1) TLS：服务器只支持 TLSv1.2 + 老 cipher（AES128-SHA，无 ECDHE），
#    Python OpenSSL 默认 SECLEVEL=2 直接拒绝握手（SSLV3_ALERT_HANDSHAKE_FAILURE），
#    而 Windows curl 走 Schannel 能通 → 必须用 SECLEVEL=0 的专用 context；
# 2) 渲染：portal 前端是 Vue SPA，无头 Edge 里始终不挂载（120636 字符的
#    noscript 兜底页，字节数恒定），CDP 兜底对它无效（2026-09-23 起必现）。
# 接口方案：portal 前端本体就是打 /svc/<服务码>/... 的 JSON 网关，
# 无需登录即可访问 front* 端点（从 /portal/index.js 静态分析得出）：
#   POST /svc/portal/newsInfo/frontListNewsInfo
#   body: {"unitId":"hfut","page":1,"limit":50,"status":"1","catalogIds":[...],"contentFlag":"1"}
# 详情页 permalink：/portal/noticedetail/{catalogId}/{id}（页面按 unitId+id 取内容，
# 首段 navs 仅用于面包屑，用 catalogId 顶替也能正常打开）。
_LIB_API_URL = "https://lib.hfut.edu.cn/svc/portal/newsInfo/frontListNewsInfo"
_LIB_DETAIL_URL = "https://lib.hfut.edu.cn/portal/noticedetail/{catalog}/{id}"
# "新闻公告"栏目的 catalogId（站内导航固定的雪花 ID，2026-10-10 实测 515 条）
_LIB_CATALOG_NEWS = "1748162997496451074"


def _lib_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")   # 允许服务器唯一支持的 AES128-SHA
    except Exception:
        pass
    return ctx


def _lib_api_post(url, body, timeout=20):
    """图书馆 JSON 网关 POST（直连 + SECLEVEL=0 TLS，各重试 1 次）。"""
    from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={
        "User-Agent": HEADERS["User-Agent"],
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://lib.hfut.edu.cn/portal/",
    })
    last_err = None
    for _ in range(2):
        try:
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=_LIB_SSL_CTX_INTERNAL))
            return opener.open(req, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
            time.sleep(1)
    print(f"     ⚠ 图书馆接口请求失败: {last_err}")
    return None


# 模块级共享的 SECLEVEL=0 context（_lib_api_post 内引用）
_LIB_SSL_CTX_INTERNAL = _lib_ssl_ctx()


def fetch_lib_api(max_pages=3, limit=50):
    """拉取图书馆「新闻公告」栏目通知，返回 items 列表（已去重）。"""
    items, seen = [], set()
    for page in range(1, max_pages + 1):
        txt = _lib_api_post(_LIB_API_URL, {
            "unitId": "hfut", "page": page, "limit": limit, "status": "1",
            "catalogIds": [_LIB_CATALOG_NEWS], "contentFlag": "1",
        })
        if not txt:
            break
        try:
            data = json.loads(txt)
        except Exception:
            break
        rows = (data.get("data") or {}).get("list") or []
        if not rows:
            break
        for row in rows:
            nid, title = row.get("id"), (row.get("title") or "").strip()
            if not nid or len(title) < 4:
                continue
            url = _LIB_DETAIL_URL.format(catalog=row.get("catalogId") or _LIB_CATALOG_NEWS, id=nid)
            if url in seen:
                continue
            seen.add(url)
            # releaseDate 形如 "2026-10-08 11:00:00"，取前 10 位即 ISO 日期
            items.append({"title": title, "url": url,
                          "date": (row.get("releaseDate") or "")[:10]})
        # 接口按发布时间倒序，翻页前若最后一页日期已足够旧即可停止
        if len(rows) < limit:
            break
    return items


def _scan_lib_api(channel_name, domain, channel_type, since_date=None, max_pages=3):
    """图书馆专用扫描：JSON 接口直连，绕过 SPA 渲染与 SECLEVEL 限制。"""
    print(f"\n  [{channel_name}]")
    print(f"     URL: https://lib.hfut.edu.cn/portal/ (JSON 接口直连，绕过 SPA 渲染)")
    items = fetch_lib_api(max_pages=max_pages)
    if not items:
        print(f"     未提取到条目（接口可能变更，回退 HTML 解析需 CDP）")
        return []
    for item in items:
        item["channel"] = channel_name
        item["domain"] = domain
        item["type"] = channel_type
        item["score"] = score_item(item)
        item["category"] = categorize_item(item)
        item["date_parsed"] = parse_date(item.get("date", "")) is not None
        item["scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if since_date:
        kept = []
        for item in items:
            d = parse_date(item.get("date", ""))
            if d is None or d >= since_date:
                kept.append(item)
        items = kept
    print(f"     合计 {len(items)} 条（去重后）")
    return items


def get_total_pages(html, base_url, cms_type="standard"):
    """从列表页 HTML 中获取总页数"""
    pages_match = re.search(r'/\s*(\d+)\s*页', html)
    if pages_match:
        return int(pages_match.group(1))
    
    page_links = re.findall(r'href=["\']([^"\']*(?:/\d+\.htm|list\.htm\?page=\d+))["\']', html, re.I)
    max_page = 1
    for link in page_links:
        m = re.search(r'/(\d+)\.htm', link)
        if m:
            p = int(m.group(1))
            if 1 < p < 1000:
                max_page = p
        m = re.search(r'page=(\d+)', link)
        if m:
            p = int(m.group(1))
            if 1 < p < 1000:
                max_page = p
    return max_page

def parse_date(date_str):
    """解析各种格式的日期字符串"""
    if not date_str:
        return None
    date_str = date_str.strip()
    date_str = re.sub(r'[年月]', '-', date_str).replace('日', '').strip()
    date_str = re.sub(r'[/.]', '-', date_str)
    for fmt in ["%Y-%m-%d", "%Y%m%d"]:
        try:
            return datetime.strptime(date_str, fmt)
        except Exception:
            continue
    parts = date_str.split('-')
    if len(parts) == 3:
        try:
            return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass
    return None

def score_item(item):
    """根据关键词对条目进行相关度评分"""
    title = item.get("title", "")
    score = 0
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in title:
            score += 10
    for kw in RELATED_KEYWORDS:
        if kw in title:
            score += 3
    return score

def categorize_item(item):
    """对条目进行智能分类"""
    title = item.get("title", "")
    score = item.get("score", 0)
    
    if any(kw in title for kw in ["竞赛", "比赛", "挑战杯", "互联网+", "蓝桥杯", "ACM", 
                                    "数学建模", "电子设计", "机器人大赛", "节能减排",
                                    "机械创新", "智能车", "光电设计", "程序设计"]):
        return "competition"
    if any(kw in title for kw in ["夏令营", "保研", "推免", "预推免", "研究生招生", "硕博连读"]):
        return "baoyan"
    if any(kw in title for kw in ["讲座", "学术报告", "论坛", "学术会议"]):
        return "academic"
    if any(kw in title for kw in ["基金", "自然科学基金", "社科基金", "项目申报"]):
        return "funding"
    if any(kw in title for kw in ["奖学金", "国奖", "励志"]):
        return "scholarship"
    if any(kw in title for kw in ["实习", "招聘", "宣讲会", "双选会", "秋招", "春招"]):
        return "career"
    if score >= 10:
        return "important"
    return "other"

def scan_channel(channel_name, list_url, domain, channel_type, cms_type="standard", 
                 max_pages=3, since_date=None, use_browser_fallback=False):
    """扫描单个栏目（含哨兵校验和浏览器兜底）"""
    # 就业网 / 图书馆走 JSON 接口直连（绕过浏览器与校园代理）
    if domain == "gdjy.hfut.edu.cn":
        return _scan_gdjy_api(channel_name, domain, channel_type, since_date, max_pages)
    if domain == "lib.hfut.edu.cn":
        # 图书馆 SPA 在无头浏览器里始终不挂载，且 TLS 需 SECLEVEL=0，
        # 两条路都走不通 → 直接打 /svc JSON 网关（2026-10-10 修复）
        return _scan_lib_api(channel_name, domain, channel_type, since_date, max_pages)
    print(f"\n  [{channel_name}]")
    print(f"     URL: {list_url}")
    
    # 哨兵检查
    if cms_type == "jwc_dynamic":
        # JS动态渲染页面直接走浏览器
        if not get_playwright():
            print(f"     🔴 Playwright 不可用，跳过 JS 动态页面")
            return []
        print(f"     🌐 JS动态页面，使用浏览器渲染")
        items = extract_jwc_dynamic(list_url)
        if items:
            for item in items:
                item["channel"] = channel_name
                item["domain"] = domain
                item["type"] = channel_type
                item["score"] = score_item(item)
                item["category"] = categorize_item(item)
                item["date_parsed"] = parse_date(item.get("date", "")) is not None
                item["scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # 去重
            seen = set()
            unique = []
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    unique.append(item)
            
            print(f"     合计 {len(unique)} 条")
            return unique
        else:
            print(f"     未提取到条目")
            return []
    
    # 注：已移除「哨兵 HEAD 预检」——它对每个栏目多打一次 HTTP 往返，纯属重复。
    # 改为「真实请求失败时再判定不可达」：fetch_with_fallback 返回 None 即视为该栏目
    # 本页失败，scan_channel 据此 break 并把栏目计为失败（计入 failed_channels）。

    all_items = []

    for page in range(1, max_pages + 1):
        if page == 1:
            url = list_url
        else:
            base = list_url
            if base.endswith('/'):
                base = base + '1.htm'
            if re.search(r'\.htm$', base) and not re.search(r'/\d+\.htm$', base):
                url = base.replace('.htm', f'/{page}.htm')
            elif re.search(r'/\d+\.htm$', base):
                url = re.sub(r'/\d+\.htm$', f'/{page}.htm', base)
            else:
                sep = '&' if '?' in base else '?'
                url = f"{base}{sep}page={page}"
        
        html = fetch_with_fallback(url, use_browser_fallback=use_browser_fallback)
        if not html:
            print(f"     P{page}: 无法获取内容")
            break

        items = extract_for_domain(domain, html, url, cms_type)
        
        # 第一页没提取到，尝试找其他列表页
        if not items and page == 1:
            list_links = re.findall(r'href=["\']([^"\']*list\.htm)["\']', html, re.I)
            if list_links:
                for ll in list_links:
                    full_ll = urljoin(url, ll)
                    alt_html = fetch_with_fallback(full_ll, use_browser_fallback=use_browser_fallback)
                    if alt_html:
                        items = extract_for_domain(domain, alt_html, full_ll, cms_type)
                        if items:
                            break
        
        if not items:
            if page == 1:
                print(f"     P{page}: 未提取到条目")
            break
        
        for item in items:
            item["channel"] = channel_name
            item["domain"] = domain
            item["type"] = channel_type
            item["score"] = score_item(item)
            item["category"] = categorize_item(item)
            item["date_parsed"] = parse_date(item.get("date", "")) is not None
            item["scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        all_items.extend(items)
        print(f"     P{page}: +{len(items)} 条")
        
        # 增量扫描
        if since_date:
            oldest = None
            for item in items:
                d = parse_date(item.get("date", ""))
                if d and (oldest is None or d < oldest):
                    oldest = d
            if oldest and oldest < since_date:
                print(f"     到达增量边界 ({oldest.strftime('%Y-%m-%d')}), 停止")
                break
        
        time.sleep(0.2)  # 优化：减少等待时间（原0.5s）
    
    # 去重（按归一化 URL：http/https、主机大小写、末位斜杠视为同一篇）
    seen = set()
    unique = []
    for item in all_items:
        k = normalize_url(item.get("url", ""))
        if k not in seen:
            seen.add(k)
            unique.append(item)
    
    print(f"     合计 {len(unique)} 条（去重后）")
    return unique

def generate_daily_summary(index, new_items=None):
    """生成每日摘要 HTML（0 Token，纯脚本逻辑）"""
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    week_ago_str = week_ago.strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    
    # 筛选最近7天的条目
    recent = []
    for item in index:
        d = parse_date(item.get("date", ""))
        if d and d >= week_ago:
            recent.append(item)
    
    # 按分类分组
    categories = {
        "competition": {"label": "🏆 竞赛信息", "icon": "🏆", "items": [], "color": "#e74c3c"},
        "baoyan": {"label": "🎓 保研推免", "icon": "🎓", "items": [], "color": "#9b59b6"},
        "academic": {"label": "📖 学术讲座", "icon": "📖", "items": [], "color": "#3498db"},
        "funding": {"label": "💰 科研基金", "icon": "💰", "items": [], "color": "#27ae60"},
        "scholarship": {"label": "🎖 奖学金", "icon": "🎖", "items": [], "color": "#f39c12"},
        "career": {"label": "💼 就业招聘", "icon": "💼", "items": [], "color": "#1abc9c"},
        "important": {"label": "⭐ 重要通知", "icon": "⭐", "items": [], "color": "#e67e22"},
        "other": {"label": "📋 其他通知", "icon": "📋", "items": [], "color": "#95a5a6"},
    }
    
    for item in recent:
        cat = item.get("category", "other")
        if cat in categories:
            categories[cat]["items"].append(item)
    
    # 按分数排序
    for cat in categories.values():
        cat["items"].sort(key=lambda x: x.get("score", 0), reverse=True)
    
    # 统计
    total_recent = len(recent)
    high_score = sum(1 for i in recent if i.get("score", 0) >= 10)
    
    # 新增标记（统一用归一化 URL，与索引去重键保持一致；用 .get 避免缺 url 键 KeyError）
    new_urls = {normalize_url(item.get("url", "")) for item in (new_items or [])}

    # 生成HTML（用 list 累积 + 末尾 join，避免千条循环里反复拼接字符串）
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>合工大官网每日摘要 - {today_str}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ 
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: #e0e0e0; min-height: 100vh; padding: 20px;
}}
.container {{ max-width: 900px; margin: 0 auto; }}
.header {{ 
    text-align: center; padding: 30px 0 20px;
    border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 25px;
}}
.header h1 {{ font-size: 28px; color: #fff; margin-bottom: 8px; }}
.header .meta {{ color: #888; font-size: 14px; }}
.stats {{
    display: flex; gap: 15px; justify-content: center; margin: 20px 0;
    flex-wrap: wrap;
}}
.stat-card {{
    background: rgba(255,255,255,0.05); border-radius: 12px; padding: 15px 25px;
    text-align: center; border: 1px solid rgba(255,255,255,0.1);
    min-width: 120px;
}}
.stat-card .number {{ font-size: 32px; font-weight: bold; color: #4fc3f7; }}
.stat-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
.category {{ margin-bottom: 25px; }}
.category-header {{ 
    display: flex; align-items: center; gap: 10px;
    padding: 12px 15px; border-radius: 10px;
    background: rgba(255,255,255,0.03); margin-bottom: 10px;
    border-left: 4px solid {{ "{{color}}" }};
}}
.category-header .icon {{ font-size: 22px; }}
.category-header .title {{ font-size: 16px; font-weight: 600; color: #fff; }}
.category-header .count {{ 
    margin-left: auto; background: rgba(255,255,255,0.1); 
    padding: 2px 10px; border-radius: 12px; font-size: 13px; color: #aaa;
}}
.item {{
    display: flex; align-items: flex-start; gap: 10px;
    padding: 10px 15px; margin: 4px 0; border-radius: 8px;
    transition: background 0.2s;
}}
.item:hover {{ background: rgba(255,255,255,0.05); }}
.item .date {{ 
    font-size: 12px; color: #666; white-space: nowrap; min-width: 75px; padding-top: 2px;
}}
.item .content {{ flex: 1; }}
.item .title-text {{ font-size: 14px; color: #ddd; }}
.item .title-text a {{ color: #ddd; text-decoration: none; }}
.item .title-text a:hover {{ color: #4fc3f7; }}
.item .tags {{ margin-top: 4px; display: flex; gap: 5px; flex-wrap: wrap; }}
.tag {{
    font-size: 11px; padding: 1px 8px; border-radius: 10px;
    background: rgba(255,255,255,0.08); color: #999;
}}
.tag.new {{ background: rgba(46, 204, 113, 0.2); color: #2ecc71; }}
.tag.score {{ background: rgba(231, 76, 60, 0.2); color: #e74c3c; }}
.footer {{
    text-align: center; padding: 20px 0; margin-top: 20px;
    border-top: 1px solid rgba(255,255,255,0.1); color: #555; font-size: 12px;
}}
.empty {{ text-align: center; color: #555; padding: 20px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📋 合工大官网每日摘要</h1>
        <div class="meta">生成时间: {today.strftime("%Y-%m-%d %H:%M")} | 覆盖最近7天 ({week_ago_str} ~ {today_str})</div>
    </div>
    
    <div class="stats">
        <div class="stat-card">
            <div class="number">{total_recent}</div>
            <div class="label">近7天通知</div>
        </div>
        <div class="stat-card">
            <div class="number">{high_score}</div>
            <div class="label">高相关度</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(new_items or [])}</div>
            <div class="label">今日新增</div>
        </div>
        <div class="stat-card">
            <div class="number">{len(index)}</div>
            <div class="label">总索引量</div>
        </div>
    </div>
""")
    
    # 输出各分类
    for cat_key, cat_data in categories.items():
        items = cat_data["items"]
        if not items:
            continue
        
        color = cat_data["color"]
        parts.append(f"""
    <div class="category">
        <div class="category-header" style="border-left-color: {color}">
            <span class="icon">{cat_data['icon']}</span>
            <span class="title">{cat_data['label']}</span>
            <span class="count">{len(items)} 条</span>
        </div>
""")
        
        for item in items:
            is_new = normalize_url(item.get("url", "")) in new_urls
            score = item.get("score", 0)
            date = html.escape(item.get("date", ""))
            title = html.escape(item.get("title", ""))
            url = html.escape(item.get("url", ""))
            channel = html.escape(item.get("channel", ""))
            
            new_tag = '<span class="tag new">NEW</span>' if is_new else ''
            score_tag = f'<span class="tag score">+{score}</span>' if score >= 10 else ''
            
            parts.append(f"""
        <div class="item">
            <div class="date">{date}</div>
            <div class="content">
                <div class="title-text"><a href="{url}" target="_blank">{title}</a></div>
                <div class="tags">
                    <span class="tag">{channel}</span>
                    {new_tag}{score_tag}
                </div>
            </div>
        </div>
""")
        
        parts.append("""
    </div>
""")
    
    if total_recent == 0:
        parts.append('<div class="empty">近7天没有新的通知公告</div>')

    parts.append(f"""
    <div class="footer">
        合工大官网信息自动巡检系统 v3.1 (性能优化版) | 数据由 Python + Playwright 自动采集<br>
        本摘要由脚本自动生成（0 Token），无需 AI 介入
    </div>
</div>
</body>
</html>
""")
    
    summary_html = "".join(parts)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(summary_html)
    print(f"\n  📋 每日摘要已生成: {SUMMARY_FILE}")
    return SUMMARY_FILE

def save_scan_log(result):
    """保存扫描日志（用于追踪历史和排查问题）"""
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []

    logs.append(result)
    # 只保留最近30条
    logs = logs[-30:]

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

def _atomic_write_json(path, obj, compact=False):
    """原子写 JSON：先写 .tmp 再 os.replace，避免中途被杀导致整文件截断。
    compact=True 用 separators=(',',':') 替代 indent=2，体积约减 30~40% 且写入更快。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_scan": None, "last_scan_time": None, "total_items": 0}

def save_state(state):
    state["last_scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_json(STATE_FILE, state, compact=True)

def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_index(index):
    _atomic_write_json(INDEX_FILE, index, compact=True)

# === 主程序 ===

def main():
    args = sys.argv[1:]
    full_scan = "--full" in args
    summary_only = "--summary" in args
    force_browser = "--browser" in args
    max_pages = 3
    for arg in args:
        if arg.startswith("--pages"):
            max_pages = int(arg.split("=")[1]) if "=" in arg else 3
    
    print("=" * 60)
    print("合工大官网信息扫描器 v3.1 (性能优化版)")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"模式: {'摘要生成' if summary_only else ('全量扫描' if full_scan else '增量扫描')}")
    print(f"引擎: {'浏览器优先' if force_browser else 'requests + 浏览器兜底'}")
    print(f"每栏目最大页数: {max_pages}")
    print("=" * 60)
    
    existing_index = load_index()
    
    # 仅生成摘要模式
    if summary_only:
        generate_daily_summary(existing_index)
        return []
    
    state = load_state()
    since_date = None
    if not full_scan and state.get("last_scan"):
        since_date = parse_date(state["last_scan"])
        if since_date:
            since_date -= timedelta(days=3)
            print(f"增量起始: {since_date.strftime('%Y-%m-%d')}")
    
    # 去重键用归一化 URL（http/https、大小写、末位斜杠差异视为同一篇）
    existing_urls = {normalize_url(item.get("url", "")) for item in existing_index}
    
    # 加载上次失败站点
    failed_sites = load_failed_sites()
    
    all_new_items = []
    channel_results = {}
    channel_engine = {}  # 记录每个栏目使用的引擎
    channel_errors = {}  # 记录抛异常的栏目（异常摘要，便于线上排查）
    
    start_time = time.time()
    
    # 优化：并发扫描独立频道（使用线程池）
    # 注意：Playwright 浏览器模式不支持多线程，需要单独处理
    browser_channels = []
    requests_channels = []
    
    for channel in CHANNELS:
        if len(channel) == 5:
            channel_name, list_url, domain, channel_type, cms_type = channel
            use_browser = False
        else:
            channel_name, list_url, domain, channel_type, cms_type, use_browser = channel
        
        # 上次失败时启用浏览器兜底 —— 但必须先确认 Playwright 真的可用。
        # 原逻辑无条件下发浏览器模式，而本机并未安装 Playwright，于是形成
        # 「失败 → 走浏览器 → 浏览器不可用 → 又失败」的自锁死循环（fail_count
        # 一度累积到 16）。这里加可用性闸门，并限制最多兜底 2 次。
        if channel_name in failed_sites:
            prev_fail = failed_sites[channel_name].get("fail_count", 0)
            if prev_fail >= 2:
                print(f"     ⏭ {channel_name}: 已连续失败 {prev_fail} 次，"
                      f"跳过本次（不再重试，避免拖慢整体）")
                channel_results[channel_name] = 0
                continue
            if get_playwright() or os.path.exists(_NODE_EXE):
                use_browser = True
                print(f"     🔄 上次失败，启用浏览器兜底")
            else:
                print(f"     ⚠️ 上次失败，但浏览器引擎(Playwright/CDP)均不可用，仍用 requests 模式")
        
        if force_browser:
            use_browser = True
        
        # 分类：需要浏览器的放一个列表，普通的放另一个
        if cms_type == "jwc_dynamic" or use_browser:
            browser_channels.append((channel_name, list_url, domain, channel_type, cms_type, True))
        else:
            requests_channels.append((channel_name, list_url, domain, channel_type, cms_type, False))

    # 2026-09-22：浏览器栏目（CDP 渲染）默认开启 —— 实测整体 21.7s → 34.8s，
    # 换来 `招标采购-通知公告` 这个原本永远抓不到的 SPA 栏目（+18 条）。
    # 若某次需要最快跑完，可用 HFUT_ENABLE_BROWSER_CHANNELS=0 关掉。
    browser_skipped = []
    _browser_env = os.environ.get("HFUT_ENABLE_BROWSER_CHANNELS", "1").strip().lower()
    _allow_browser = force_browser or _browser_env not in ("0", "false", "no", "off")
    if browser_channels and not _allow_browser:
        browser_skipped = [c[0] for c in browser_channels]
        for c in browser_channels:
            channel_results[c[0]] = 0
        print(f"  ⏭ 跳过浏览器栏目 {len(browser_skipped)} 个"
              f"（HFUT_ENABLE_BROWSER_CHANNELS=0 已显式关闭）: {', '.join(browser_skipped)}")
        browser_channels = []
    
    # 先并发扫描普通频道（requests模式）
    if requests_channels:
        print(f"\n  🚀 并发扫描 {len(requests_channels)} 个普通频道...")
        
        def scan_wrapper(args):
            """包装函数，用于线程池"""
            name, url, domain, ctype, cms, browser_flag = args
            try:
                items = scan_channel(name, url, domain, ctype, cms,
                                   max_pages, since_date, use_browser_fallback=browser_flag)
                return name, url, items, browser_flag, None
            except Exception as e:
                # 关键：不再静默吞掉异常。打印类型+消息+前几行 traceback，
                # 并把异常摘要随返回值带回，由 main 写入 state["channel_errors"] 便于排查。
                tb = traceback.format_exc().strip().splitlines()[-6:]
                print(f"     ⚠ 栏目 [{name}] 异常: {type(e).__name__}: {e}")
                for line in tb:
                    print(f"        {line}")
                return name, url, [], browser_flag, f"{type(e).__name__}: {e}"
        
        # 优化：增加max_workers到6（原来是3）
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(scan_wrapper, ch): ch for ch in requests_channels}
            
            for future in as_completed(futures):
                channel_name, channel_url, items, used_browser, channel_err = future.result()
                if channel_err:
                    channel_errors[channel_name] = channel_err
                
                if items:
                    channel_results[channel_name] = len(items)
                    channel_engine[channel_name] = "browser" if used_browser else "requests"
                    # 清除失败记录
                    failed_sites.pop(channel_name, None)
                else:
                    channel_results[channel_name] = 0
                    # 记录失败（原代码此处误用了外层循环变量 list_url，
                    # 导致所有失败栏目都被记成最后一个栏目的 URL）
                    failed_sites[channel_name] = {
                        "url": channel_url,
                        "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "fail_count": failed_sites.get(channel_name, {}).get("fail_count", 0) + 1,
                    }
                
                new_items = [item for item in items
                             if item_key(item) not in existing_urls]
                if new_items:
                    print(f"     ✅ {channel_name}: +{len(items)} 条（新增 {len(new_items)} 条）")
                    all_new_items.extend(new_items)
                    # 同步登记，避免同一次扫描内跨栏目重复计入
                    existing_urls.update(item_key(i) for i in new_items)
                else:
                    print(f"     ⚠️ {channel_name}: 无新增")
    
    # 串行扫描需要浏览器的频道（浏览器引擎不支持多线程，且避免并发抢调试端口）
    # try/finally：确保中途异常（KeyboardInterrupt 等）也能关闭无头 Edge，不残留进程。
    if browser_channels:
        try:
            ensure_cdp_browser_up()
            print(f"\n  🌐 扫描 {len(browser_channels)} 个浏览器模式频道...")
            for ch in browser_channels:
                channel_name, list_url, domain, channel_type, cms_type, use_browser = ch
                _bch_t0 = time.time()

                try:
                    items = scan_channel(channel_name, list_url, domain, channel_type, cms_type,
                                       max_pages, since_date, use_browser_fallback=use_browser)
                except Exception as e:
                    tb = traceback.format_exc().strip().splitlines()[-6:]
                    print(f"     ⚠ 栏目 [{channel_name}] 异常: {type(e).__name__}: {e}")
                    for line in tb:
                        print(f"        {line}")
                    channel_errors[channel_name] = f"{type(e).__name__}: {e}"
                    items = []

                if items:
                    channel_results[channel_name] = len(items)
                    channel_engine[channel_name] = "browser"
                    failed_sites.pop(channel_name, None)
                else:
                    channel_results[channel_name] = 0
                    failed_sites[channel_name] = {
                        "url": list_url,
                        "last_fail": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "fail_count": failed_sites.get(channel_name, {}).get("fail_count", 0) + 1,
                    }

                new_items = [item for item in items
                             if item_key(item) not in existing_urls]
                if new_items:
                    print(f"     ✅ {channel_name}: +{len(items)} 条（新增 {len(new_items)} 条）")
                    all_new_items.extend(new_items)
                    existing_urls.update(item_key(i) for i in new_items)
                print(f"     ⏱ {channel_name} 浏览器渲染耗时 {time.time() - _bch_t0:.1f}s")
        finally:
            close_cdp_browser()
    else:
        close_cdp_browser()

    elapsed = time.time() - start_time
    
    existing_index.extend(all_new_items)
    existing_index.sort(key=lambda x: parse_date(x.get("date", "")) or datetime(2000, 1, 1), reverse=True)
    save_index(existing_index)
    
    latest = existing_index[:100]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    
    state["last_scan"] = datetime.now().strftime("%Y-%m-%d")
    state["total_items"] = len(existing_index)
    state["channel_results"] = channel_results
    # 2026-09-23 新增：把抛异常的栏目（含异常摘要）写回 state，便于线上诊断。
    # 此前异常被 scan_wrapper 静默吞掉，出问题完全无法定位。
    state["channel_errors"] = channel_errors
    # 2026-09-22 新增：把失败栏目显式写回 state。此前只存 channel_results，
    # 而 build_summary 读 state["failed_channels"]，导致报告恒显示
    # 「覆盖栏目 33/33」这种 33 个成功 / 33 个总数的假象。
    # 被主动跳过的浏览器栏目（browser_skipped）不算失败。
    state["browser_skipped"] = browser_skipped
    _skip_set = set(browser_skipped)
    state["failed_channels"] = [k for k, v in channel_results.items()
                                if v == 0 and k not in _skip_set]
    state["last_scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    
    # 保存失败记录
    save_failed_sites(failed_sites)
    
    # 生成每日摘要
    generate_daily_summary(existing_index, all_new_items)
    
    # 保存扫描日志
    save_scan_log({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "full" if full_scan else "incremental",
        "elapsed": round(elapsed, 1),
        "total_index": len(existing_index),
        "new_items": len(all_new_items),
        "channels_success": sum(1 for v in channel_results.values() if v > 0),
        "channels_total": len(channel_results),
        "failed_sites": list(failed_sites.keys()),
    })
    
    print("\n" + "=" * 60)
    print("扫描完成!")
    print(f"  ⏱ 耗时: {elapsed:.1f}s")
    print(f"  📊 总索引: {len(existing_index)} 条")
    print(f"  🆕 本次新增: {len(all_new_items)} 条")
    print(f"  🔥 高相关度: {sum(1 for i in existing_index if i.get('score', 0) >= 10)} 条")
    print(f"  📡 覆盖栏目: {len(channel_results)} 个")
    
    success_count = sum(1 for v in channel_results.values() if v > 0)
    fail_count = len(channel_results) - success_count
    print(f"  ✅ 成功提取: {success_count} / {len(channel_results)} 个栏目")
    
    if fail_count > 0:
        print(f"  ⚠ 失败栏目（已记录，下次自动重试）:")
        for name, count in channel_results.items():
            if count == 0:
                print(f"    - {name}")
    if channel_errors:
        print(f"  🐞 异常栏目（异常被捕获未中断整体）:")
        for name, err in channel_errors.items():
            print(f"    - {name}: {err}")
    # 输出各栏目实际使用的引擎（诊断价值：确认哪些走了浏览器兜底）
    _browser_eng = [n for n, e in channel_engine.items() if e == "browser"]
    if _browser_eng:
        print(f"  🌐 浏览器引擎栏目: {', '.join(_browser_eng)}")
    print("=" * 60)
    
    if all_new_items:
        all_new_items.sort(key=lambda x: x.get("score", 0), reverse=True)
        print("\n🔥 高相关度新增:")
        count = 0
        for item in all_new_items:
            if item.get("score", 0) >= 5:
                cat_label = {"competition": "🏆竞赛", "baoyan": "🎓保研", "academic": "📖学术",
                           "funding": "💰基金", "scholarship": "🎖奖学金", "career": "💼就业"}.get(
                    item.get("category", ""), "")
                print(f"  [{item.get('score', 0):2d}] {item.get('date', '')} | {cat_label} {item['channel']} | {item['title'][:50]}")
                count += 1
        if count == 0:
            print("  (无高相关度新增)")
    
    return all_new_items

if __name__ == "__main__":
    main()
