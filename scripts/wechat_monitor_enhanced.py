"""
微信公众号监控模块（增强版 v2）
基于 weixin_search_mcp 搜索引擎，获取合肥工业大学相关公众号最新文章

依赖：weixin_search_mcp（已安装在 ../weixin_search_mcp/）
     pip install -e ../weixin_search_mcp

数据输出格式（与现有系统兼容）：
{
    "source": "wechat_official",
    "title": "文章标题",
    "content": "正文摘要",
    "time": "2026-06-04",
    "url": "https://mp.weixin.qq.com/...",
    "account_name": "公众号名称"
}
"""

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("HFUT_Monitor")

# 搜索关键词列表（覆盖合工大全貌）
SEARCH_KEYWORDS = [
    "合肥工业大学",
    "合肥工业大学宣城校区",
    "HFUT",
    "合工大 教务",
    "合工大 通知",
    "合工大 招生",
    "合工大 考试",
    "合工大 奖学金",
    "合工大 就业",
    "合工大 学术",
]

# 最大搜索结果数（每个关键词）
MAX_RESULTS_PER_KEYWORD = 5


def _load_config():
    """加载配置"""
    config_path = Path(__file__).parent.parent / "config" / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_search_fn():
    """
    延迟导入 weixin_search_mcp 的搜索函数
    避免模块级 ImportError
    """
    try:
        # 尝试从相邻目录导入
        weixin_mcp_dir = Path(__file__).parent.parent.parent / "weixin_search_mcp"
        if str(weixin_mcp_dir) not in sys.path:
            sys.path.insert(0, str(weixin_mcp_dir))
        
        from weixin_search_mcp.tools.weixin_search import (
            sogou_weixin_search,
            get_article_content,
        )
        return sogou_weixin_search, get_article_content
    except ImportError as e:
        logger.warning(f"⚠️  weixin_search_mcp 未安装: {e}")
        logger.warning("   安装方式: cd D:/项目整理/01-代码项目 && git clone https://github.com/fancyboi999/weixin_search_mcp.git")
        logger.warning("          cd weixin_search_mcp && pip install -e .")
        return None, None


def monitor_wechat_official(keywords=None, headless=True) -> list:
    """
    监控微信公众号最新文章
    
    Args:
        keywords: 搜索关键词列表，默认使用内置关键词
        headless: 兼容参数
        
    Returns:
        list: 文章列表
    """
    if keywords is None:
        config = _load_config()
        keywords = config.get("wechat_keywords", SEARCH_KEYWORDS)
    
    search_fn, content_fn = _get_search_fn()
    if not search_fn:
        return []
    
    logger.info(f"📱 微信公众号监控开始，{len(keywords)} 个关键词")
    results = []
    seen_urls = set()  # 去重
    
    for i, keyword in enumerate(keywords):
        try:
            logger.info(f"   [{i+1}/{len(keywords)}] 搜索: {keyword}")
            
            # 搜索第1页
            items = search_fn(keyword, page=1)
            
            for item in items[:MAX_RESULTS_PER_KEYWORD]:
                url = item.get("real_url", "")
                link = item.get("link", "")
                title = item.get("title", "")

                # 优先用 real_url，为空时降级用 link（搜狗跳转页）
                if not url or url == "https://mp.":
                    if link and link.startswith("http"):
                        url = link  # 降级：使用搜狗链接
                    else:
                        continue

                if url in seen_urls:
                    continue

                seen_urls.add(url)
                
                # 提取文章内容（摘要模式，只取前500字）
                content = ""
                try:
                    full_content = content_fn(url, referer=item.get("link", ""))
                    content = full_content[:500] + ("..." if len(full_content) > 500 else "")
                except Exception as e:
                    logger.debug(f"      内容提取失败: {e}")
                    content = title  # 降级为标题
                
                # 解析时间（搜狗返回的是 JS 时间戳格式）
                publish_time = item.get("publish_time", "")
                parsed_time = _parse_publish_time(publish_time)
                
                results.append({
                    "source": "wechat_official",
                    "title": title,
                    "content": content,
                    "time": parsed_time,
                    "url": url,
                    "account_name": keyword,
                    "publish_raw": publish_time,
                })
            
            time.sleep(1)  # 友好爬虫
            
        except Exception as e:
            logger.debug(f"   关键词 '{keyword}' 搜索失败: {e}")
            continue
    
    logger.info(f"✅ 微信公众号监控完成，共 {len(results)} 条（去重后）")
    return results


def _parse_publish_time(raw: str) -> str:
    """
    解析发布时间
    搜狗可能返回：
    - document.write(timeConvert('1665110771')) → Unix timestamp
    - 2024年06月03日 → 中文日期
    - 2天前 / 3小时前 → 相对时间
    """
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")
    
    # 提取纯数字（Unix 时间戳）
    import re
    numbers = re.findall(r'\d+', raw)
    if numbers:
        ts = int(numbers[0])
        # 判断是时间戳还是日期数字
        if ts > 1000000000:  # 时间戳
            dt = datetime.fromtimestamp(ts)
            return dt.strftime("%Y-%m-%d")
        elif len(numbers) >= 3 and ts > 1900:  # 可能是日期 YYYYMMDD
            try:
                return f"{ts // 10000 % 10000}-{numbers[1] if len(numbers)>1 else '01'}-{numbers[2] if len(numbers)>2 else '01'}"
            except Exception:
                pass
    
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


def monitor_wechat_groups(group_ids=None) -> list:
    """
    监控微信群聊消息（需要 wechat-decrypt 实时监听）
    未来扩展方向
    """
    logger.info("💬 微信群监控（需要 wechat-decrypt MCP Server）")
    
    # 检查本地 wechat-decrypt 是否可用
    wechat_decrypt_dir = Path(__file__).parent.parent.parent / "wechat-decrypt"
    if wechat_decrypt_dir.exists():
        try:
            import requests
            resp = requests.get("http://localhost:5678", timeout=2)
            if resp.status_code == 200:
                logger.info("   ✅ wechat-decrypt monitor_web 运行中（端口 5678）")
                # TODO: 通过 SSE 接口获取实时消息
        except Exception:
            logger.debug("   wechat-decrypt 未运行")
    
    return []


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    print("=" * 50)
    print("测试微信公众号监控（weixin_search_mcp 方案）")
    print("=" * 50)
    data = monitor_wechat_official(keywords=["合肥工业大学", "合工大 通知"])
    print(f"\n获得 {len(data)} 条数据:\n")
    for i, item in enumerate(data[:10], 1):
        print(f"[{i}] {item['title'][:45]}")
        print(f"    来源: {item['account_name']} | 时间: {item['time']}")
        print(f"    URL: {item['url'][:60]}...")
        preview = item.get('content', '')[:80].replace('\n', ' ')
        print(f"    摘要: {preview}...")
        print()
