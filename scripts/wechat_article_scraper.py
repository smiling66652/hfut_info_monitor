"""
微信公众号文章采集模块
======================

功能：获取特定微信公众号的全部文章（历史+最新）

多级退回机制：
- Plan A: we-mp-rss（需要独立部署Docker服务）
- Plan B: weixin_search_mcp（基于搜狗搜索，当前主方案）✅
- Plan C: 基础HTTP请求（搜狗搜索直接调用）

依赖：
- Plan B: weixin_search_mcp（已安装在 ../weixin_search_mcp/）
- 标准库: requests, hashlib, json, time, datetime

数据输出格式（统一）：
{
    'id': '唯一ID (MD5)',
    'source': '微信公众号',
    'category': '文章',
    'title': '文章标题',
    'content': '正文摘要/全文',
    'url': '文章链接',
    'author': '公众号名称',
    'publish_time': '2026-06-04',
    'fetch_time': '2026-06-04 10:30:00',
    'extra': {
        'read_count': 1000,
        'like_count': 50,
        'comment_count': 10,
    },
    'raw': {...}
}

使用示例：
```python
# 初始化
scraper = WechatArticleScraper()

# 添加公众号
scraper.init_account("合肥工业大学")

# 全量扫描
articles = scraper.fetch_articles("合肥工业大学")

# 增量扫描（只获取最新文章）
new_articles = scraper.fetch_new_articles("合肥工业大学", last_fetch_time="2026-06-03")

# 多公众号批量获取
all_articles = scraper.fetch_all_accounts()
```

作者：CodeBuddy Code
日期：2026-06-04
版本：v1.0.0
"""

import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("HFUT_Monitor.WechatScraper")


# ============================================================
# 配置常量
# ============================================================

# 请求间隔（秒），避免触发反爬
REQUEST_INTERVAL = 3

# 搜索结果最大页数
MAX_SEARCH_PAGES = 5

# 每页最大结果数
MAX_RESULTS_PER_PAGE = 10

# 内容摘要长度（字符数）
CONTENT_PREVIEW_LENGTH = 500

# we-mp-rss 服务地址（如果已部署）
WEMP_RSS_BASE_URL = "http://localhost:8001"

# we-mp-rss API 认证 Token（如果启用）
WEMP_RSS_TOKEN = None  # 从环境变量或配置文件读取


# ============================================================
# 方案选择器
# ============================================================

class ScraperStrategy:
    """采集方案枚举"""
    PLAN_A_WEMP_RSS = "wemp_rss"      # we-mp-rss（需要独立服务）
    PLAN_B_WEIXIN_MCP = "weixin_mcp"  # weixin_search_mcp（当前默认）
    PLAN_C_HTTP_DIRECT = "http_direct"  # 直接HTTP请求（兜底）


def detect_available_strategy() -> str:
    """
    自动检测可用的采集方案

    Returns:
        str: 方案名称 (ScraperStrategy)
    """
    # 优先检测 we-mp-rss 服务是否可用
    try:
        import requests
        resp = requests.get(f"{WEMP_RSS_BASE_URL}/rss", timeout=3)
        if resp.status_code == 200:
            logger.info("✅ 检测到 we-mp-rss 服务运行中")
            return ScraperStrategy.PLAN_A_WEMP_RSS
    except Exception:
        pass

    # 其次检测 weixin_search_mcp 是否可用
    try:
        weixin_mcp_dir = Path(__file__).parent.parent.parent / "weixin_search_mcp"
        if str(weixin_mcp_dir) not in sys.path:
            sys.path.insert(0, str(weixin_mcp_dir))

        from weixin_search_mcp.tools.weixin_search import (
            sogou_weixin_search,
            get_article_content,
        )
        logger.info("✅ 检测到 weixin_search_mcp 可用")
        return ScraperStrategy.PLAN_B_WEIXIN_MCP
    except ImportError:
        pass

    # 兜底：直接 HTTP 请求
    logger.info("⚠️ 使用基础HTTP请求方案（功能受限）")
    return ScraperStrategy.PLAN_C_HTTP_DIRECT


# ============================================================
# 统一数据格式工具
# ============================================================

def generate_article_id(url: str, title: str) -> str:
    """
    生成文章唯一ID（MD5哈希）

    Args:
        url: 文章URL
        title: 文章标题

    Returns:
        str: MD5格式的唯一ID
    """
    raw_string = f"{url}|{title}"
    return hashlib.md5(raw_string.encode('utf-8')).hexdigest()


def normalize_article_data(raw_data: Dict[str, Any], source: str = "微信公众号") -> Dict[str, Any]:
    """
    将不同来源的原始数据标准化为统一格式

    Args:
        raw_data: 原始数据字典
        source: 数据来源标识

    Returns:
        dict: 标准化的文章数据
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 提取基础字段
    url = raw_data.get('url', '') or raw_data.get('real_url', '')
    title = raw_data.get('title', '').strip()
    author = raw_data.get('author', '') or raw_data.get('account_name', '') or raw_data.get('mp_name', '')

    # 解析发布时间
    publish_time = raw_data.get('publish_time', '') or raw_data.get('time', '')
    if isinstance(publish_time, (int, float)):
        # Unix 时间戳
        try:
            publish_time = datetime.fromtimestamp(publish_time).strftime("%Y-%m-%d")
        except Exception:
            publish_time = datetime.now().strftime("%Y-%m-%d")
    else:
        publish_time = parse_publish_time(str(publish_time))

    # 提取内容
    content = raw_data.get('content', '')
    if content and len(content) > CONTENT_PREVIEW_LENGTH:
        content = content[:CONTENT_PREVIEW_LENGTH] + "..."

    # 提取额外信息
    extra = {
        'read_count': raw_data.get('read_count', 0),
        'like_count': raw_data.get('like_count', 0),
        'comment_count': raw_data.get('comment_count', 0),
    }

    # 构建标准格式
    normalized = {
        'id': generate_article_id(url, title),
        'source': source,
        'category': '文章',
        'title': title,
        'content': content,
        'url': url,
        'author': author,
        'publish_time': publish_time,
        'fetch_time': now,
        'extra': extra,
        'raw': raw_data,  # 保留原始数据便于调试
    }

    return normalized


def parse_publish_time(raw: str) -> str:
    """
    解析多种格式的发布时间

    支持格式：
    - Unix时间戳（纯数字 > 10位）
    - 中文日期：2024年06月03日
    - 相对时间：2天前、3小时前
    - JS时间戳格式：document.write(timeConvert('1665110771'))

    Args:
        raw: 原始时间字符串

    Returns:
        str: 格式化后的日期字符串 (YYYY-MM-DD)
    """
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")

    # 提取纯数字（Unix 时间戳）
    numbers = re.findall(r'\d+', raw)
    if numbers:
        ts = int(numbers[0])
        # 判断是时间戳还是日期数字
        if ts > 1000000000:  # Unix时间戳（秒级）
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d")

    # 包含中文日期
    cn_date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', raw)
    if cn_date_match:
        y, m, d = cn_date_match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"

    # ISO格式日期：2024-06-03
    iso_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if iso_match:
        return raw[:10]

    # 相对时间处理
    if "天前" in raw:
        days = int(re.search(r'(\d+)天前', raw).group(1)) if re.search(r'(\d+)天前', raw) else 0
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if "小时前" in raw:
        hours = int(re.search(r'(\d+)小时前', raw).group(1)) if re.search(r'(\d+)小时前', raw) else 0
        return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")
    if "分钟前" in raw:
        return datetime.now().strftime("%Y-%m-%d")

    # 默认返回今天
    return datetime.now().strftime("%Y-%m-%d")


# ============================================================
# Plan A: we-mp-rss 采集器（需要独立部署）
# ============================================================

class WempRssScraper:
    """
    基于 we-mp-rss 服务的文章采集器

    要求：
    - 已部署 we-mp-rss 服务（Docker或本地运行）
    - 默认端口：8001
    - 已通过管理界面添加公众号订阅

    文档：https://github.com/rachelos/we-mp-rss
    """

    def __init__(self, base_url: str = WEMP_RSS_BASE_URL, token: Optional[str] = None):
        """
        初始化 we-mp-rss 采集器

        Args:
            base_url: we-mp-rss 服务地址
            token: API 认证 Token（如启用）
        """
        self.base_url = base_url.rstrip('/')
        self.token = token or WEMP_RSS_TOKEN
        self.session = None

        try:
            import requests
            self.session = requests.Session()
            if self.token:
                self.session.headers.update({'Authorization': f'Bearer {self.token}'})
            logger.info(f"✅ we-mp-rss 采集器初始化成功: {base_url}")
        except ImportError:
            logger.error("❌ 未安装 requests 库，无法使用 we-mp-rss 方案")

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """
        发送 HTTP 请求到 we-mp-rss 服务

        Args:
            method: HTTP方法 (GET/POST)
            endpoint: API端点
            **kwargs: 额外参数

        Returns:
            dict: JSON响应或None
        """
        if not self.session:
            return None

        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.request(method, url, timeout=15, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"   we-mp-rss 请求失败 [{method} {endpoint}]: {e}")
            return None

    def get_feeds(self) -> List[Dict]:
        """
        获取所有已订阅的公众号列表

        Returns:
            list: 公众号列表
        """
        data = self._request('GET', '/api/rss')
        if data and data.get('code') == 200:
            feeds = data.get('data', [])
            logger.info(f"   📋 获取到 {len(feeds)} 个订阅公众号")
            return feeds
        return []

    def fetch_articles(self, feed_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        """
        获取指定公众号的文章列表

        Args:
            feed_id: 公众号ID（we-mp-rss分配的ID）
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            list: 文章原始数据列表
        """
        params = {'limit': limit, 'offset': offset}
        data = self._request('GET', f'/rss/{feed_id}.json', params=params)

        if data and isinstance(data, list):
            articles = []
            for item in data:
                # 转换为统一格式所需的字段
                article_raw = {
                    'title': item.get('title', ''),
                    'url': item.get('link', '') or item.get('url', ''),
                    'content': item.get('description', '') or item.get('content', ''),
                    'author': item.get('author', ''),
                    'publish_time': item.get('pubDate', '') or item.get('published', ''),
                    'mp_name': item.get('mp_name', ''),  # 公众号名称
                }
                articles.append(article_raw)

            logger.info(f"   📄 公众号 [{feed_id}] 获取 {len(articles)} 篇文章")
            return articles

        return []

    def get_article_detail(self, article_id: str) -> Optional[Dict]:
        """
        获取单篇文章详情（包含完整内容）

        Args:
            article_id: 文章ID

        Returns:
            dict: 文章详情或None
        """
        data = self._request('GET', f'/articles/{article_id}')
        if data and data.get('code') == 200:
            return data.get('data')
        return None

    def refresh_article(self, article_id: str) -> bool:
        """
        刷新单篇文章内容

        Args:
            article_id: 文章ID

        Returns:
            bool: 是否成功发起刷新
        """
        data = self._request('POST', f'/articles/{article_id}/refresh')
        if data and data.get('code') == 200:
            logger.info(f"   🔄 文章刷新任务已提交: {article_id}")
            return True
        return False


# ============================================================
# Plan B: weixin_search_mcp 采集器（当前主方案）
# ============================================================

class WeixinSearchMcpScraper:
    """
    基于 weixin_search_mcp 的文章采集器（推荐）

    特点：
    ✅ 无需独立部署，直接Python调用
    ✅ 基于搜狗微信搜索引擎
    ⚠️ 只能获取搜狗收录的文章（非全量）
    ⚠️ 受搜狗反爬机制影响

    依赖：weixin_search_mcp（https://github.com/fancyboi999/weixin_search_mcp）
    """

    def __init__(self):
        """初始化 weixin_search_mcp 采集器"""
        self.search_fn = None
        self.content_fn = None
        self._load_dependencies()

    def _load_dependencies(self):
        """延迟加载 weixin_search_mcp 依赖"""
        try:
            weixin_mcp_dir = Path(__file__).parent.parent.parent / "weixin_search_mcp"
            if str(weixin_mcp_dir) not in sys.path:
                sys.path.insert(0, str(weixin_mcp_dir))

            from weixin_search_mcp.tools.weixin_search import (
                sogou_weixin_search,
                sogou_weixin_search_all,
                get_article_content,
            )

            self.search_fn = sogou_weixin_search
            self.search_all_fn = sogou_weixin_search_all
            self.content_fn = get_article_content
            logger.info("✅ weixin_search_mcp 采集器初始化成功")

        except ImportError as e:
            logger.warning(f"⚠️ weixin_search_mcp 加载失败: {e}")
            logger.warning("   安装方式: cd D:/项目整理/01-代码项目 && git clone https://github.com/fancyboi999/weixin_search_mcp.git")
            logger.warning("          cd weixin_search_mcp && pip install -e .")

    def search_articles(
        self,
        query: str,
        page: int = 1,
        max_results: int = MAX_RESULTS_PER_PAGE
    ) -> List[Dict]:
        """
        通过关键词搜索公众号文章

        Args:
            query: 搜索关键词（可以是公众号名、话题等）
            page: 页码
            max_results: 最大返回数量

        Returns:
            list: 文章原始数据列表（未标准化）
        """
        if not self.search_fn:
            logger.error("❌ 搜索函数未加载，无法执行搜索")
            return []

        try:
            results = self.search_fn(query, page=page)

            # 限制返回数量并过滤无效结果
            valid_results = []
            for item in results[:max_results]:
                url = item.get('real_url', '')
                title = item.get('title', '')
                if url and url != "https://mp." and title:
                    valid_results.append(item)

            logger.info(f"   🔍 关键词 [{query}] 第{page}页: {len(valid_results)} 条结果")
            return valid_results

        except Exception as e:
            logger.error(f"   ❌ 搜索失败 [{query}]: {e}")
            return []

    def search_all_pages(
        self,
        query: str,
        max_pages: int = MAX_SEARCH_PAGES
    ) -> List[Dict]:
        """
        自动翻页搜索所有相关文章

        Args:
            query: 搜索关键词
            max_pages: 最大翻页数

        Returns:
            list: 所有页面的搜索结果合并列表
        """
        if not self.search_all_fn:
            # 如果没有多页搜索函数，手动分页
            all_results = []
            for page in range(1, max_pages + 1):
                results = self.search_articles(query, page=page)
                if not results:
                    break
                all_results.extend(results)
                time.sleep(REQUEST_INTERVAL)  # 友好爬虫
            return all_results

        try:
            results = self.search_all_fn(query, max_pages=max_pages)
            logger.info(f"   🔍 关键词 [{query}] 全量搜索: {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"   ❌ 全量搜索失败 [{query}]: {e}")
            return []

    def fetch_article_content(self, url: str, referer: str = "") -> str:
        """
        获取单篇文章的完整内容

        Args:
            url: 微信文章真实URL
            referer: 来源页面URL（用于绕过Referer检查）

        Returns:
            str: 文章正文内容
        """
        if not self.content_fn:
            return ""

        try:
            content = self.content_fn(url, referer=referer)
            return content
        except Exception as e:
            logger.debug(f"      内容提取失败: {e}")
            return ""


# ============================================================
# Plan C: 直接HTTP请求采集器（兜底方案）
# ============================================================

class HttpDirectScraper:
    """
    基于直接HTTP请求的文章采集器（兜底方案）

    特点：
    ✅ 无任何外部依赖
    ⚠️ 功能最基础，容易被反爬拦截
    ⚠️ 只支持关键词搜索

    适用场景：
    - 无法安装 weixin_search_mcp
    - we-mp-rss 服务不可用
    - 临时紧急需求
    """

    def __init__(self):
        """初始化 HTTP 采集器"""
        try:
            import requests
            from lxml import html
            self.requests = requests
            self.lxml_html = html
            self.available = True
            logger.info("✅ HTTP直接请求采集器初始化成功")
        except ImportError as e:
            logger.warning(f"⚠️ 缺少必要依赖: {e}")
            logger.warning("   安装: pip install requests lxml")
            self.available = False

    def _get_headers(self) -> Dict[str, str]:
        """构造浏览器请求头"""
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cache-Control': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
        }

    def search_articles(self, query: str, page: int = 1) -> List[Dict]:
        """
        通过搜狗微信搜索接口查询文章

        Args:
            query: 搜索关键词
            page: 页码

        Returns:
            list: 搜索结果列表
        """
        if not self.available:
            return []

        params = {
            'type': '2',
            'query': query,
            'ie': 'utf8',
            'page': page,
        }

        try:
            resp = self.requests.get(
                'https://weixin.sogou.com/weixin',
                params=params,
                headers=self._get_headers(),
                timeout=15,
            )

            if resp.status_code != 200:
                logger.error(f"   ❌ 搜狗搜索返回状态码: {resp.status_code}")
                return []

            # 检查反爬验证
            if "antispider" in resp.url.lower() or "seccoderight" in resp.text.lower():
                logger.error("   ❌ 触发搜狗反爬验证，请稍后再试")
                return []

            tree = self.lxml_html.fromstring(resp.text)
            results = []

            elements = tree.xpath("//a[contains(@id, 'sogou_vr_11002601_title_')]")
            time_elements = tree.xpath(
                "//li[contains(@id, 'sogou_vr_11002601_box_')]/div[@class='txt-box']/div[@class='s-p']/span[@class='s2']"
            )

            for elem, time_elem in zip(elements, time_elements):
                title = elem.text_content().strip()
                link = elem.get('href', '')
                if not link.startswith('http'):
                    link = 'https://weixin.sogou.com' + link

                publish_time = time_elem.text_content().strip() if time_elem is not None else ''

                results.append({
                    'title': title,
                    'url': '',          # 真实URL需要二次解析
                    'real_url': '',     # 待填充
                    'link': link,
                    'publish_time': publish_time,
                    'account_name': query,
                })

            logger.info(f"   🔍 [Plan C] 搜狗搜索 [{query}] 第{page}页: {len(results)} 条")
            return results

        except Exception as e:
            logger.error(f"   ❌ HTTP请求失败: {e}")
            return []


# ============================================================
# 主采集器类（统一入口）
# ============================================================

class WechatArticleScraper:
    """
    微信公众号文章采集器（统一入口）

    功能特性：
    ✅ 全量扫描：同步历史文章（首次运行或重建索引）
    ✅ 增量扫描：只获取新发布文章
    ✅ 多公众号支持：可同时监控多个公众号
    ✅ 统一数据格式：标准化输出，易于后续处理
    ✅ 多级退回：自动检测可用方案，降级保证可用性

    使用示例：
    ```python
    scraper = WechatArticleScraper()

    # 初始化要监控的公众号
    scraper.init_account("合肥工业大学")
    scraper.init_account("合工大宣城校区")

    # 全量获取某公众号文章
    articles = scraper.fetch_articles("合肥工业大学")

    # 增量获取最新文章
    new_articles = scraper.fetch_new_articles("合肥工业大学", last_fetch_time="2026-06-03")

    # 批量获取所有公众号文章
    all_articles = scraper.fetch_all_accounts()
    ```
    """

    def __init__(self, strategy: Optional[str] = None):
        """
        初始化采集器

        Args:
            strategy: 强制指定方案 ('wemp_rss', 'weixin_mcp', 'http_direct')
                     若为None则自动检测最佳方案
        """
        # 选择采集方案
        self.strategy = strategy or detect_available_strategy()
        logger.info(f"📱 微信文章采集器启动，方案: {self.strategy}")

        # 初始化对应的采集器实例
        self._scraper = None
        self._init_scraper()

        # 已注册的公众号列表
        self.accounts: Dict[str, Dict] = {}

        # URL去重集合（防止重复抓取）
        self.seen_urls: set = set()

    def _init_scraper(self):
        """根据选择的方案初始化采集器实例"""
        if self.strategy == ScraperStrategy.PLAN_A_WEMP_RSS:
            self._scraper = WempRssScraper()
        elif self.strategy == ScraperStrategy.PLAN_B_WEIXIN_MCP:
            self._scraper = WeixinSearchMcpScraper()
        else:
            self._scraper = HttpDirectScraper()

    def init_account(
        self,
        account_name: str,
        biz_id: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        **metadata
    ) -> bool:
        """
        初始化要监控的公众号

        Args:
            account_name: 公众号名称（必填）
            biz_id: 公众号biz ID（用于精确匹配，可选）
            keywords: 搜索关键词列表（用于搜索模式，默认使用公众号名）
            **metadata: 其他元数据（如分类、标签等）

        Returns:
            bool: 是否初始化成功

        注意事项：
        - biz_id 是微信公众号的唯一标识符，可通过以下方式获取：
          * 在公众号文章URL中查找 ?biz=MzIxxxxx 参数
          * 使用微信开发者工具查看
        - 对于 Plan A (we-mp-rss)，需要先在管理界面扫码添加该公众号
        """
        if not account_name.strip():
            logger.error("❌ 公众号名称不能为空")
            return False

        # 设置默认搜索关键词
        if not keywords:
            keywords = [
                account_name,
                f"{account_name} 通知",
                f"{account_name} 新闻",
            ]

        account_info = {
            'name': account_name,
            'biz_id': biz_id,           # 公众号唯一标识
            'keywords': keywords,       # 搜索关键词
            'feed_id': None,            # we-mp-rss 的订阅ID（Plan A使用）
            'last_fetch_time': None,    # 上次采集时间
            'total_articles': 0,        # 已采集文章总数
            'metadata': metadata,       # 自定义元数据
        }

        # 如果是 we-mp-rss 方案，尝试获取 feed_id
        if self.strategy == ScraperStrategy.PLAN_A_WEMP_RSS and hasattr(self._scraper, 'get_feeds'):
            feeds = self._scraper.get_feeds()
            for feed in feeds:
                feed_name = feed.get('name', '') or feed.get('mp_name', '')
                if account_name in feed_name:
                    account_info['feed_id'] = feed.get('id')
                    logger.info(f"   📌 匹配到 we-mp-rss 订阅: {feed_name} (ID: {account_info['feed_id']})")
                    break

        self.accounts[account_name] = account_info
        logger.info(f"✅ 公众号初始化完成: {account_name} (方案: {self.strategy})")

        return True

    def fetch_articles(
        self,
        account: str,
        full_scan: bool = True,
        max_articles: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取指定公众号的全部文章列表（全量扫描）

        Args:
            account: 公众号名称（必须是已初始化的账号）
            full_scan: 是否全量扫描（True=历史+最新，False=仅首页）
            max_articles: 最大返回文章数量

        Returns:
            list: 标准化后的文章列表（统一格式）
        """
        if account not in self.accounts:
            logger.error(f"❌ 公众号未初始化: {account}")
            return []

        account_info = self.accounts[account]
        logger.info(f"📖 开始采集 [{account}] 的文章{'（全量）' if full_scan else ''}")

        articles = []

        # 根据方案执行不同的采集逻辑
        if self.strategy == ScraperStrategy.PLAN_A_WEMP_RSS:
            articles = self._fetch_via_wemp_rss(account_info, max_articles)
        elif self.strategy == ScraperStrategy.PLAN_B_WEIXIN_MCP:
            articles = self._fetch_via_weixin_mcp(account_info, full_scan, max_articles)
        else:
            articles = self._fetch_via_http_direct(account_info, max_articles)

        # 更新统计信息
        account_info['last_fetch_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        account_info['total_articles'] += len(articles)

        # 去重
        unique_articles = self._deduplicate(articles)

        logger.info(f"   ✅ [{account}] 采集完成: {len(unique_articles)} 篇（去重后）")
        return unique_articles

    def fetch_new_articles(
        self,
        account: str,
        last_fetch_time: str,
        max_articles: int = 20
    ) -> List[Dict[str, Any]]:
        """
        获取指定公众号的最新文章（增量扫描）

        Args:
            account: 公众号名称
            last_fetch_time: 上次采集时间（格式: "YYYY-MM-DD HH:MM:SS"）
            max_articles: 最大返回数量

        Returns:
            list: 新发布的文章列表

        工作原理：
        1. 获取最新的文章列表
        2. 过滤出发布时间晚于 last_fetch_time 的文章
        3. 返回新增文章
        """
        if account not in self.accounts:
            logger.error(f"❌ 公众号未初始化: {account}")
            return []

        logger.info(f"🆕 增量采集 [{account}]，上次采集: {last_fetch_time}")

        # 先全量获取（受限于API能力）
        all_articles = self.fetch_articles(account, full_scan=False, max_articles=max_articles * 2)

        # 过滤新文章
        new_articles = []
        try:
            last_time = datetime.strptime(last_fetch_time, "%Y-%m-%d %H:%M:%S")
            for article in all_articles:
                pub_time_str = article.get('publish_time', '')
                if pub_time_str:
                    try:
                        pub_time = datetime.strptime(pub_time_str, "%Y-%m-%d")
                        if pub_time >= last_time.date():
                            new_articles.append(article)
                    except ValueError:
                        # 时间格式不匹配，保留
                        new_articles.append(article)
                else:
                    # 无时间信息，保守起见保留
                    new_articles.append(article)
        except Exception as e:
            logger.warning(f"   ⚠️ 时间解析异常，返回全部结果: {e}")
            new_articles = all_articles

        logger.info(f"   ✅ [{account}] 发现 {len(new_articles)} 篇新文章")
        return new_articles

    def fetch_all_accounts(self, max_per_account: int = 50) -> Dict[str, List[Dict]]:
        """
        批量获取所有已注册公众号的文章

        Args:
            max_per_account: 每个公众号最大文章数

        Returns:
            dict: {公众号名: [文章列表]}
        """
        if not self.accounts:
            logger.warning("⚠️ 尚未初始化任何公众号，请先调用 init_account()")
            return {}

        logger.info(f"📦 开始批量采集 {len(self.accounts)} 个公众号...")

        all_results = {}
        for i, account_name in enumerate(self.accounts.keys()):
            logger.info(f"\n[{i+1}/{len(self.accounts)}] 处理公众号: {account_name}")
            articles = self.fetch_articles(account_name, max_articles=max_per_account)
            all_results[account_name] = articles

            # 控制请求频率
            time.sleep(REQUEST_INTERVAL)

        total = sum(len(v) for v in all_results.values())
        logger.info(f"\n🎉 批量采集完成: {total} 篇文章（{len(all_results)} 个公众号）")

        return all_results

    def parse_article(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析原始数据为统一标准格式

        Args:
            raw_data: 原始文章数据（任意来源）

        Returns:
            dict: 标准化后的文章数据
        """
        return normalize_article_data(raw_data)

    # ==================== 内部方法 ====================

    def _fetch_via_wemp_rss(
        self,
        account_info: Dict,
        max_articles: int
    ) -> List[Dict]:
        """通过 we-mp-rss 服务采集文章（Plan A）"""
        scraper = self._scraper
        if not hasattr(scraper, 'fetch_articles'):
            logger.error("   ❌ we-mp-rss 采集器未正确初始化")
            return []

        feed_id = account_info.get('feed_id')
        if not feed_id:
            logger.error(f"   ❌ 公众号 [{account_info['name']}] 未在 we-mp-rss 中找到订阅")
            logger.info("   💡 请先在 we-mp-rss 管理界面添加该公众号")
            return []

        # 分批获取
        all_raw_articles = []
        offset = 0
        batch_size = 50

        while len(all_raw_articles) < max_articles:
            batch = scraper.fetch_articles(feed_id, limit=batch_size, offset=offset)
            if not batch:
                break
            all_raw_articles.extend(batch)
            offset += batch_size
            time.sleep(1)  # 避免请求过快

        # 标准化
        articles = [normalize_article_data(raw) for raw in all_raw_articles[:max_articles]]
        return articles

    def _fetch_via_weixin_mcp(
        self,
        account_info: Dict,
        full_scan: bool,
        max_articles: int
    ) -> List[Dict]:
        """通过 weixin_search_mcp 采集文章（Plan B）"""
        scraper = self._scraper
        if not hasattr(scraper, 'search_articles'):
            logger.error("   ❌ weixin_search_mcp 采集器未正确初始化")
            return []

        keywords = account_info.get('keywords', [account_info['name']])
        all_raw_articles = []

        # 对每个关键词进行搜索
        for keyword in keywords:
            if len(all_raw_articles) >= max_articles:
                break

            if full_scan:
                # 全量模式：翻页搜索
                results = scraper.search_all_pages(keyword, max_pages=MAX_SEARCH_PAGES)
            else:
                # 快速模式：仅第一页
                results = scraper.search_articles(keyword, page=1)

            # 补充完整内容
            for item in results:
                if len(all_raw_articles) >= max_articles:
                    break

                url = item.get('real_url', '')
                if url and url not in self.seen_urls:
                    # 提取正文内容
                    content = ""
                    if hasattr(scraper, 'fetch_article_content') and url:
                        try:
                            content = scraper.fetch_article_content(url, referer=item.get('link', ''))
                            time.sleep(1)  # 控制频率
                        except Exception as e:
                            logger.debug(f"      内容提取失败: {e}")

                    # 构建原始数据
                    raw_data = {
                        **item,
                        'content': content,
                        'account_name': account_info['name'],
                    }
                    all_raw_articles.append(raw_data)
                    self.seen_urls.add(url)

            # 控制请求频率
            time.sleep(REQUEST_INTERVAL)

        # 标准化
        articles = [normalize_article_data(raw) for raw in all_raw_articles[:max_articles]]
        return articles

    def _fetch_via_http_direct(
        self,
        account_info: Dict,
        max_articles: int
    ) -> List[Dict]:
        """通过直接HTTP请求采集文章（Plan C）"""
        scraper = self._scraper
        if not getattr(scraper, 'available', False):
            logger.error("   ❌ HTTP采集器未正确初始化")
            return []

        keywords = account_info.get('keywords', [account_info['name']])
        all_raw_articles = []

        for keyword in keywords[:2]:  # Plan C 只搜索前2个关键词以降低风险
            if len(all_raw_articles) >= max_articles:
                break

            results = scraper.search_articles(keyword, page=1)
            for item in results:
                if len(all_raw_articles) >= max_articles:
                    break

                item['account_name'] = account_info['name']
                all_raw_articles.append(item)

            time.sleep(REQUEST_INTERVAL)

        articles = [normalize_article_data(raw) for raw in all_raw_articles[:max_articles]]
        return articles

    def _deduplicate(self, articles: List[Dict]) -> List[Dict]:
        """
        文章去重（基于URL）

        Args:
            articles: 文章列表

        Returns:
            list: 去重后的文章列表
        """
        seen = set()
        unique = []
        for article in articles:
            url = article.get('url', '')
            if url and url not in seen:
                seen.add(url)
                unique.append(article)
        return unique

    def get_status(self) -> Dict[str, Any]:
        """
        获取采集器当前状态信息

        Returns:
            dict: 状态报告
        """
        return {
            'strategy': self.strategy,
            'registered_accounts': list(self.accounts.keys()),
            'total_fetched': sum(acc.get('total_articles', 0) for acc in self.accounts.values()),
            'seen_urls_count': len(self.seen_urls),
            'scraper_type': type(self._scraper).__name__,
        }


# ============================================================
# 便捷函数（简化调用）
# ============================================================

def quick_scrape(
    account_names: List[str],
    strategy: Optional[str] = None,
    max_per_account: int = 30
) -> Dict[str, List[Dict]]:
    """
    快速采集函数（一行调用）

    Args:
        account_names: 要采集的公众号名称列表
        strategy: 强制指定方案（可选）
        max_per_account: 每个公众号最大文章数

    Returns:
        dict: {公众号名: [文章列表]}

    示例：
    ```python
    results = quick_scrape(["合肥工业大学", "合工大宣城校区"])
    for account, articles in results.items():
        print(f"{account}: {len(articles)}篇文章")
    ```
    """
    scraper = WechatArticleScraper(strategy=strategy)

    for name in account_names:
        scraper.init_account(name)

    return scraper.fetch_all_accounts(max_per_account=max_per_account)


# ============================================================
# 主程序（测试/演示）
# ============================================================

if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    print("=" * 70)
    print("  微信公众号文章采集模块测试")
    print("  WeChat Article Scraper v1.0.0")
    print("=" * 70)

    # 显示检测到的方案
    detected_strategy = detect_available_strategy()
    print(f"\n📊 当前可用方案: {detected_strategy}")
    print("-" * 70)

    # 创建采集器实例
    scraper = WechatArticleScraper()

    # 初始化测试公众号
    test_accounts = ["合肥工业大学"]
    print(f"\n🔧 初始化测试公众号: {test_accounts}")
    for acc in test_accounts:
        scraper.init_account(acc)

    # 测试1：全量采集
    print("\n" + "=" * 70)
    print("  测试1: 全量采集")
    print("=" * 70)
    articles = scraper.fetch_articles("合肥工业大学", full_scan=False, max_articles=5)

    print(f"\n📊 采集结果: {len(articles)} 篇文章\n")
    for i, article in enumerate(articles[:5], 1):
        print(f"[{i}] {article['title'][:50]}")
        print(f"    作者: {article['author']} | 发布: {article['publish_time']}")
        print(f"    URL: {article['url'][:60]}...")
        preview = article.get('content', '')[:80].replace('\n', ' ')
        print(f"    摘要: {preview}...")
        print(f"    ID: {article['id'][:12]}...")
        print()

    # 测试2：显示状态
    print("=" * 70)
    print("  采集器状态")
    print("=" * 70)
    status = scraper.get_status()
    for key, value in status.items():
        print(f"  {key}: {value}")

    print("\n" + "=" * 70)
    print("  ✅ 测试完成")
    print("=" * 70)
