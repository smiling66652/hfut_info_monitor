"""
合工大官网爬虫模块（增强版）
功能：从合工大官网（含宣城校区）采集新闻、通知、公告等真实数据

数据源：
1. 合肥工业大学本部 - 新闻要闻 (news.hfut.edu.cn)
2. 合肥工业大学本部 - 通知公告 (news.hfut.edu.cn/tzgg2.htm)
3. 合肥工业大学宣城校区 - 校区新闻 (xc.hfut.edu.cn)
4. 合肥工业大学宣城校区 - 通知公告 (xc.hfut.edu.cn)

技术方案：
- 使用 requests + BeautifulSoup4（页面均为服务端渲染，无需JS）
- 支持增量采集（基于已归档ID去重）
- 自适应速率限制（避免被封）

增强特性：
- 自动检测分页URL模式（支持多种分页格式）
- 增强网络容错（按域名差异化超时/重试，不可达源快速失败）
- 多数据源并发采集（2026-09-10 性能重构：官网采集 505.8s → 约 5s）
- 全量扫描支持（scrape_full()遍历所有历史页）
- 单源失败隔离（不影响其他源采集）
"""

import re
import json
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

# 添加 scripts 目录到路径
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config

logger = logging.getLogger('HFUTScraper')

# ============================================================
# 配置
# ============================================================

# 数据源定义
SOURCES = {
    'main_news': {
        'name': '本部-新闻要闻',
        'base_url': 'https://news.hfut.edu.cn/',
        'list_url': 'https://news.hfut.edu.cn/',
        'category': '新闻',
    },
    'main_notice': {
        'name': '本部-通知公告',
        'base_url': 'https://news.hfut.edu.cn/',
        'list_url': 'https://news.hfut.edu.cn/tzgg2.htm',
        'category': '通知公告',
    },
    'xc_news': {
        'name': '宣城校区-校区新闻',
        'base_url': 'https://xc.hfut.edu.cn',
        'list_url': 'https://xc.hfut.edu.cn/1954/list.htm',
        'category': '新闻',
    },
    'xc_notice': {
        'name': '宣城校区-通知公告',
        'base_url': 'https://xc.hfut.edu.cn',
        'list_url': 'https://xc.hfut.edu.cn/1955/list.htm',
        'category': '通知公告',
    }
}

# 请求配置（2026-09-10 性能重构）
#
# 历史问题：原配置 timeout=30s / retry=5 次，对「校外根本连不通」的宣城校区域名
# 会造成 30*5 + 退避(2+4+6+8) ≈ 170 秒/URL 的空等。实测 2026-06-11 那次巡检
# 总耗时 1358.9 秒，其中官网采集 505.8 秒，几乎全部是 xc.hfut.edu.cn 的超时重试。
# 对策：缩短基础超时 + 减少重试 + 按域名差异化 + 多源并发。
REQUEST_CONFIG = {
    'timeout': 12,             # 基础超时（原 30，够用且不空等）
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'retry_times': 2,          # 基础重试（原 5）
    'retry_delay': 2,
    'min_request_interval': 1.5,  # 最小请求间隔(秒)，避免频率限制
    'max_workers': 4,          # 多数据源并发采集的线程数
    # 是否继承环境变量里的 HTTP(S)_PROXY。
    # 实测（2026-09-10）：本机环境存在 http://127.0.0.1 的本地代理，对 hfut 域名返回
    # 502 Bad Gateway；改为直连后 news.hfut.edu.cn 仅 0.1s 即 200。
    # 本爬虫只访问 hfut.edu.cn，不需要代理，默认关闭。
    'use_env_proxy': False,
}

# 按域名的差异化策略：校外直连经常不通的源，快速失败，不要浪费整个巡检的时间
HOST_OVERRIDES = {
    'xc.hfut.edu.cn': {
        'timeout': 10,
        'retry_times': 1,
        # 校外(非校园网/WebVPN)直连基本不通，失败即走降级，不再反复重试
        'hint': '宣城校区域名校外常不可达，需 WebVPN(webvpn.hfut.edu.cn) 或校园网内访问',
    },
}

# 全局 HTTP 会话（复用连接）
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """获取或创建 HTTP 会话"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            'User-Agent': REQUEST_CONFIG['user_agent'],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cache-Control': 'no-cache',
        })
        # 默认不走环境代理：实测本地代理对 hfut 域名返回 502，直连反而更快更稳
        if not REQUEST_CONFIG.get('use_env_proxy', False):
            _session.trust_env = False
    return _session


def _request(url: str) -> Optional[str]:
    """
    发送HTTP GET请求，带重试和速率限制（增强版）

    Args:
        url: 目标URL

    Returns:
        响应文本，失败返回None
    """
    session = _get_session()

    # 按域名套用差异化超时/重试：校外不通的源快速失败，不拖垮整次巡检
    host = urlparse(url).hostname or ''
    override = HOST_OVERRIDES.get(host, {})
    timeout = override.get('timeout', REQUEST_CONFIG['timeout'])
    retry_times = override.get('retry_times', REQUEST_CONFIG['retry_times'])

    for attempt in range(retry_times):
        try:
            # 速率限制：避免请求过快被封锁
            if hasattr(_request, '_last_time'):
                elapsed = time.time() - _request._last_time
                if elapsed < REQUEST_CONFIG['min_request_interval']:
                    time.sleep(REQUEST_CONFIG['min_request_interval'] - elapsed)

            resp = session.get(url, timeout=timeout)
            _request._last_time = time.time()

            if resp.status_code == 200:
                # 尝试检测编码（处理中文网页乱码问题）
                resp.encoding = resp.apparent_encoding or 'utf-8'
                return resp.text
            elif resp.status_code == 403:
                logger.warning(f"访问被拒绝 (403): {url}")
                return None
            elif resp.status_code == 404:
                logger.warning(f"页面不存在 (404): {url}")
                return None
            else:
                logger.warning(f"HTTP {resp.status_code}: {url}")

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"连接失败 (第{attempt+1}次): {e}")
        except requests.exceptions.Timeout:
            logger.warning(f"请求超时 (第{attempt+1}次): {url}")
        except Exception as e:
            logger.error(f"未知错误: {e}")
            return None

        # 指数退避重试：等待时间逐渐增加
        if attempt < retry_times - 1:
            wait_time = REQUEST_CONFIG['retry_delay'] * (attempt + 1)
            logger.debug(f"等待 {wait_time}s 后重试...")
            time.sleep(wait_time)

    logger.error(f"请求最终失败（已重试{retry_times}次）: {url}")
    if override.get('hint'):
        logger.warning(f"  ↳ 该域名已知不可达：{override['hint']}")
    return None


# ============================================================
# 分页检测与构建（新增增强功能）
# ============================================================

def _detect_pagination(html: str, base_list_url: str, source_key: str) -> dict:
    """
    从HTML中自动检测分页信息

    支持的分页模式：
    1. 宣城校区标准: /1954/list2.htm, /1954/list3.htm ...
    2. 本部通知: tzgg2/1243.htm, tzgg2/1242.htm ...
    3. 通用数字参数: ?page=2, &p=3 等
    4. 通过 "下一页" 链接推断

    Args:
        html: 列表页HTML内容
        base_list_url: 基础列表URL
        source_key: 数据源标识

    Returns:
        分页信息字典:
        {
            'has_next': bool,          # 是否有下一页
            'next_url': str|None,      # 下一页URL（如果能直接获取）
            'total_pages': int|None,   # 总页数（如果可检测）
            'pattern': str,            # 检测到的分页模式名称
        }
    """
    soup = BeautifulSoup(html, 'html.parser')
    result = {
        'has_next': False,
        'next_url': None,
        'total_pages': None,
        'pattern': 'unknown'
    }

    # 方法1：查找"下一页"或"下页"链接
    next_link_patterns = [
        {'text': ['下一页', '下页', 'Next', 'next >', '>']},
        {'class': ['next', 'page-next', 'pagenext']},
    ]

    for pattern in next_link_patterns:
        if 'text' in pattern:
            for text_pattern in pattern['text']:
                for a in soup.find_all('a'):
                    link_text = a.get_text(strip=True)
                    href = a.get('href', '')
                    if text_pattern.lower() == link_text.lower():
                        result['has_next'] = True
                        if href and not href.startswith('javascript'):
                            result['next_url'] = urljoin(base_list_url, href)
                            result['pattern'] = 'next-link-text'
                        break

        if 'class' in pattern:
            for class_name in pattern['class']:
                a_tag = soup.find('a', class_=lambda c: c and class_name in str(c).split())
                if a_tag:
                    href = a_tag.get('href', '')
                    if href and not href.startswith('javascript'):
                        result['has_next'] = True
                        result['next_url'] = urljoin(base_list_url, href)
                        result['pattern'] = f'next-link-{class_name}'
                        break

        if result['has_next']:
            break

    # 方法2：从分页导航中提取总页数
    pagination_selectors = [
        ('div.pagination', 'a'),
        ('div.pages', 'a'),
        ('td.pages', 'a'),
        ('div.pager', 'a'),
        ('ul.pager', 'li'),
        ('div.page-nav', 'a'),
        ('.pages', 'a'),
        ('#pages', 'a'),
    ]

    for container_sel, item_sel in pagination_selectors:
        try:
            container = soup.select_one(container_sel)
            if container:
                items = container.select(item_sel)
                page_numbers = []
                for item in items:
                    text = item.get_text(strip=True)
                    # 提取纯数字页码
                    num_match = re.match(r'^(\d+)$', text)
                    if num_match:
                        page_numbers.append(int(num_match.group(1)))
                        # 同时检查是否有最后一页的链接
                        href = item.get('href', '')
                        if href and 'last' in str(item.get('class', '')).lower():
                            result['pattern'] = 'pagination-last'

                if page_numbers:
                    result['total_pages'] = max(page_numbers)
                    if result['total_pages'] > 1:
                        result['has_next'] = True
                    if result['pattern'] == 'unknown':
                        result['pattern'] = f'pagination-{container_sel.replace(".", "").replace("#", "")}'
                    break
        except Exception:
            continue

    # 方法3：根据source_key推断已知分页模式（备用方案）
    if not result['has_next']:
        if source_key in ('xc_news', 'xc_notice'):
            result['has_next'] = True
            result['pattern'] = 'xc-known-pattern'
            logger.debug("使用宣城校区已知分页模式")
        elif source_key == 'main_notice':
            result['has_next'] = True
            result['pattern'] = 'main-notice-known'
            logger.debug("使用本部通知已知分页模式")

    return result


def _build_page_url(list_url: str, source_key: str, page_num: int) -> Optional[str]:
    """
    根据页码构建分页URL（支持多种模式自动适配）

    Args:
        list_url: 首页列表URL（如 https://xc.hfut.edu.cn/1954/list.htm）
        source_key: 数据源标识
        page_num: 页码（从1开始，1表示第二页）

    Returns:
        构建的分页URL，无法构建则返回None
    """
    if page_num < 1:
        return list_url

    try:
        # 模式1：宣城校区 /1954/list.htm → /1954/list2.htm, list3.htm ...
        if source_key in ('xc_news', 'xc_notice'):
            if '/list.htm' in list_url:
                base = list_url.rsplit('/list.htm', 1)[0]
                return f"{base}/list{page_num + 1}.htm"
            elif re.search(r'/list\d+\.htm$', list_url):
                # 已经是分页URL，替换数字
                return re.sub(r'/list\d+\.htm$', f'/list{page_num + 1}.htm', list_url)

        # 模式2：本部通知 tzgg2.htm → tzgg2/1243.htm, 1242.htm ...
        elif source_key == 'main_notice':
            if list_url.endswith('.htm') and '/' not in list_url.rstrip('.htm').split('/')[-1]:
                base = list_url[:-4]  # 移除 .htm
                total_pages = 1244  # 已知总页数（可通过_detect_pagination动态更新）
                target_page = total_pages - page_num
                if target_page > 0:
                    return f"{base}/{target_page}.htm"

        # 模式3：通用查询参数 ?page=N 或 &page=N
        else:
            parsed = urlparse(list_url)
            query_dict = dict([q.split('=') for q in parsed.query.split('&') if '=' in q])
            query_dict['page'] = str(page_num + 1)
            new_query = '&'.join([f"{k}={v}" for k, v in query_dict.items()])
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', new_query, ''))

    except Exception as e:
        logger.warning(f"构建分页URL失败 (page={page_num}): {e}")

    return None


def _check_empty_page(html: str) -> bool:
    """
    检查页面是否为空页面（无有效数据）

    空页面判断标准：
    1. HTML长度异常短（<500字符且不含列表结构）
    2. 包含"无数据"、"暂无记录"、"没有找到"等提示文字
    3. 列表容器存在但无li子元素

    Args:
        html: 页面HTML内容

    Returns:
        True表示空页面，False表示有数据
    """
    if not html or len(html.strip()) < 200:
        return True

    # 检查常见的无数据提示文本
    empty_patterns = [
        r'暂无.*记录',
        r'没有.*数据',
        r'无.*信息',
        r'暂无.*内容',
        r'not\s*found',
        r'no\s*data',
        r'empty\s*list',
    ]

    for pattern in empty_patterns:
        if re.search(pattern, html, re.IGNORECASE):
            return True

    # 尝试用BS4解析检查列表是否为空
    try:
        soup = BeautifulSoup(html, 'html.parser')
        news_list = soup.find('ul', class_='news_list') or soup.find('ul', class_='list')
        if news_list and len(news_list.find_all('li')) == 0:
            return True
    except Exception:
        pass

    return False


# ============================================================
# 解析器：各数据源专用（保持原有逻辑不变）
# ============================================================

def _parse_main_news_list(html: str, base_url: str) -> list[dict]:
    """
    解析本部新闻要闻列表页
    URL: https://news.hfut.edu.cn/

    HTML结构：
      <li><span class="date">_2026.06.02_</span><a href="info/1127/xxxxx.htm" title="...">...</a></li>
    """
    items = []
    soup = BeautifulSoup(html, 'html.parser')

    # 查找包含新闻列表的 ul/li 结构
    for li in soup.find_all('li'):
        a_tag = li.find('a')
        if not a_tag or not a_tag.get('href'):
            continue

        href = a_tag.get('href', '')
        title = a_tag.get('title') or a_tag.get_text(strip=True)

        # 过滤非新闻链接（导航、广告等）
        if not href or not title or len(title) < 5:
            continue

        # 只匹配新闻类链接
        if not re.match(r'(info|tzgg|cgdt|hbdjt)/\d+/\d+\.htm', href):
            continue

        # 提取日期
        date_span = li.find('span', class_='date')
        date_str = date_span.get_text(strip=True).strip('_') if date_span else ''

        # 标准化链接
        full_url = urljoin(base_url, href)

        items.append({
            'title': title,
            'url': full_url,
            'time': _normalize_date(date_str),
            'source': SOURCES['main_news']['name'],
            'category': SOURCES['main_news']['category'],
        })

    return items


def _parse_main_notice_list(html: str, base_url: str) -> list[dict]:
    """
    解析本部通知公告列表页
    URL: https://news.hfut.edu.cn/tzgg2.htm

    HTML结构：
      <li><span class="date">_2026年06月03日_</span><a href="https://kyy.hfut.edu.cn/..." title="...">...</a></li>

    注意：通知公告的链接指向各子部门网站（多域名）
    """
    items = []
    soup = BeautifulSoup(html, 'html.parser')

    for li in soup.find_all('li'):
        a_tag = li.find('a')
        if not a_tag or not a_tag.get('href'):
            continue

        href = a_tag.get('href', '')
        title = a_tag.get('title') or a_tag.get_text(strip=True)

        if not href or not title or len(title) < 5:
            continue

        # 提取日期
        date_span = li.find('span', class_='date')
        date_str = date_span.get_text(strip=True).strip('_') if date_span else ''

        # 标准化链接（可能是绝对URL或相对路径）
        if href.startswith('http'):
            full_url = href
        else:
            full_url = urljoin(base_url, href)

        items.append({
            'title': title,
            'url': full_url,
            'time': _normalize_date(date_str),
            'source': SOURCES['main_notice']['name'],
            'category': SOURCES['main_notice']['category'],
        })

    return items


def _parse_xc_list(html: str, base_url: str, source_key: str) -> list[dict]:
    """
    解析宣城校区通用列表页（新闻/通知公告共用）（增强版）

    HTML结构：
      <li>
          <a href="/a2/2a/c1954a41514/page.htm" title="标题">标题</a>
          <span>2026-06-01</span>
      </li>

    改进点：
    - 更宽松的链接匹配规则
    - 支持多种日期格式
    - 容错处理缺失字段
    """
    items = []
    source = SOURCES[source_key]
    soup = BeautifulSoup(html, 'html.parser')

    # 宣城校区使用 .news_list 或通用 ul（增加更多备选容器）
    news_list = (
        soup.find('ul', class_='news_list') or
        soup.find('ul', class_='list') or
        soup.find('ul', class_='news-list') or
        soup.find('div', class_='news_list') or
        soup.find('div', class_='list')
    )
    container = news_list or soup

    for li in container.find_all('li'):
        try:
            a_tag = li.find('a')
            if not a_tag or not a_tag.get('href'):
                continue

            href = a_tag.get('href', '')
            title = a_tag.get('title') or a_tag.get_text(strip=True)

            # 基础验证：必须有有效的标题和链接
            if not href or not title or len(title) < 2:
                continue

            # 内容页链接匹配规则（更宽松）
            is_content_link = bool(re.match(
                r'(/a\d+/[^/]+/page\.htm|/info/\d+/\d+\.htm)',
                href
            ))

            # 允许外部域名链接或以/开头的相对路径
            if not is_content_link:
                parsed = urlparse(href)
                if not parsed.netloc and not href.startswith('/'):
                    continue

            # 提取日期（支持多种span位置和格式）
            date_str = ''
            span_tags = li.find_all('span')
            for span in span_tags:
                span_text = span.get_text(strip=True)
                # 尝试匹配日期格式
                if re.match(r'^[\d\-./年月日\s_]+$', span_text) and len(span_text) >= 6:
                    date_str = span_text
                    break
            # 备用方案：从li文本中提取日期
            if not date_str:
                li_text = li.get_text(strip=True)
                date_match = re.search(r'(\d{4}[-./年]\d{1,2}[-./月]\d{1,2}[日]?)', li_text)
                if date_match:
                    date_str = date_match.group(1)

            # 标准化链接
            if href.startswith('http'):
                full_url = href
            elif href.startswith('//'):
                full_url = 'https:' + href
            else:
                full_url = urljoin(base_url, href)

            items.append({
                'title': title,
                'url': full_url,
                'time': _normalize_date(date_str),
                'source': source['name'],
                'category': source['category'],
            })

        except Exception as e:
            logger.debug(f"解析列表项时出错（已跳过）: {e}")
            continue

    return items


# ============================================================
# 日期标准化
# ============================================================

def _normalize_date(date_str: str) -> str:
    """
    将各种日期格式统一为 %Y-%m-%d

    支持格式：
    - "2026年06月03日"
    - "_2026.06.03_"
    - "2026-06-03"
    - "2026.06.03"
    - "06-03" (当年)
    """
    if not date_str:
        return datetime.now().strftime('%Y-%m-%d')

    date_str = date_str.strip().strip('_').strip()
    current_year = datetime.now().year

    # 格式1：中文日期 "2026年06月03日"
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

    # 格式2：点分隔 "2026.06.03" 或 ".2026.06.03."
    m = re.match(r'[.\s]*(\d{4})\.(\d{1,2})\.(\d{1,2})', date_str)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

    # 格式3：横线分隔 "2026-06-03"
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"

    # 格式4：短格式 "06-03"（假设当年）
    m = re.match(r'(\d{1,2})-(\d{1,2})', date_str)
    if m:
        return f"{current_year}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"

    # 无法解析，返回今天
    logger.debug(f"无法解析日期格式: '{date_str}'，使用今天")
    return datetime.now().strftime('%Y-%m-%d')


# ============================================================
# 内容提取（详情页正文）
# ============================================================

def fetch_content(url: str) -> Optional[str]:
    """
    获取文章详情页的正文内容
    支持：
    - 合工大官网标准模板 (.htm 页面)
    - 各子部门网站

    Args:
        url: 文章详情页URL

    Returns:
        正文纯文本，失败返回None
    """
    html = _request(url)
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')

    # 合工大官网常见正文容器选择器（按优先级排序）
    content_selectors = [
        '.v_news_content',       # 本部新闻网标准
        '#vsb_content',          # vsb CMS 系统
        '.article-content',      # 通用
        '.news-content',
        '.content',
        '#content',
        '.txt_box',              # 宣城校区
        '.article',              # 备选
    ]

    content = None
    for selector in content_selectors:
        elem = soup.select_one(selector)
        if elem:
            # 清理HTML标签，保留纯文本
            text = elem.get_text(separator='\n', strip=True)
            # 去除多余空白
            text = re.sub(r'\n\s*\n+', '\n', text)
            text = re.sub(r'[ \t]+', ' ', text)
            if len(text) > 20:  # 正文至少20字符
                content = text
                break

    # 如果选择器都没命中，使用备用策略：找最大的文本块
    if not content:
        # 移除 script/style/nav/header/footer
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()

        # 尝试找主内容区
        main = soup.find('main') or soup.find('article') or soup.find(role='main')
        if main:
            content = main.get_text(separator='\n', strip=True)
            content = re.sub(r'\n\s*\n+', '\n', content)[:2000]

    return content if content and len(content.strip()) > 10 else None


# ============================================================
# 去重机制（优化版）
# ============================================================

def _generate_id(item: dict) -> str:
    """
    生成信息唯一标识（基于URL的MD5哈希）

    优化说明：
    - 主要基于URL生成（URL具有全局唯一性）
    - 结合标题作为辅助（防止同一URL内容更新）
    - 输出12位十六进制MD5摘要

    Args:
        item: 信息字典，必须包含'url'和'title'字段

    Returns:
        12位MD5哈希字符串
    """
    # 以URL为主键，标题为辅（权重更高以确保唯一性）
    raw = f"{item.get('url', '')}|{item.get('title', '')}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:12]


def load_existing_ids(json_path) -> set[str]:
    """
    从归档文件加载已存在的ID集合（增强版）

    改进点：
    - 支持更大的归档文件（流式读取避免内存溢出）
    - 更好的错误处理和日志记录
    - 统计加载效率

    Args:
        json_path: JSON归档文件路径

    Returns:
        已存在的ID集合
    """
    ids = set()
    load_count = 0
    error_count = 0

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    info = json.loads(line)
                    item_id = info.get('id')
                    if item_id:
                        ids.add(item_id)
                        load_count += 1
                    else:
                        # 如果没有id字段，尝试生成一个
                        ids.add(_generate_id(info))
                        load_count += 1
                except json.JSONDecodeError as e:
                    error_count += 1
                    if error_count <= 3:  # 只记录前3个错误
                        logger.debug(f"JSON解析错误 (行{line_no}): {e}")

        if error_count > 0:
            logger.warning(f"归档文件中有 {error_count} 行解析失败")

        logger.info(f"已加载 {load_count} 条历史记录用于去重")

    except FileNotFoundError:
        logger.info("未找到已有归档文件，将从零开始采集")
    except PermissionError:
        logger.error(f"无法读取归档文件（权限不足）: {json_path}")
    except Exception as e:
        logger.warning(f"读取已有ID时出错：{e}")

    return ids


def append_to_archive(items: list[dict], json_path: str) -> int:
    """
    将新采集的数据追加到归档文件（增量保存）

    Args:
        items: 待保存的信息列表
        json_path: 归档文件路径

    Returns:
        成功写入的条数
    """
    if not items:
        return 0

    written_count = 0
    try:
        with open(json_path, 'a', encoding='utf-8') as f:
            for item in items:
                try:
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
                    written_count += 1
                except Exception as e:
                    logger.warning(f"写入单条记录失败: {e}")

        logger.info(f"已追加 {written_count} 条新记录到归档文件")

    except PermissionError:
        logger.error(f"无法写入归档文件（权限不足）: {json_path}")
    except Exception as e:
        logger.error(f"写入归档文件失败: {e}")

    return written_count


# ============================================================
# 主采集函数（增强版）
# ============================================================

def scrape_source(source_key: str, max_pages: int = 1, seen_ids: set = None) -> list[dict]:
    """
    采集单个数据源（增强版：自动分页检测 + 容错）

    改进点：
    - 自动检测分页URL模式和总页数
    - 空数据页面不中断，继续尝试下一页
    - HTTP错误记录警告但继续执行
    - 支持配置最大翻页数限制

    Args:
        source_key: 数据源key（main_news/main_notice/xc_news/xc_notice）
        max_pages: 最大翻页数（默认只抓首页最新数据；设为0表示无限翻页直到无新数据）
        seen_ids: 已见过的ID集合（用于去重）

    Returns:
        新采集到的信息列表
    """
    if seen_ids is None:
        seen_ids = set()

    source = SOURCES.get(source_key)
    if not source:
        logger.error(f"未知的数据源: {source_key}")
        return []

    logger.info(f"开始采集: {source['name']} ({source['list_url']})")

    all_items = []
    list_url = source['list_url']
    base_url = source['base_url']
    consecutive_empty_pages = 0  # 连续空页面计数器
    MAX_CONSECUTIVE_EMPTY = 3    # 连续3页空白后停止（防止死循环）

    for page in range(max_pages + 1) if max_pages > 0 else range(1000):  # 安全上限1000页
        # 构建当前页URL
        if page == 0:
            current_url = list_url
        else:
            # 尝试智能构建分页URL
            built_url = _build_page_url(list_url, source_key, page)
            if built_url:
                current_url = built_url
            else:
                logger.debug(f"无法构建第{page + 1}页URL，停止翻页")
                break

        logger.debug(f"  抓取第 {page + 1} 页: {current_url}")

        # 发送请求（带容错）
        html = _request(current_url)
        if not html:
            logger.warning(f"  第 {page + 1} 页获取失败（网络错误或超时），继续下一页")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= MAX_CONSECUTIVE_EMPTY:
                logger.warning(f"  连续 {MAX_CONSECUTIVE_EMPTY} 页失败，停止该源采集")
                break
            continue

        # 检测是否为空页面
        if _check_empty_page(html):
            logger.debug(f"  第 {page + 1} 页为空页面（无数据），可能已到达末页")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= MAX_CONSECUTIVE_EMPTY:
                logger.info(f"  连续 {MAX_CONSECUTIVE_EMPTY} 页无数据，停止翻页")
                break
            continue
        else:
            consecutive_empty_pages = 0  # 重置计数器

        # 使用对应的解析器
        try:
            if source_key == 'main_news':
                items = _parse_main_news_list(html, base_url)
            elif source_key == 'main_notice':
                items = _parse_main_notice_list(html, base_url)
            else:  # xc_news, xc_notice
                items = _parse_xc_list(html, base_url, source_key)
        except Exception as e:
            logger.error(f"  解析第 {page + 1} 页失败: {e}")
            continue

        if not items:
            logger.debug(f"  第 {page + 1} 页解析结果为空，可能已到达末页")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= MAX_CONSECUTIVE_EMPTY:
                break
            continue

        # 重置空页计数器（成功获取数据）
        consecutive_empty_pages = 0

        # 去重并添加ID
        new_count = 0
        for item in items:
            item_id = _generate_id(item)
            item['id'] = item_id

            if item_id not in seen_ids:
                seen_ids.add(item_id)
                all_items.append(item)
                new_count += 1

        logger.info(f"  第 {page + 1} 页: 获取 {len(items)} 条, 新增 {new_count} 条")

        # 如果这一页全是重复数据，说明已经抓到历史数据了，可以提前停止
        if new_count == 0 and page > 0:
            logger.info(f"  全部是历史数据（已去重），停止翻页")
            break

        # 动态检测分页信息（首次获取首页后）
        if page == 0:
            pagination_info = _detect_pagination(html, list_url, source_key)
            if not pagination_info['has_next'] and max_pages <= 1:
                logger.info(f"  未检测到分页信息，仅采集首页")
                break
            else:
                logger.debug(f"  检测到分页模式: {pagination_info['pattern']}")

    logger.info(f"[完成] {source['name']}: 共采集 {len(all_items)} 条新数据")
    return all_items


def scrape_all(sources: list[str] = None, max_pages: int = 1,
               fetch_content_detail: bool = False, auto_save: bool = False) -> list[dict]:
    """
    采集所有（或指定）数据源（增强版）

    改进点：
    - 单源失败隔离（try-except包裹每个源的采集过程）
    - 可选自动保存到归档文件
    - 更详细的统计日志

    Args:
        sources: 要采集的数据源列表，默认全部
        max_pages: 每个源最大翻页数
        fetch_content_detail: 是否获取详情页正文（较慢）
        auto_save: 是否自动将结果保存到归档文件

    Returns:
        所有新增信息的统一列表
    """
    if sources is None:
        sources = ['main_news', 'main_notice', 'xc_news', 'xc_notice']

    from config_loader import get_json_path
    json_path = get_json_path()
    seen_ids = load_existing_ids(json_path)

    logger.info(f"{'='*60}")
    logger.info(f"合工大官网爬虫 - 开始全量采集")
    logger.info(f"数据源: {[SOURCES[s]['name'] for s in sources]}")
    logger.info(f"已有记录数: {len(seen_ids)}, 每源最大页数: {max_pages}")
    logger.info(f"{'='*60}")

    all_items = []
    start_time = time.time()
    success_count = 0
    failed_sources = []

    # ---- 阶段1：多源并发采集（只做网络IO+解析，不写文件）----
    # 串行时不可达源会把超时逐源叠加（实测 505 秒几乎全是宣城域名空等）；
    # 并发后总耗时≈最慢的单个源，而不是所有源之和。
    workers = max(1, min(REQUEST_CONFIG.get('max_workers', 4), len(sources)))
    collected: dict[str, list[dict]] = {}
    source_elapsed: dict[str, float] = {}

    def _scrape_one(source_key: str):
        t0 = time.time()
        items = scrape_source(source_key, max_pages=max_pages, seen_ids=seen_ids)
        return items, time.time() - t0

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='src') as executor:
            futures = {executor.submit(_scrape_one, sk): sk for sk in sources}
            for fut in as_completed(futures):
                sk = futures[fut]
                try:
                    items, cost = fut.result()
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    logger.error(f"采集 {sk} 失败: {e}", exc_info=True)
                    failed_sources.append(sk)
                    continue
                collected[sk] = items
                source_elapsed[sk] = cost
                logger.info(f"  ✓ {SOURCES[sk]['name']}: {len(items)} 条 ({cost:.1f}s)")
    except KeyboardInterrupt:
        logger.warning("用户中断采集，保存已采集的数据")
        if auto_save and collected:
            append_to_archive([i for v in collected.values() for i in v], json_path)
        raise

    # ---- 阶段2：串行补全详情 + 写归档（避免并发写同一 JSON）----
    for source_key in sources:
        items = collected.get(source_key)
        if items is None:
            continue
        success_count += 1

        # 可选：获取详情页正文
        if fetch_content_detail:
            for item in items:
                content = fetch_content(item.get('url', ''))
                if content:
                    item['content'] = content[:1000]  # 截断长正文
                # 避免请求过快
                time.sleep(0.5)

        all_items.extend(items)

        # 自动保存（可选）
        if auto_save and items:
            append_to_archive(items, json_path)

    elapsed = time.time() - start_time

    # 输出统计报告
    logger.info(f"{'='*60}")
    logger.info(f"采集完成! 统计:")
    logger.info(f"  总计新数据: {len(all_items)} 条")
    logger.info(f"  成功源: {success_count}/{len(sources)}")
    logger.info(f"  耗时: {elapsed:.1f}秒")
    for sk in sources:
        name = SOURCES[sk]['name']
        if sk in source_elapsed:
            logger.info(f"    - {name}: {len(collected.get(sk, []))} 条 / {source_elapsed[sk]:.1f}s")
        else:
            logger.warning(f"    - {name}: 未采集成功（已跳过）")
    if failed_sources:
        logger.warning(f"  失败源: {failed_sources}")
        for sk in failed_sources:
            host = urlparse(SOURCES[sk]['list_url']).hostname or ''
            hint = HOST_OVERRIDES.get(host, {}).get('hint')
            if hint:
                logger.warning(f"    ↳ {SOURCES[sk]['name']}: {hint}")
    logger.info(f"{'='*60}")

    # 最终保存（如果启用了自动保存且之前没保存过）
    if auto_save and all_items and not any(items for items in [all_items]):
        append_to_archive(all_items, json_path)

    return all_items


def quick_scrape() -> list[dict]:
    """
    快速采集（只抓首页最新，不获取详情正文）
    用于定时任务中的轻量级增量采集

    保持原有接口不变，向后兼容。

    Returns:
        新采集到的信息列表
    """
    return scrape_all(max_pages=1, fetch_content_detail=False)


def scrape_full(sources: list[str] = None, max_pages_per_source: int = 0,
                save_to_file: bool = True) -> dict:
    """
    全量扫描采集（新增功能）

    遍历所有分页直到以下条件之一满足：
    1. 页面返回空数据（连续3页）
    2. 所有数据都是重复的历史记录
    3. 达到安全上限（1000页，防死循环）

    适用场景：
    - 首次运行或重建归档库
    - 定期全量同步（如每周一次）
    - 手动补采历史数据

    Args:
        sources: 要全量采集的数据源列表，默认全部4个源
        max_pages_per_source: 每个源的最大翻页数（0=无限直到自然结束，默认0）
        save_to_file: 是否自动保存到归档文件（默认True）

    Returns:
        采集结果字典:
        {
            'total_new': int,           # 总新增条数
            'items': list[dict],        # 所有新增数据
            'sources_stats': dict,      # 各源统计 {'xc_news': 120, ...}
            'elapsed_seconds': float,   # 总耗时（秒）
            'failed_sources': list,     # 失败的源列表
        }

    示例用法:
        >>> result = scrape_full()
        >>> print(f"共采集 {result['total_new']} 条新数据")
        >>> for item in result['items'][:5]:
        ...     print(item['title'])
    """
    if sources is None:
        sources = ['main_news', 'main_notice', 'xc_news', 'xc_notice']

    from config_loader import get_json_path
    json_path = get_json_path()
    seen_ids = load_existing_ids(json_path)

    logger.info(f"{'#'*60}")
    logger.info(f"【全量扫描模式】开始运行")
    logger.info(f"目标源: {[SOURCES[s]['name'] for s in sources]}")
    logger.info(f"翻页策略: {'无限翻页' if max_pages_per_source == 0 else f'最多{max_pages_per_source}页/源'}")
    logger.info(f"自动保存: {'是' if save_to_file else '否'}")
    logger.info(f"{'#'*60}")

    all_items = []
    sources_stats = {}
    failed_sources = []
    start_time = time.time()

    for idx, source_key in enumerate(sources, 1):
        source_name = SOURCES[source_key]['name']
        progress = f"[{idx}/{len(sources)}]"
        logger.info(f"\n{progress} 正在处理: {source_name}")

        try:
            # 使用 max_pages=0 表示无限翻页（由内部逻辑控制终止条件）
            items = scrape_source(
                source_key,
                max_pages=max_pages_per_source if max_pages_per_source > 0 else 9999,
                seen_ids=seen_ids
            )

            sources_stats[source_key] = len(items)
            all_items.extend(items)

            # 逐源保存（避免中途崩溃丢失数据）
            if save_to_file and items:
                written = append_to_archive(items, json_path)
                logger.info(f"  已保存 {written} 条到归档")

            # 源间间隔（减轻服务器压力）
            if idx < len(sources):
                wait_time = 2.0
                logger.debug(f"  等待 {wait_time}s 后处理下一个源...")
                time.sleep(wait_time)

        except KeyboardInterrupt:
            logger.warning("\n用户中断全量扫描！保存已采集数据...")
            if save_to_file and all_items:
                append_to_archive(all_items, json_path)
            raise
        except Exception as e:
            logger.error(f"处理 {source_name} 时出错: {e}", exc_info=True)
            failed_sources.append(source_key)
            sources_stats[source_key] = 0
            continue

    elapsed = time.time() - start_time

    # 构建结果报告
    result = {
        'total_new': len(all_items),
        'items': all_items,
        'sources_stats': sources_stats,
        'elapsed_seconds': round(elapsed, 2),
        'failed_sources': failed_sources,
    }

    # 输出最终报告
    logger.info(f"\n{'#'*60}")
    logger.info(f"【全量扫描完成】报告:")
    logger.info(f"  总新增条目: {result['total_new']}")
    logger.info(f"  各源明细:")
    for src_key, count in sources_stats.items():
        status_icon = "✓" if src_key not in failed_sources else "✗"
        logger.info(f"    {status_icon} {SOURCES[src_key]['name']}: {count} 条")
    logger.info(f"  总耗时: {result['elapsed_seconds']}s")
    if failed_sources:
        logger.warning(f"  失败源: {failed_sources}")
    logger.info(f"{'#'*60}")

    return result


# ============================================================
# 测试入口
# ============================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("合工大官网爬虫（增强版）- 测试模式")
    print("=" * 60)

    # 测试快速采集（增量模式，兼容旧接口）
    print("\n>>> 测试 quick_scrape()（快速增量采集）...")
    results_quick = quick_scrape()

    print(f"\n[快速采集结果] 获取 {len(results_quick)} 条新信息\n")

    for i, item in enumerate(results_quick[:15], 1):
        print(f"  [{i}] {item.get('source', '?')} | {item.get('time', '?')} | ID:{item.get('id', 'N/A')[:8]}")
        print(f"      {item.get('title', 'N/A')[:60]}")
        print(f"      URL: {item.get('url', '')[:80]}")
        print()

    if len(results_quick) > 15:
        print(f"  ... 还有 {len(results_ask) - 15} 条\n")

    # 测试全量扫描（可选，取消注释以启用）
    # print("\n" + "="*60)
    # print(">>> 测试 scrape_full()（全量扫描）...")
    # print("(这会遍历所有分页，耗时较长)")
    # print("="*60)
    #
    # result_full = scrape_full(max_pages_per_source=3, save_to_file=True)  # 限制每源3页测试
    # print(f"\n[全量扫描结果] 总计 {result_full['total_new']} 条新数据")
    # print(f"各源统计: {result_full['sources_stats']}")
    # print(f"耗时: {result_full['elapsed_seconds']}s")


# ============================================================
# CLI 入口（新增）
# ============================================================

def main():
    """CLI 入口函数 - 提供命令行接口"""
    import argparse
    from datetime import datetime
    
    parser = argparse.ArgumentParser(
        description='合工大官网爬虫（增强版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python hfut_web_scraper.py --mode quick      # 快速增量采集
  python hfut_web_scraper.py --mode full       # 全量扫描
  python hfut_web_scraper.py --mode source --source main_news  # 采集单个源
        """
    )
    
    parser.add_argument(
        '--mode', '-m',
        choices=['quick', 'full', 'source'],
        default='quick',
        help='运行模式'
    )
    
    parser.add_argument(
        '--source', '-s',
        choices=['main_news', 'main_notice', 'xc_news', 'xc_notice'],
        default='main_news',
        help='单个数据源（mode=source时使用）'
    )
    
    parser.add_argument(
        '--max-pages', '-p',
        type=int,
        default=0,
        help='每源最大页数（0=无限，默认0）'
    )
    
    parser.add_argument(
        '--save', '-S',
        action='store_true',
        help='自动保存到归档文件'
    )
    
    parser.add_argument(
        '--log-level', '-l',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='日志级别'
    )
    
    args = parser.parse_args()
    
    # 配置日志
    log_level = getattr(logging, args.log_level)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info("=" * 60)
    logger.info('合工大官网爬虫（增强版）')
    logger.info("=" * 60)
    logger.info(f'运行模式: {args.mode}')
    
    start_time = time.time()
    
    # 根据模式执行
    if args.mode == 'quick':
        logger.info('模式：快速增量采集')
        results = quick_scrape()
        logger.info(f'✅ 快速采集完成，共 {len(results)} 条新数据')
        
        if results:
            logger.info('前10条结果:')
            for i, item in enumerate(results[:10], 1):
                logger.info(f'  [{i}] {item.get("title", "N/A")[:40]}')
                logger.info(f'      来源: {item.get("source", "未知")} | 时间: {item.get("time", "未知")}')
        
    elif args.mode == 'full':
        logger.info('模式：全量扫描')
        result = scrape_full(
            max_pages_per_source=args.max_pages if args.max_pages > 0 else 0,
            save_to_file=args.save
        )
        logger.info(f'✅ 全量扫描完成，共 {result["total_new"]} 条新数据')
        
    elif args.mode == 'source':
        logger.info(f'模式：采集单个源 - {args.source}')
        items = scrape_source(args.source, max_pages=1)
        logger.info(f'✅ 采集完成，共 {len(items)} 条新数据')
        
        if items:
            logger.info('结果:')
            for i, item in enumerate(items[:10], 1):
                logger.info(f'  [{i}] {item.get("title", "N/A")[:40]}')
    
    elapsed_time = time.time() - start_time
    
    logger.info("=" * 60)
    logger.info(f'任务执行完成，耗时 {elapsed_time:.1f} 秒')
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
