"""
Playwright 增强抓取器 - PlaywrightScraper
===========================================

使用 Playwright 浏览器自动化增强微信公众号抓取能力：

    - 微信公众号搜狗搜索结果深度抓取
    - 自动翻页获取更多结果
    - 截图保存（用于 OCR 或人工查看）
    - 比纯 requests 更能绕过反爬

依赖（可选）：
    pip install playwright && playwright install chromium

若 Playwright 不可用，自动降级到 requests + BeautifulSoup。

使用示例:
    from scripts.playwright_scraper import PlaywrightScraper

    scraper = PlaywrightScraper(headless=True)

    articles = scraper.search_wechat_articles("合肥工业大学", max_pages=2)
    content = scraper.fetch_article_content("https://mp.weixin.qq.com/s/...")
    scraper.screenshot_page("https://mp.weixin.qq.com/s/...", "output/screenshot.png")
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

logger = logging.getLogger("HFUT_Monitor.PlaywrightScraper")

# ==================== Playwright 可用性检测 ====================

_PLAYWRIGHT_AVAILABLE: Optional[bool] = None


def _check_playwright() -> bool:
    """检测 Playwright 是否可用（含浏览器）"""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is not None:
        return _PLAYWRIGHT_AVAILABLE

    try:
        import importlib

        # 检查库是否安装
        importlib.import_module("playwright")
        # 检查浏览器是否安装（通过 sync_api 检查）
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
                _PLAYWRIGHT_AVAILABLE = True
                logger.info("✅ Playwright + Chromium 可用")
            except Exception as e:
                logger.warning(
                    "Playwright 已安装但 Chromium 浏览器不可用: %s，请运行 "
                    "`playwright install chromium`",
                    e,
                )
                _PLAYWRIGHT_AVAILABLE = False
    except ImportError:
        logger.warning(
            "Playwright 未安装，将使用 requests 降级模式。"
            "安装: pip install playwright && playwright install chromium"
        )
        _PLAYWRIGHT_AVAILABLE = False

    return _PLAYWRIGHT_AVAILABLE


def _load_playwright_config() -> dict:
    """从 config.json 加载 Playwright 配置"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("playwright", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ==================== PlaywrightScraper ====================

class PlaywrightScraper:
    """Playwright 增强抓取器

    支持微信公众号搜狗搜索、文章内容提取、页面截图。
    Playwright 不可用时自动降级到 requests 方案。

    Attributes:
        headless: 是否无头模式运行浏览器
        timeout: 页面加载超时（毫秒）
        enable: 是否启用 Playwright（False 时使用降级模式）
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        timeout: Optional[int] = None,
        enable: Optional[bool] = None,
    ):
        """初始化抓取器

        Args:
            headless: 无头模式，默认从 config.json 读取
            timeout: 超时（毫秒），默认从 config.json 读取
            enable: 是否启用，默认自动检测
        """
        cfg = _load_playwright_config()

        self.headless = headless if headless is not None else cfg.get("headless", True)
        self.timeout = timeout if timeout is not None else cfg.get("timeout", 30000)
        self.enable = enable if enable is not None else cfg.get("enable", True)

        # 实际可用性
        self._pw_available = self.enable and _check_playwright()

        if self._pw_available:
            logger.info(
                "PlaywrightScraper 初始化完成 (headless=%s, timeout=%dms)",
                self.headless,
                self.timeout,
            )
        else:
            logger.info(
                "PlaywrightScraper 初始化完成 (降级模式: requests)"
            )

    @property
    def is_available(self) -> bool:
        """Playwright 是否可用"""
        return self._pw_available

    # ---------- 公开方法 ----------

    def search_wechat_articles(
        self,
        keyword: str,
        max_pages: int = 3,
        screenshot_dir: Optional[str] = None,
    ) -> list[dict]:
        """搜索微信公众号文章

        通过搜狗微信搜索 (weixin.sogou.com) 检索文章。

        Args:
            keyword: 搜索关键词
            max_pages: 最大翻页数
            screenshot_dir: 截图保存目录（可选）

        Returns:
            文章列表，每条: {title, url, account_name, publish_time, digest}
        """
        if not keyword:
            logger.warning("搜索关键词为空，跳过")
            return []

        logger.info("搜索微信公众号文章: keyword=%s, max_pages=%d", keyword, max_pages)

        if self._pw_available:
            return self._search_with_playwright(keyword, max_pages, screenshot_dir)
        else:
            return self._search_with_requests(keyword, max_pages)

    def fetch_article_content(self, url: str) -> str:
        """获取微信公众号文章全文

        Args:
            url: 文章链接

        Returns:
            文章正文文本（纯文本，去除 HTML 标签）
        """
        if not url:
            return ""

        logger.info("获取文章内容: %s", url[:80])

        if self._pw_available:
            return self._fetch_with_playwright(url)
        else:
            return self._fetch_with_requests(url)

    def screenshot_page(
        self,
        url: str,
        output_path: str,
        full_page: bool = True,
    ) -> bool:
        """截图存档页面

        Args:
            url: 页面 URL
            output_path: 截图保存路径
            full_page: 是否全页截图

        Returns:
            是否成功
        """
        if not url:
            logger.warning("URL 为空，无法截图")
            return False

        if not self._pw_available:
            logger.warning("Playwright 不可用，无法截图: %s", url[:60])
            return False

        logger.info("截图: %s → %s", url[:60], output_path)

        try:
            # 确保输出目录存在
            out_dir = os.path.dirname(output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                try:
                    page.goto(url, timeout=self.timeout, wait_until="networkidle")
                    page.wait_for_timeout(2000)  # 额外等待渲染
                    page.screenshot(
                        path=output_path,
                        full_page=full_page,
                    )
                    logger.info("✅ 截图保存成功: %s", output_path)
                    return True
                finally:
                    context.close()
                    browser.close()

        except Exception as e:
            logger.error("截图失败: %s", e)
            return False

    # ---------- Playwright 实现 ----------

    def _search_with_playwright(
        self,
        keyword: str,
        max_pages: int,
        screenshot_dir: Optional[str],
    ) -> list[dict]:
        """使用 Playwright 搜索微信公众号文章"""
        from playwright.sync_api import sync_playwright

        results: list[dict] = []
        seen_urls: set[str] = set()

        encoded_keyword = quote(keyword)
        base_url = (
            f"https://weixin.sogou.com/weixin"
            f"?type=2&query={encoded_keyword}"
        )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()

                try:
                    for pg in range(1, max_pages + 1):
                        url = base_url if pg == 1 else f"{base_url}&page={pg}"

                        logger.info("  加载第 %d 页: %s", pg, url[:80])
                        page.goto(url, timeout=self.timeout, wait_until="networkidle")
                        page.wait_for_timeout(2000)

                        # 截图（可选）
                        if screenshot_dir:
                            os.makedirs(screenshot_dir, exist_ok=True)
                            screenshot_path = os.path.join(
                                screenshot_dir,
                                f"wechat_search_{keyword}_{pg}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
                            )
                            page.screenshot(path=screenshot_path, full_page=False)
                            logger.debug("  搜索页截图: %s", screenshot_path)

                        # 解析文章列表
                        # 搜狗微信搜索结果在 .news-box 或 .txt-box 中
                        articles = page.query_selector_all("li")
                        page_items = 0

                        for article in articles:
                            try:
                                # 尝试获取标题和链接
                                title_el = article.query_selector("h3 a")
                                if not title_el:
                                    title_el = article.query_selector("a[href*='mp.weixin.qq.com']")
                                if not title_el:
                                    continue

                                title = title_el.inner_text().strip()
                                link = title_el.get_attribute("href", "")

                                if not title or not link:
                                    continue
                                if link in seen_urls:
                                    continue

                                seen_urls.add(link)

                                # 尝试获取摘要
                                digest = ""
                                digest_el = article.query_selector(".txt-info")
                                if digest_el:
                                    digest = digest_el.inner_text().strip()

                                # 尝试获取公众号名称和时间
                                account_name = ""
                                publish_time = ""
                                account_el = article.query_selector(".account")
                                if account_el:
                                    account_name = account_el.inner_text().strip()
                                time_el = article.query_selector(".s2")
                                if time_el:
                                    publish_time = time_el.inner_text().strip()

                                results.append({
                                    "title": title,
                                    "url": link,
                                    "account_name": account_name,
                                    "publish_time": publish_time,
                                    "digest": digest[:200] if digest else "",
                                })
                                page_items += 1

                            except Exception as e:
                                logger.debug("  解析文章条目失败: %s", e)
                                continue

                        logger.info(
                            "  第 %d 页获取 %d 篇文章",
                            pg,
                            page_items,
                        )

                        if page_items == 0:
                            logger.info("  无更多结果，停止翻页")
                            break

                        # 翻页延迟
                        if pg < max_pages:
                            time.sleep(2)

                finally:
                    context.close()
                    browser.close()

        except Exception as e:
            logger.error("Playwright 搜索失败: %s", e)
            # 降级
            logger.info("降级到 requests 模式...")
            return self._search_with_requests(keyword, max_pages)

        logger.info(
            "✅ 搜索完成: keyword=%s, 共 %d 篇（去重后）",
            keyword,
            len(results),
        )
        return results

    def _fetch_with_playwright(self, url: str) -> str:
        """使用 Playwright 获取文章内容"""
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()

                try:
                    page.goto(url, timeout=self.timeout, wait_until="networkidle")
                    page.wait_for_timeout(2000)

                    # 提取正文（微信公众号文章内容在 #js_content 中）
                    content_el = page.query_selector("#js_content")
                    if content_el:
                        text = content_el.inner_text()
                    else:
                        # 回退到 body
                        text = page.inner_text("body")

                    context.close()
                    browser.close()

                    return text.strip()

                except Exception:
                    context.close()
                    browser.close()
                    raise

        except Exception as e:
            logger.error("Playwright 获取内容失败: %s", e)
            return self._fetch_with_requests(url)

    # ---------- Requests 降级实现 ----------

    @staticmethod
    def _search_with_requests(keyword: str, max_pages: int) -> list[dict]:
        """使用 requests 降级搜索（搜狗微信 API）"""
        import requests as req

        results: list[dict] = []
        seen_urls: set[str] = set()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            session = req.Session()
            session.headers.update(headers)

            for pg in range(1, max_pages + 1):
                url = (
                    f"https://weixin.sogou.com/weixin"
                    f"?type=2&query={quote(keyword)}"
                )
                if pg > 1:
                    url += f"&page={pg}"

                logger.info("  [requests] 加载第 %d 页", pg)

                try:
                    resp = session.get(url, timeout=15)
                    resp.raise_for_status()
                except req.RequestException as e:
                    logger.warning("  [requests] 请求失败: %s", e)
                    break

                # 简单正则提取（比 BeautifulSoup 更轻量）
                html = resp.text

                # 提取文章条目
                # 搜狗微信搜索结果中，文章链接格式: https://mp.weixin.qq.com/s/...
                link_pattern = re.compile(
                    r'href="(https?://mp\.weixin\.qq\.com/s/[^"]+)"'
                )
                title_pattern = re.compile(
                    r'<h3[^>]*>.*?<a[^>]*>([^<]+)</a>',
                    re.DOTALL,
                )

                links = link_pattern.findall(html)
                titles = title_pattern.findall(html)

                page_items = 0
                for i, link in enumerate(links):
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)

                    title = titles[i].strip() if i < len(titles) else "无标题"

                    # 清理标题中的 HTML 标签
                    title = re.sub(r"<[^>]+>", "", title)
                    title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

                    results.append({
                        "title": title,
                        "url": link,
                        "account_name": "",
                        "publish_time": "",
                        "digest": "",
                    })
                    page_items += 1

                logger.info(
                    "  [requests] 第 %d 页获取 %d 篇",
                    pg,
                    page_items,
                )

                if page_items == 0:
                    break

                time.sleep(1.5)

        except Exception as e:
            logger.error("[requests] 搜索异常: %s", e)

        return results

    @staticmethod
    def _fetch_with_requests(url: str) -> str:
        """使用 requests 降级获取文章内容"""
        import requests as req

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            resp = req.get(url, headers=headers, timeout=15)
            resp.raise_for_status()

            html = resp.text

            # 简单提取正文
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            text = text.strip()

            # 截取合理长度
            if len(text) > 5000:
                text = text[:5000] + "..."

            return text

        except Exception as e:
            logger.error("[requests] 获取内容失败: %s", e)
            return ""


# ==================== 便捷函数 ====================

def search_wechat_articles(
    keyword: str,
    max_pages: int = 3,
) -> list[dict]:
    """便捷函数：搜索微信公众号文章

    Args:
        keyword: 搜索关键词
        max_pages: 最大翻页数

    Returns:
        文章列表
    """
    scraper = PlaywrightScraper()
    return scraper.search_wechat_articles(keyword, max_pages)


def fetch_article_content(url: str) -> str:
    """便捷函数：获取文章内容

    Args:
        url: 文章链接

    Returns:
        文章正文文本
    """
    scraper = PlaywrightScraper()
    return scraper.fetch_article_content(url)


# ==================== 自测 ====================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    scraper = PlaywrightScraper()

    print("=" * 60)
    print("PlaywrightScraper 自测")
    print(f"Playwright 可用: {scraper.is_available}")
    print("=" * 60)

    # 测试搜索
    articles = scraper.search_wechat_articles("合肥工业大学", max_pages=1)
    print(f"\n搜索 '合肥工业大学' 结果 ({len(articles)} 篇):")
    for i, a in enumerate(articles[:5], 1):
        print(f"  [{i}] {a['title'][:50]}")
        print(f"      URL: {a['url'][:60]}...")

    if articles:
        # 测试获取内容
        print(f"\n获取第 1 篇文章内容...")
        content = scraper.fetch_article_content(articles[0]["url"])
        print(f"  内容长度: {len(content)} 字符")
        print(f"  预览: {content[:200]}...")
