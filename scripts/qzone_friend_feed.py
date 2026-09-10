"""
QQ空间好友动态监控模块（多级降级方案）
=========================================

核心功能：监控自己的QQ空间好友动态Feed，识别竞赛/二手/有用信息。

多级降级策略：
┌─────────────────────────────────────────────────────────────┐
│ Plan A: qzone-api 内部 get_feeds() → API直连好友动态端点     │
│ Plan B: Playwright 浏览器自动化 → 可视化操作+截图取证        │
│ Plan C: agent-browser CLI → 轻量无头浏览器抓取               │
│ Plan D: requests + cookie → 直接HTTP请求 feeds3_html_more    │
│ Plan E: 本地cookie持久化 → 自动重试 + 人工降级通知           │
└─────────────────────────────────────────────────────────────┘

与 qzone_scraper.py 的区别：
- qzone_scraper.py: 抓取指定好友的「个人说说」
- qzone_friend_feed.py: 抓取自己的「好友动态流」(朋友们的帖子聚合)

数据结构统一格式：
{
    "source": "qzone_feed",
    "source_type": "QQ空间好友动态",
    "author_qq": "发布者QQ",
    "author_name": "发布者昵称",
    "title": "内容摘要(前50字)",
    "content": "完整内容",
    "publish_time": "2026-06-08 12:30",
    "publish_timestamp": 1749357000,
    "url": "https://user.qzone.qq.com/{qq}/...",
    "images": ["url1", "url2"],  # 可选
    "likes": 0,                   # 可选
    "comments": 0                 # 可选
}

作者：CodeBuddy Code
日期：2026-06-08
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config_loader import get_data_dir, get_log_dir

logger = logging.getLogger('QzoneFriendFeed')

BEIJING_TZ = timezone(timedelta(hours=8))
DATA_DIR = get_data_dir()
FEED_DATA_FILE = DATA_DIR / 'qzone_friend_feed.json'
FEED_COOKIE_FILE = DATA_DIR / 'qzone_feed_cookies.json'

# ==================== 能力检测 ====================

GLOBALS = {
    'qzone_api': False,
    'playwright': False,
    'agent_browser': False,
    'requests_ok': True,
}

try:
    from qzone_api import QzoneApi
    from qzone_api.login import QzoneLogin
    from qzone_api.api.api_parms import get_feeds
    GLOBALS['qzone_api'] = True
    logger.info("✅ qzone-api 可用 (Plan A)")
except ImportError:
    logger.warning("⚠️ qzone-api 不可用，Plan A 降级")

try:
    from playwright.async_api import async_playwright
    GLOBALS['playwright'] = True
    logger.info("✅ Playwright 可用 (Plan B)")
except ImportError:
    logger.warning("⚠️ Playwright 不可用，Plan B 降级")

AGENT_BROWSER_AVAIL = False
try:
    result = subprocess.run(['agent-browser', '--version'], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        GLOBALS['agent_browser'] = True
        logger.info("✅ agent-browser 可用 (Plan C)")
except Exception:
    logger.warning("⚠️ agent-browser 不可用，Plan C 降级")


# ==================== Plan A: qzone-api 好友动态 ====================

class QzoneFriendFeedPlanA:
    """Plan A: 使用 qzone-api 的 get_feeds() 获取好友动态"""

    def __init__(self):
        self.qq: str = ''
        self.g_tk: int = 0
        self.cookies: Dict[str, str] = {}
        self.is_logged_in: bool = False

    @property
    def clean_qq(self) -> str:
        """去除 QQ UIN 前缀 (如 'o<测试QQ号>' → '<测试QQ号>')"""
        return re.sub(r'^[a-zA-Z]+', '', self.qq) if self.qq else ''

    async def login(self, timeout: int = 120) -> Dict[str, Any]:
        """二维码登录 (复用 qzone-api 登录流程)

        无人值守（自动化巡检）场景说明：
            没有人会去扫二维码，等满 120 秒纯属浪费时间——实测 2026-06-11 那次巡检
            就在这一步白等了约 2 分钟。
            可用环境变量 QZONE_LOGIN_TIMEOUT 覆盖等待时长；
            设为 0 表示完全跳过二维码登录，立即走降级链路（Plan B/C/D/E）。
        """
        timeout = int(os.getenv('QZONE_LOGIN_TIMEOUT', str(timeout)))
        if timeout <= 0:
            logger.warning(
                "⏭️ [Plan A] QZONE_LOGIN_TIMEOUT<=0，无人值守模式：跳过二维码登录，直接降级"
            )
            return {
                'success': False,
                'message': '无人值守模式，已跳过二维码登录',
                'plan': 'A',
                'fallback_to': 'B',
            }

        logger.info(f"🔐 [Plan A] 开始 QQ 空间登录 (二维码方式, 最长等待 {timeout}s)")

        if not GLOBALS['qzone_api']:
            return {'success': False, 'message': 'qzone-api 不可用', 'plan': 'A', 'fallback_to': 'B'}

        try:
            handler = QzoneLogin()

            def _open_qr():
                time.sleep(1.5)
                qr_path = Path(os.getcwd()) / 'QR_feed.png'
                if qr_path.exists():
                    try:
                        os.startfile(str(qr_path))
                    except Exception:
                        subprocess.run(['start', str(qr_path)], shell=True)

            import threading
            threading.Thread(target=_open_qr, daemon=True).start()

            result = await handler.login(timeout=timeout)

            if result.get('code') != 0:
                return {'success': False, 'message': result.get('msg', '登录失败'), 'plan': 'A'}

            self.cookies = result.get('cookies', {})
            self.g_tk = result.get('bkn', 0)
            self.qq = str(result.get('qq', ''))
            self.is_logged_in = True

            self._save_cookies()
            logger.info(f"✅ [Plan A] 登录成功, QQ: {self.qq}")
            return {'success': True, 'plan': 'A', 'qq': self.qq}

        except Exception as e:
            logger.error(f"❌ [Plan A] 登录异常: {e}")
            return {'success': False, 'message': str(e), 'plan': 'A', 'fallback_to': 'B'}

    def _save_cookies(self):
        FEED_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FEED_COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'cookies': self.cookies,
                'qq': self.qq,
                'g_tk': self.g_tk,
                'save_time': datetime.now(BEIJING_TZ).isoformat()
            }, f, ensure_ascii=False, indent=2)

    async def load_cookies(self) -> bool:
        """加载已保存的 cookies（仅加载，不验证有效性）"""
        if not FEED_COOKIE_FILE.exists():
            return False
        try:
            with open(FEED_COOKIE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.cookies = data.get('cookies', {})
            self.qq = str(data.get('qq', ''))
            self.g_tk = data.get('g_tk', 0)

            # 检查 cookie 年龄（超过 7 天强制重新登录）
            save_time_str = data.get('save_time', '')
            if save_time_str:
                try:
                    save_dt = datetime.fromisoformat(save_time_str)
                    age = (datetime.now(BEIJING_TZ) - save_dt).days
                    if age >= 7:
                        logger.warning("⚠️ [Plan A] cookies 已过期 %d 天（>=7天阈值），需要重新扫码", age)
                        return False
                except Exception:
                    pass

            self.is_logged_in = True
            logger.info("✅ [Plan A] 已加载 cookies (QQ: %s)", self.qq)
            return True
        except Exception as e:
            logger.warning("加载 cookies 失败: %s", e)
            return False

    async def check_cookies_valid(self) -> bool:
        """
        探测 cookies 是否仍然有效（轻量级 HTTP 探针）
        用最简单的请求验证登录态，避免在 fetch 时才发现失效
        """
        if not self.cookies or not self.is_logged_in:
            return False

        import requests as _req
        try:
            # 用一个轻量端点探测：QQ空间个人主页
            test_url = f"https://user.qzone.qq.com/{self.clean_qq}"
            resp = _req.get(
                test_url,
                cookies=self._format_cookies(),
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://qzone.qq.com/',
                },
                timeout=10,
                allow_redirects=False
            )

            # 如果返回 302 重定向到登录页 → cookie 已失效
            if resp.status_code in (301, 302, 303):
                location = resp.headers.get('Location', '')
                if 'login' in location.lower() or 'ptlogin' in location.lower():
                    logger.warning("⚠️ [Plan A] cookie 探测失败：被重定向到登录页 (HTTP %d)", resp.status_code)
                    self.is_logged_in = False
                    return False

            # 200 且页面内容包含空间标识 → 有效
            if resp.status_code == 200 and len(resp.text) > 500:
                logger.info("✅ [Plan A] cookie 探测通过 (HTTP 200, %d bytes)", len(resp.text))
                return True

            # 其他情况视为可疑
            logger.warning("⚠️ [Plan A] cookie 探测结果异常 (HTTP %d, %d bytes)", resp.status_code, len(resp.text))
            return False

        except Exception as e:
            logger.warning("⚠️ [Plan A] cookie 探测异常: %s", e)
            return False

    async def fetch_friend_feed(self, page: int = 1, count: int = 30, begin_time: int = 0) -> Dict[str, Any]:
        """
        获取好友动态Feed

        Args:
            page: 页码
            count: 每页条数 (建议 10-30)
            begin_time: 开始时间戳 (0 = 获取全部)

        Returns:
            {"success": bool, "plan": "A", "data": [...], "total": int}
        """
        if not self.is_logged_in:
            return {'success': False, 'message': '未登录', 'plan': 'A', 'fallback_to': 'B'}

        try:
            # 构建 feed 参数 (与 qzone-api 内部 get_feeds 函数一致)
            clean_qq_int = int(self.clean_qq) if self.clean_qq else 0
            params = get_feeds(clean_qq_int, self.g_tk, page=page, count=count, begintime=begin_time)

            # 直接调用 API
            from qzone_api.api.api_base import ApiBase
            api_base = ApiBase()
            url = 'https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more'
            raw = await api_base._make_get_request(url, params, self._format_cookies())

            if not raw:
                return {'success': False, 'message': 'API 返回空', 'plan': 'A'}

            # 解析 feed 数据
            feed_items = self._parse_feed_data(raw)
            logger.info(f"✅ [Plan A] 获取好友动态 {len(feed_items)} 条 (page={page})")
            return {'success': True, 'plan': 'A', 'data': feed_items, 'total': len(feed_items)}

        except Exception as e:
            logger.error(f"❌ [Plan A] 获取好友动态失败: {e}")
            return {'success': False, 'message': str(e), 'plan': 'A', 'fallback_to': 'B'}

    def _format_cookies(self) -> str:
        return '; '.join([f'{k}={v}' for k, v in self.cookies.items()])

    @staticmethod
    def _decode_html_content(html: str) -> str:
        """解码 \\xNN hex 转义为实际字符（QQ空间 html 字段专用）"""
        def _replace_hex(m):
            return chr(int(m.group(1), 16))
        return re.sub(r'\\x([0-9a-fA-F]{2})', _replace_hex, html)

    @staticmethod
    def _strip_html_tags(html: str) -> str:
        """去除 HTML 标签，提取纯文本"""
        text = re.sub(r'<[^>]+>', '', html)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _extract_feed_item(self, feed_block: str) -> Optional[Dict[str, Any]]:
        """从单个 feed JS 对象块中提取字段"""
        try:
            nickname = re.search(r"nickname:\s*'([^']*)'", feed_block)
            uin = re.search(r"uin:\s*'([^']*)'", feed_block)
            abstime = re.search(r"abstime:\s*'(\d+)'", feed_block)
            userhome = re.search(r"userHome:\s*'([^']*)'", feed_block)

            # 提取 html 字段内容（可能跨数千字符，包含 \\xNN 转义）
            html_val = re.search(r"html:\s*'((?:[^'\\]|\\[xX][0-9a-fA-F]{2}|\\[^xX])*)'", feed_block)
            
            content = ''
            if html_val and html_val.group(1).strip():
                html_raw = self._decode_html_content(html_val.group(1))
                content = self._strip_html_tags(html_raw)
            
            if not content or len(content) < 3:
                # fallback: 尝试 summary 字段
                summary = re.search(r"summary:\s*'([^']*)'", feed_block)
                if summary and summary.group(1).strip():
                    content = summary.group(1).strip()

            if not content or len(content) < 3:
                return None  # 跳过无实质内容的 feed

            item = {
                'id': hashlib.md5(feed_block.encode()).hexdigest()[:16],
                'source': 'qzone_feed',
                'source_type': 'QQ空间好友动态',
                'author_qq': uin.group(1) if uin else '',
                'author_name': nickname.group(1).strip() if nickname else '',
                'content': content,
                'title': content[:50],
                'publish_time': '',
                'publish_timestamp': 0,
                'url': userhome.group(1) if userhome else '',
            }

            if abstime:
                ts = int(abstime.group(1))
                item['publish_timestamp'] = ts
                item['publish_time'] = datetime.fromtimestamp(ts, tz=BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

            return item

        except Exception:
            return None

    def _parse_feed_data(self, raw_data) -> List[Dict[str, Any]]:
        """解析 feeds3_html_more 返回的原始数据（括号配对法，最稳健）"""
        items = []

        try:
            raw_str = str(raw_data)

            # 找到 data:[ ... ] 的起止位置（括号配对追踪）
            data_start = raw_str.find('data:[')
            if data_start < 0:
                return items

            arr_start = data_start + 5
            depth = 0
            arr_end = -1
            for i, c in enumerate(raw_str[arr_start:]):
                if c == '[': depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        arr_end = arr_start + i
                        break
            if arr_end < 0:
                return items

            feeds_str = raw_str[arr_start + 1:arr_end]

            # 用 { } 配对拆分为每条 feed
            depth = 0
            feed_start = -1
            for i, c in enumerate(feeds_str):
                if c == '{':
                    if depth == 0:
                        feed_start = i
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0 and feed_start >= 0:
                        feed_items = self._extract_feed_item(feeds_str[feed_start:i + 1])
                        if feed_items:
                            items.append(feed_items)
                        feed_start = -1

        except Exception as e:
            logger.error(f"解析feed数据失败: {e}")

        return items


# ==================== Plan B: Playwright 浏览器抓取 ====================

class QzoneFriendFeedPlanB:
    """Plan B: Playwright 浏览器自动化"""

    async def fetch_friend_feed(self, cookie_file: Optional[str] = None, headless: bool = True,
                                 max_scrolls: int = 5) -> Dict[str, Any]:
        """
        Playwright 浏览器抓取好友动态

        Args:
            cookie_file: cookies文件路径 (用于恢复登录状态)
            headless: 是否无头模式
            max_scrolls: 最大滚动次数 (模拟翻页)

        Returns:
            {"success": bool, "plan": "B", "data": [...], "screenshot": str}
        """
        if not GLOBALS['playwright']:
            return {'success': False, 'message': 'Playwright 不可用', 'plan': 'B', 'fallback_to': 'C'}

        logger.info(f"🌐 [Plan B] 启动 Playwright (headless={headless})")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=headless)
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 900},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
                )

                # 尝试加载cookies恢复登录
                if cookie_file and FEED_COOKIE_FILE.exists():
                    try:
                        with open(FEED_COOKIE_FILE, 'r', encoding='utf-8') as f:
                            saved = json.load(f)
                        cookie_list = []
                        for k, v in saved.get('cookies', {}).items():
                            cookie_list.append({'name': k, 'value': v, 'domain': '.qq.com', 'path': '/'})
                        await context.add_cookies(cookie_list)
                        logger.info("[Plan B] 已加载 cookies")
                    except Exception:
                        pass

                page = await context.new_page()

                # 导航到 QQ 空间好友动态页 (使用新版 H5 页面)
                friend_feed_url = 'https://h5.qzone.qq.com/feeds/feeds_list'
                await page.goto(friend_feed_url, wait_until='networkidle', timeout=30000)
                await asyncio.sleep(3)

                # 滚动加载更多内容
                items = []
                for i in range(max_scrolls):
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(2)

                    # 提取当前可见的动态 (兼容新版 H5 + 旧版 v6 选择器)
                    feeds = await page.evaluate("""
                        () => {
                            const items = [];
                            // 新版 H5 选择器
                            const selectors = [
                                '.feed-item', '.f-item', '[data-feedsflag]',
                                '.feeds-item', '.feed-card', '.masonry__item',
                                '[class*=\"feed\"]', 'li[data-id]',
                                '.f-dynamic-item', '.dynamic-item'
                            ];
                            const allEls = new Set();
                            for (const sel of selectors) {
                                document.querySelectorAll(sel).forEach(el => allEls.add(el));
                            }
                            allEls.forEach(el => {
                                const text = el.innerText?.trim() || '';
                                if (text.length < 10) return;  // 跳过空白/太短的
                                const timeEl = el.querySelector('.f-date, .time, .f-note, [class*=\"time\"], [class*=\"date\"]');
                                const nameEl = el.querySelector('.f-name, .nickname, .user-name, [class*=\"name\"], [class*=\"nick\"]');
                                const linkEl = el.querySelector('a');
                                items.push({
                                    content: text.substring(0, 500),
                                    time: timeEl?.innerText?.trim() || '',
                                    name: nameEl?.innerText?.trim() || '',
                                    link: linkEl?.href || ''
                                });
                            });
                            return items;
                        }
                    """)

                    for f in feeds:
                        if f.get('content') and f['content'] not in [it.get('content') for it in items]:
                            items.append({
                                'id': hashlib.md5(f.get('content', '').encode()).hexdigest()[:16],
                                'source': 'qzone_feed',
                                'source_type': 'QQ空间好友动态',
                                'author_name': f.get('name', ''),
                                'content': f.get('content', ''),
                                'title': (f.get('content', '') or '')[:50],
                                'publish_time': f.get('time', ''),
                                'url': f.get('link', ''),
                            })

                # 截图作为备份
                screenshot_dir = DATA_DIR / 'screenshots'
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = screenshot_dir / f'qzone_feed_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
                await page.screenshot(path=str(screenshot_path), full_page=True)

                await browser.close()

                logger.info(f"✅ [Plan B] 获取好友动态 {len(items)} 条, 截图: {screenshot_path}")
                return {'success': True, 'plan': 'B', 'data': items, 'total': len(items),
                        'screenshot': str(screenshot_path)}

        except Exception as e:
            logger.error(f"❌ [Plan B] Playwright 抓取失败: {e}")
            return {'success': False, 'message': str(e), 'plan': 'B', 'fallback_to': 'C'}


# ==================== Plan C: agent-browser 抓取 ====================

class QzoneFriendFeedPlanC:
    """Plan C: agent-browser CLI 抓取"""

    @staticmethod
    async def fetch_friend_feed(friend_feed_url: str = None) -> Dict[str, Any]:
        """
        使用 agent-browser 抓取好友动态页面

        Returns:
            {"success": bool, "plan": "C", "data": [...], "screenshot": str}
        """
        if not GLOBALS['agent_browser']:
            return {'success': False, 'message': 'agent-browser 不可用', 'plan': 'C', 'fallback_to': 'D'}

        if friend_feed_url is None:
            friend_feed_url = 'https://user.qzone.qq.com/proxy/domain/qzs.qzone.qq.com/qzone/v6/friend/friend_dynamic.html'

        logger.info(f"🖥️ [Plan C] 启动 agent-browser: {friend_feed_url}")

        try:
            # 打开页面
            subprocess.run(['agent-browser', 'open', friend_feed_url], capture_output=True, text=True, timeout=30)
            await asyncio.sleep(3)

            # 等待加载
            subprocess.run(['agent-browser', 'wait', '--load', 'networkidle'], capture_output=True, text=True, timeout=20)

            # 采集内容
            snapshot = subprocess.run(['agent-browser', 'snapshot'], capture_output=True, text=True, timeout=15)

            # 截图
            screenshot_dir = DATA_DIR / 'screenshots'
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshot_dir / f'qzone_feed_ag_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
            subprocess.run(['agent-browser', 'screenshot', str(screenshot_path)], capture_output=True, text=True, timeout=15)

            # 关闭浏览器
            subprocess.run(['agent-browser', 'close'], capture_output=True, text=True, timeout=10)

            # 解析 snapshot 文本
            items = QzoneFriendFeedPlanC._parse_snapshot(snapshot.stdout)

            logger.info(f"✅ [Plan C] 获取好友动态 {len(items)} 条, 截图: {screenshot_path}")
            return {'success': True, 'plan': 'C', 'data': items, 'total': len(items),
                    'screenshot': str(screenshot_path)}

        except subprocess.TimeoutExpired:
            logger.error("❌ [Plan C] agent-browser 超时")
            try:
                subprocess.run(['agent-browser', 'close'], capture_output=True, timeout=10)
            except Exception:
                pass
            return {'success': False, 'message': 'agent-browser 超时', 'plan': 'C', 'fallback_to': 'D'}

        except Exception as e:
            logger.error(f"❌ [Plan C] agent-browser 失败: {e}")
            try:
                subprocess.run(['agent-browser', 'close'], capture_output=True, timeout=10)
            except Exception:
                pass
            return {'success': False, 'message': str(e), 'plan': 'C', 'fallback_to': 'D'}

    @staticmethod
    def _parse_snapshot(text: str) -> List[Dict[str, Any]]:
        """从 agent-browser snapshot 文本中提取内容"""
        items = []
        if not text:
            return items

        # 按段落分割
        lines = text.strip().split('\n')
        current = {}
        for line in lines:
            line = line.strip()
            if not line:
                if current.get('content'):
                    current['id'] = hashlib.md5(current['content'].encode()).hexdigest()[:16]
                    current['source'] = 'qzone_feed'
                    current['source_type'] = 'QQ空间好友动态'
                    items.append(current)
                current = {}
            else:
                if 'content' not in current:
                    current['content'] = line
                    current['title'] = line[:50]
                else:
                    current['content'] += ' ' + line

        if current.get('content'):
            current['id'] = hashlib.md5(current['content'].encode()).hexdigest()[:16]
            current['source'] = 'qzone_feed'
            current['source_type'] = 'QQ空间好友动态'
            items.append(current)

        return items


# ==================== Plan D: requests + cookie ====================

class QzoneFriendFeedPlanD:
    """Plan D: 纯 HTTP 请求 (requests + cookie)"""

    @staticmethod
    def fetch_friend_feed(attempt: int = 1) -> Dict[str, Any]:
        """
        使用 requests + cookie 尝试访问好友动态端点

        Returns:
            {"success": bool, "plan": "D", "data": [...]}
        """
        import requests

        logger.info(f"📡 [Plan D] 尝试 HTTP 请求 (attempt={attempt})")
        url = 'https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more'

        # 加载 cookies
        cookies_dict = {}
        if FEED_COOKIE_FILE.exists():
            try:
                with open(FEED_COOKIE_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                cookies_dict = saved.get('cookies', {})
            except Exception:
                pass

        if not cookies_dict:
            logger.warning("❌ [Plan D] 无可用 cookies，降级到 Plan E")
            return {'success': False, 'message': '无可用 cookies', 'plan': 'D', 'fallback_to': 'E'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://qzone.qq.com/',
        }

        try:
            resp = requests.get(url, cookies=cookies_dict, headers=headers, timeout=15)

            if resp.status_code == 200 and len(resp.text) > 100:
                items = QzoneFriendFeedPlanD._parse_text_response(resp.text)
                logger.info(f"✅ [Plan D] HTTP 请求成功, 获取 {len(items)} 项")
                return {'success': True, 'plan': 'D', 'data': items, 'total': len(items)}
            else:
                return {'success': False, 'message': f'HTTP {resp.status_code}', 'plan': 'D', 'fallback_to': 'E'}

        except Exception as e:
            logger.error(f"❌ [Plan D] HTTP 请求失败: {e}")
            return {'success': False, 'message': str(e), 'plan': 'D', 'fallback_to': 'E'}

    @staticmethod
    def _parse_text_response(text: str) -> List[Dict[str, Any]]:
        """解析纯文本HTTP响应"""
        items = []
        try:
            json_match = re.search(r'_Callback\((\{.*\})\)', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                feed_list = data.get('data', {}).get('data', []) if isinstance(data, dict) else []

                for feed in feed_list:
                    if isinstance(feed, dict) and feed.get('summary', {}).get('summary'):
                        items.append({
                            'id': hashlib.md5(str(feed).encode()).hexdigest()[:16],
                            'source': 'qzone_feed',
                            'source_type': 'QQ空间好友动态',
                            'content': feed['summary']['summary'],
                            'title': feed['summary']['summary'][:50],
                        })
        except Exception:
            pass
        return items


# ==================== 统一入口 ====================

class QzoneFriendFeedOrchestrator:
    """
    QQ空间好友动态统一采集器

    自动按 Plan A → B → C → D → E 多级降级尝试

    使用方法:
        orchestrator = QzoneFriendFeedOrchestrator()
        result = await orchestrator.collect()
        if result['success']:
            print(f"获取 {result['total']} 条好友动态, 使用方案: {result['plan']}")
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.plan_a = QzoneFriendFeedPlanA() if GLOBALS['qzone_api'] else None
        self.plan_b = QzoneFriendFeedPlanB() if GLOBALS['playwright'] else None
        self.plan_d = QzoneFriendFeedPlanD()

    async def collect(self, count: int = 30, begin_time: int = 0) -> Dict[str, Any]:
        """
        采集好友动态 (自动多级降级)

        Args:
            count: 获取条数
            begin_time: 开始时间戳 (0=获取全部)

        Returns:
            {"success": bool, "plan": "A/B/C/D/E", "data": [...], "total": int, "plan_history": [...]}
        """
        result = {'success': False, 'data': [], 'total': 0, 'plan': None, 'plan_history': []}

        # ====== Plan A: qzone-api ======
        if self.plan_a:
            logger.info("🚀 尝试 Plan A: qzone-api get_feeds()")
            result['plan_history'].append('A')

            # 三步登录流程：①加载文件 → ②探测有效性 → ③过期才弹二维码
            need_login = False

            if not self.plan_a.is_logged_in:
                # Step 1: 尝试从文件加载 cookies
                load_ok = await self.plan_a.load_cookies()
                if not load_ok:
                    logger.info("[Plan A] 无本地 cookie 文件，需要扫码登录")
                    need_login = True
                else:
                    # Step 2: Cookie 存在，探测是否仍然有效
                    logger.info("[Plan A] 探测 cookie 有效性...")
                    valid = await self.plan_a.check_cookies_valid()
                    if not valid:
                        logger.warning("[Plan A] cookie 已失效，需要重新扫码")
                        self.plan_a.is_logged_in = False
                        need_login = True
                    else:
                        logger.info("[Plan A] cookie 有效，直接使用")

            else:
                # 已有登录状态（可能是代码层面设置的），也探测一下
                valid = await self.plan_a.check_cookies_valid()
                if not valid:
                    logger.warning("[Plan A] 已有登录态但 cookie 探测失效，需要重新扫码")
                    self.plan_a.is_logged_in = False
                    need_login = True

            # Step 3: 需要登录时才弹二维码
            if need_login:
                logger.info("🔐 [Plan A] 触发二维码登录（cookie 不存在或已过期）...")
                login_result = await self.plan_a.login(timeout=120)
                if not login_result['success']:
                    logger.warning("⚠️ Plan A 登录失败: %s", login_result.get('message'))
                    result['plan_history'].append('A:login_failed')
                else:
                    logger.info("✅ Plan A 登录成功")

            if self.plan_a.is_logged_in:
                feed_result = await self.plan_a.fetch_friend_feed(count=count, begin_time=begin_time)
                if feed_result['success']:
                    result.update({
                        'success': True, 'plan': 'A',
                        'data': feed_result['data'], 'total': feed_result['total']
                    })
                    self._save_feed_data(feed_result['data'])
                    return result
                else:
                    logger.warning("⚠️ Plan A 获取失败: %s", feed_result.get('message'))
                    result['plan_history'].append('A:fetch_failed')
            else:
                logger.warning("⚠️ Plan A 未登录，降级到 Plan B")

        # ====== Plan B: Playwright ======
        if self.plan_b and GLOBALS['playwright']:
            logger.info("🚀 尝试 Plan B: Playwright 浏览器抓取")
            result['plan_history'].append('B')

            headless = self.config.get('playwright', {}).get('headless', True)
            pb_result = await self.plan_b.fetch_friend_feed(
                cookie_file=str(FEED_COOKIE_FILE) if FEED_COOKIE_FILE.exists() else None,
                headless=headless
            )
            if pb_result['success']:
                result.update({
                    'success': True, 'plan': 'B',
                    'data': pb_result['data'], 'total': pb_result['total'],
                    'screenshot': pb_result.get('screenshot')
                })
                self._save_feed_data(pb_result['data'])
                return result
            else:
                logger.warning(f"⚠️ Plan B 失败: {pb_result.get('message')}")
                result['plan_history'].append('B:failed')

        # ====== Plan C: agent-browser ======
        if GLOBALS['agent_browser']:
            logger.info("🚀 尝试 Plan C: agent-browser 抓取")
            result['plan_history'].append('C')

            pc_result = await QzoneFriendFeedPlanC.fetch_friend_feed()
            if pc_result['success']:
                result.update({
                    'success': True, 'plan': 'C',
                    'data': pc_result['data'], 'total': pc_result['total'],
                    'screenshot': pc_result.get('screenshot')
                })
                self._save_feed_data(pc_result['data'])
                return result
            else:
                logger.warning(f"⚠️ Plan C 失败: {pc_result.get('message')}")
                result['plan_history'].append('C:failed')

        # ====== Plan D: HTTP requests ======
        logger.info("🚀 尝试 Plan D: requests + cookie")
        result['plan_history'].append('D')

        pd_result = self.plan_d.fetch_friend_feed()
        if pd_result['success']:
            result.update({
                'success': True, 'plan': 'D',
                'data': pd_result['data'], 'total': pd_result['total']
            })
            self._save_feed_data(pd_result['data'])
            return result
        else:
            logger.warning(f"⚠️ Plan D 失败: {pd_result.get('message')}")
            result['plan_history'].append('D:failed')

        # ====== Plan E: 最终降级 ======
        logger.warning("⚠️ 所有方案均失败，进入 Plan E: 保存状态，等待人工干预")
        result['plan_history'].append('E')
        result['plan'] = 'E'
        result['message'] = ('所有自动化方案均失败。需要:\n'
                             '1. 手动登录一次 QQ 空间获取 cookies\n'
                             '2. 或者运行 python scripts/qzone_friend_feed.py --login\n'
                             '3. 扫码登录后 cookies 会被保存，下次自动重试')

        # 尝试从本地文件加载缓存
        cached = self._load_cached_data()
        if cached:
            result['data'] = cached
            result['total'] = len(cached)
            result['message'] += f'\n已加载上次缓存的 {len(cached)} 条数据'

        return result

    def _save_feed_data(self, data: List[Dict]):
        """保存获取的数据到本地"""
        try:
            FEED_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

            # 与现有数据合并去重
            existing = self._load_cached_data()
            existing_ids = {item.get('id') for item in existing}
            new_items = [item for item in data if item.get('id') not in existing_ids]

            if new_items:
                all_data = existing + new_items
                with open(FEED_DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump({
                        'update_time': datetime.now(BEIJING_TZ).isoformat(),
                        'total': len(all_data),
                        'items': all_data
                    }, f, ensure_ascii=False, indent=2)
                logger.info(f"💾 已保存 {len(new_items)} 条新数据 (总计 {len(all_data)} 条)")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    @staticmethod
    def _load_cached_data() -> List[Dict]:
        """加载缓存的 feed 数据"""
        if not FEED_DATA_FILE.exists():
            return []
        try:
            with open(FEED_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('items', [])
        except Exception:
            return []


# ==================== 快捷入口 ====================

async def fetch_qzone_friend_feed(count: int = 30, config: Dict = None) -> Dict[str, Any]:
    """快捷函数: 获取QQ空间好友动态"""
    orchestrator = QzoneFriendFeedOrchestrator(config)
    return await orchestrator.collect(count=count)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='QQ空间好友动态采集')
    parser.add_argument('--login', action='store_true', help='仅执行登录')
    parser.add_argument('--count', type=int, default=30, help='获取条数')
    parser.add_argument('--begin-time', type=int, default=0, help='开始时间戳')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

    async def main():
        orchestrator = QzoneFriendFeedOrchestrator()

        if args.login:
            if orchestrator.plan_a:
                result = await orchestrator.plan_a.login(timeout=120)
                print(f"\n登录结果: {result}")
            else:
                print("qzone-api 不可用，无法登录")
            return

        result = await orchestrator.collect(count=args.count, begin_time=args.begin_time)
        print(f"\n{'='*50}")
        print(f"采集结果: 成功={result['success']}, 方案={result['plan']}, 条数={result['total']}")
        print(f"降级历史: {' -> '.join(result['plan_history'])}")
        print(f"{'='*50}")

    asyncio.run(main())
