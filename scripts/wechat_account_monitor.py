"""
微信公众号精准监控模块
功能：监控指定的四个微信公众号，提取最新文章（真实数据，非模拟）

依赖：weixin_search_mcp（已安装在 ../weixin_search_mcp/）
数据输出格式（与现有系统兼容）：
{
    "source": "wechat_account",
    "account_name": "公众号名称",
    "title": "文章标题",
    "content": "正文摘要",
    "time": "2026-06-04",
    "url": "https://mp.weixin.qq.com/...",
    "account_id": "公众号标识"
}
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger("HFUT_Monitor")


# 配置的四个微信公众号（用户可修改）
WECHAT_ACCOUNTS = [
    {
        "name": "合肥工业大学",
        "account_id": "gh_f3a8a6b0c9d1",  # 示例ID，需要真实ID
        "priority": "高",
        "description": "合工大官方公众号"
    },
    {
        "name": "合工大教务处",
        "account_id": "gh_a1b2c3d4e5f6",
        "priority": "高", 
        "description": "教务处通知公告"
    },
    {
        "name": "合工大研究生招生",
        "account_id": "gh_g7h8i9j0k1l",
        "priority": "高",
        "description": "研究生招生信息"
    },
    {
        "name": "合工大学生工作部",
        "account_id": "gh_m3n4o5p6q7r",
        "priority": "中",
        "description": "学生工作通知"
    }
]

# 每个公众号最多获取的文章数
MAX_ARTICLES_PER_ACCOUNT = 10

# 只获取最近N天的文章（避免旧文章）
MAX_DAYS_AGO = 7


def _load_config():
    """加载配置"""
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            # 从配置中读取公众号列表（如果有的话）
            accounts = config.get("wechat_accounts", WECHAT_ACCOUNTS)
            return accounts
    return WECHAT_ACCOUNTS


def _get_search_fn():
    """
    延迟导入 weixin_search_mcp 的搜索函数
    """
    try:
        weixin_mcp_dir = Path(__file__).parent.parent.parent / "weixin_search_mcp"
        if str(weixin_mcp_dir) not in sys.path:
            sys.path.insert(0, str(weixin_mcp_dir))

        from weixin_search_mcp.tools.weixin_search import (
            sogou_weixin_search,
            get_article_content,
        )
        return sogou_weixin_search, get_article_content
    except ImportError as e:
        logger.error("❌ weixin_search_mcp 未安装: %s", e)
        logger.info("   安装方式: cd D:/项目整理/01-代码项目 && git clone https://github.com/fancyboi999/weixin_search_mcp.git")
        logger.info("          cd weixin_search_mcp && pip install -e .")
        return None, None


def _fetch_hfut_official_news(max_articles: int = 10) -> List[Dict]:
    """
    降级方案：当搜狗微信搜索被反爬时，直接从合工大官网获取新闻
    作为公众号数据的替代来源（官网新闻 ≈ 公众号推送内容）

    URL 来源（2026-06-09 验证）：
      - news.hfut.edu.cn/ 首页包含各栏目新闻（info/*.htm 格式）
      - gdyw1.htm = 工大要闻列表
      - zhxw1.htm = 综合新闻列表
      - tzgg2.htm = 通知公告
      - xc.hfut.edu.cn = 宣城校区（网络不稳定，超时则跳过）
    """
    import requests as _req

    # 使用验证过的正确 URL（list1.htm 等 404，已更新为实际可用的地址）
    sources = [
        ("工大要闻", "https://news.hfut.edu.cn/gdyw1.htm"),
        ("综合新闻", "https://news.hfut.edu.cn/zhxw1.htm"),
        ("通知公告", "https://news.hfut.edu.cn/tzgg2.htm"),
        ("宣城校区", "http://xc.hfut.edu.cn/1954/list.htm"),
    ]

    results = []
    seen_titles = set()

    for source_name, list_url in sources:
        # 判断是否为宣城校区（需要特殊处理 URL 拼接）
        is_xc = 'xc.hfut.edu.cn' in list_url

        try:
            resp = _req.get(list_url, timeout=12, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            })
            if resp.status_code != 200:
                logger.debug("      [降级] %s HTTP %d", source_name, resp.status_code)
                continue

            from lxml import html as _lxml_html
            tree = _lxml_html.fromstring(resp.text)

            # 合工大本部新闻站结构（gdyw1.htm/zhxw1.htm/tzgg2.htm）：
            #   <li><a href="info/XXX.htm" title="新闻标题"><div class="time">...<div class="text"><h5>标题</h5></div></a></li>
            #   关键：a.title 属性有完整标题，a.text_content() 会混杂日期前缀
            # 宣城校区结构：
            #   <a href="/a2/xx/c1954aXXXXX/page.htm">新闻标题</a>
            items = tree.xpath(
                "//li//a[contains(@href, 'info/') or (contains(@href, '/a') and contains(@href, 'page.htm'))] "
                "| //div[contains(@class,'list')]//li//a"
            )

            count = 0
            for item in items:
                # 优先使用 title 属性（合工大本部 a 标签的 title 有完整干净标题）
                title = item.get('title', '').strip()
                if not title:
                    # 降级：用 text_content()（宣城校区等无 title 属性的情况）
                    title = item.text_content().strip()
                # 清理空白字符
                title = ' '.join(title.split())
                href = item.get('href', '')

                if not title or len(title) < 6 or title in seen_titles:
                    continue
                # 跳过导航链接、分页和无关内容
                skip_words = ('MORE', 'INDEX', '', '返回首页', '尾页', '下一页', '上一页',
                              '跳转到', '旧版')
                if title.upper() in skip_words:
                    continue
                if not href:
                    continue
                # 跳过列表页/栏目页（非文章详情页）
                if '/list' in href or href.endswith('/main.htm'):
                    continue
                # 拼接完整 URL（相对路径 → 绝对 URL）
                if not href.startswith('http'):
                    if is_xc:
                        href = ('http://xc.hfut.edu.cn' + href) if href.startswith('/') else ('http://xc.hfut.edu.cn/' + href)
                    elif href.startswith('/'):
                        href = 'https://news.hfut.edu.cn' + href
                    else:
                        href = 'https://news.hfut.edu.cn/' + href
                # 只保留 hfut.edu.cn 域名下的链接（拼接后检查）
                if 'hfut.edu.cn' not in href:
                    continue

                seen_titles.add(title)
                results.append({
                    "source": "wechat_account_fallback",
                    "account_name": f"合工大官网({source_name})",
                    "account_id": "",
                    "title": title,
                    "content": title,
                    "time": datetime.now().strftime("%Y-%m-%d"),
                    "url": href,
                    "priority": "高",
                    "publish_raw": "",
                    "_fallback": True,
                })
                count += 1
                if count >= max_articles:
                    break

            logger.info("      [降级] 从 %s 获取 %d 条", source_name, count)

        except _req.exceptions.ConnectTimeout:
            logger.warning("      [降级] %s 连接超时（网络不可达），跳过", source_name)
        except _req.exceptions.ConnectionError:
            logger.warning("      [降级] %s 连接失败（网络不可达），跳过", source_name)
        except Exception as e:
            logger.debug("      [降级] %s 获取失败: %s", source_name, e)
            continue

    return results


def _parse_publish_time(raw: str) -> str:
    """
    解析发布时间（与 wechat_monitor_enhanced.py 相同逻辑）
    """
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")
    
    import re
    
    # 提取纯数字（Unix 时间戳）
    numbers = re.findall(r'\d+', raw)
    if numbers:
        ts = int(numbers[0])
        if ts > 1000000000:  # 时间戳
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d")
    
    # 包含中文日期
    cn_date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', raw)
    if cn_date_match:
        y, m, d = cn_date_match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    
    # 相对时间处理
    if "天前" in raw:
        days = int(re.search(r'(\d+)天前', raw).group(1)) if re.search(r'(\d+)天前', raw) else 0
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if "小时前" in raw:
        hours = int(re.search(r'(\d+)小时前', raw).group(1)) if re.search(r'(\d+)小时前', raw) else 0
        return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")
    
    # 默认返回今天
    return datetime.now().strftime("%Y-%m-%d")


def monitor_wechat_accounts(accounts: List[Dict] = None, 
                             max_articles: int = MAX_ARTICLES_PER_ACCOUNT,
                             max_days: int = MAX_DAYS_AGO) -> List[Dict]:
    """
    监控指定的微信公众号，获取最新文章
    
    Args:
        accounts: 公众号列表，默认使用配置中的四个公众号
        max_articles: 每个公众号最多获取的文章数
        max_days: 只获取最近N天的文章
        
    Returns:
        list: 文章列表
    """
    if accounts is None:
        accounts = _load_config()
    
    search_fn, content_fn = _get_search_fn()
    if not search_fn:
        logger.error("❌ weixin_search_mcp 不可用，无法监控公众号")
        return []
    
    logger.info("📱 开始监控 %d 个微信公众号...", len(accounts))
    logger.info("   每个公众号最多 %d 篇文章，最近 %d 天", max_articles, max_days)

    results = []
    seen_urls = set()  # 去重
    sogou_empty_count = 0  # 统计搜狗搜索空结果次数

    for i, account in enumerate(accounts, 1):
        account_name = account.get('name', '未知公众号')
        account_id = account.get('account_id', '')
        priority = account.get('priority', '中')

        logger.info("   [%d/%d] 监控公众号: %s (优先级:%s)", i, len(accounts), account_name, priority)

        try:
            # 方法1：搜索特定公众号的文章（使用账号名称作为关键词）
            # 搜狗微信搜索支持搜索特定公众号
            search_keyword = f"{account_name}"

            # 搜索第1页
            items = search_fn(search_keyword, page=1)

            if not items:
                sogou_empty_count += 1
                logger.warning("      ⚠️ 搜狗未找到公众号 '%s' 的文章（可能触发反爬）", account_name)
                continue

            logger.info("      找到 %d 篇相关文章", len(items))
            
            # 过滤出属于该公众号的文章（通过account_name匹配）
            account_articles = []
            for item in items:
                item_account = item.get("account_name", "")
                # 模糊匹配公众号名称
                if account_name in item_account or item_account in account_name:
                    account_articles.append(item)
            
            if not account_articles:
                # 如果找不到完全匹配的，使用搜索结果的前几篇
                account_articles = items[:max_articles]
                logger.warning("      ⚠️ 未找到完全匹配的文章，使用搜索结果前 %d 篇", len(account_articles))
            
            # 处理每篇文章
            for j, item in enumerate(account_articles[:max_articles], 1):
                url = item.get("real_url", "")
                link = item.get("link", "")
                title = item.get("title", "")
                
                # 优先用 real_url，为空时降级用 link
                if not url or url == "https://mp.":
                    if link and link.startswith("http"):
                        url = link
                    else:
                        continue
                
                if url in seen_urls:
                    continue
                
                seen_urls.add(url)
                
                # 提取文章内容（摘要模式，只取前500字）
                content = ""
                try:
                    if content_fn:
                        full_content = content_fn(url, referer=item.get("link", ""))
                        content = full_content[:500] + ("..." if len(full_content) > 500 else "")
                except Exception as e:
                    logger.debug("      内容提取失败: %s", e)
                    content = title  # 降级为标题
                
                # 解析时间
                publish_time = item.get("publish_time", "")
                parsed_time = _parse_publish_time(publish_time)
                
                # 检查是否在时间范围内
                try:
                    article_date = datetime.strptime(parsed_time, "%Y-%m-%d")
                    if (datetime.now() - article_date).days > max_days:
                        logger.debug("      跳过旧文章: %s", parsed_time)
                        continue
                except:
                    pass
                
                results.append({
                    "source": "wechat_account",
                    "account_name": account_name,
                    "account_id": account_id,
                    "title": title,
                    "content": content,
                    "time": parsed_time,
                    "url": url,
                    "priority": priority,
                    "publish_raw": publish_time,
                })
                
                logger.info("      [%d] %s... (%s)", j, title[:40], parsed_time)
            
            # 友好爬虫，避免被封
            time.sleep(2)

        except Exception as e:
            logger.error("   ❌ 监控公众号 '%s' 失败: %s", account_name, e)
            continue

    # ===== 降级方案：当搜狗搜索全部返回空结果时，切换到官网抓取 =====
    if sogou_empty_count >= len(accounts) and not results:
        logger.warning("⚠️ 全部 %d 个公众号搜狗搜索均无结果（疑似反爬触发），启动官网降级方案...", sogou_empty_count)
        try:
            fallback_results = _fetch_hfut_official_news(max_articles=max_articles)
            for item in fallback_results:
                if item.get('url') and item['url'] not in seen_urls:
                    seen_urls.add(item['url'])
                    results.append(item)
            if results:
                logger.info("✅ 降级方案成功，从合工大官网获取 %d 条新闻", len(results))
            else:
                logger.warning("⚠️ 降级方案也未获取到数据")
        except Exception as fallback_err:
            logger.error("❌ 官网降级方案执行失败: %s", fallback_err)

    logger.info("✅ 微信公众号监控完成，共 %d 条（去重后）", len(results))
    return results


def update_account_config(new_accounts: List[Dict]) -> bool:
    """
    更新公众号配置（写入 config.json）
    
    Args:
        new_accounts: 新的公众号列表
        
    Returns:
        bool: 是否成功更新
    """
    try:
        config_path = Path(__file__).parent.parent / "config" / "config.json"
        
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}
        
        config["wechat_accounts"] = new_accounts
        
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ 已更新公众号配置：{len(new_accounts)} 个公众号")
        return True
    except Exception as e:
        logger.error(f"❌ 更新公众号配置失败: {e}")
        return False


def test_monitor():
    """测试函数"""
    print("=" * 60)
    print("测试微信公众号精准监控")
    print("=" * 60)
    
    # 测试监控（使用默认配置）
    accounts = _load_config()
    print(f"\n配置的公众号：")
    for i, acc in enumerate(accounts, 1):
        print(f"  {i}. {acc['name']} (优先级:{acc['priority']})")
    
    print(f"\n开始监控...")
    results = monitor_wechat_accounts(accounts, max_articles=5, max_days=7)
    
    print(f"\n✅ 监控完成，共获得 {len(results)} 篇文章：\n")
    
    for i, item in enumerate(results[:10], 1):
        print(f"[{i}] {item['title'][:45]}")
        print(f"    公众号: {item['account_name']} | 时间: {item['time']}")
        print(f"    优先级: {item['priority']}")
        print(f"    URL: {item['url'][:60]}...")
        preview = item.get('content', '')[:80].replace('\n', ' ')
        print(f"    摘要: {preview}...")
        print()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    test_monitor()
