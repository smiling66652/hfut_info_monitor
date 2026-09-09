"""
新版每日摘要生成器 - DailyDigestV2
===================================

在 smart_digest.py 基础上增强，支持：

    - 按类别分组：🏆 创业比赛 | 🛒 二手好物 | 📢 其他通知
    - 每个类别最多展示 TOP 5（按重要性排序）
    - 二手物品按价值排序（高价值优先）
    - 输出格式：Markdown + 纯文本（用于邮件推送）
    - 支持生成含 emoji 的 QQ 邮箱友好格式

使用示例:
    from scripts.daily_digest_v2 import DailyDigestV2

    digest = DailyDigestV2()
    report = digest.generate(messages)
    digest.send(report)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("HFUT_Monitor.DailyDigestV2")


# ==================== 常量 ====================

CATEGORY_ICONS = {
    "competition": "🏆",
    "secondhand": "🛒",
    "useful_info": "📢",
}

CATEGORY_NAMES = {
    "competition": "创业比赛",
    "secondhand": "二手好物",
    "useful_info": "其他通知",
}

PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _load_digest_config() -> dict:
    """从 config.json 加载每日摘要配置"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("daily_digest_v2", {})
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("无法加载 config.json daily_digest_v2 配置，使用默认值")
        return {}


# ==================== DailyDigestV2 ====================

class DailyDigestV2:
    """新版每日摘要生成器

    从 EnhancedDetector 检测结果生成分类汇总报告，
    输出 Markdown + 纯文本双格式，支持邮件推送。

    Attributes:
        top_n_per_category: 每类别最多展示条数
        push_time: 推送时间（HH:MM）
        include_summary: 是否包含总览摘要
    """

    def __init__(
        self,
        top_n_per_category: Optional[int] = None,
        push_time: Optional[str] = None,
        include_summary: Optional[bool] = None,
    ):
        """初始化摘要生成器

        Args:
            top_n_per_category: 每类别 TOP N，默认从 config 读取
            push_time: 推送时间，默认从 config 读取
            include_summary: 是否包含摘要统计
        """
        cfg = _load_digest_config()

        self.top_n_per_category = (
            top_n_per_category
            or cfg.get("top_n_per_category", 5)
        )
        self.push_time = push_time or cfg.get("push_time", "20:00")
        self.include_summary = (
            include_summary
            if include_summary is not None
            else cfg.get("include_summary", True)
        )

        logger.info(
            "DailyDigestV2 初始化完成 (top_n=%d, push_time=%s)",
            self.top_n_per_category,
            self.push_time,
        )

    # ---------- 公开方法 ----------

    def generate(
        self,
        categorized_results: dict[str, list[dict]],
        date: Optional[str] = None,
    ) -> dict:
        """生成分类汇总报告

        Args:
            categorized_results: EnhancedDetector.detect_and_categorize() 的输出
                格式: {"competition": [...], "secondhand": [...], "useful_info": [...]}
            date: 日期字符串，默认今天

        Returns:
            报告 dict:
                {
                    "date": "2026-01-01",
                    "total_count": 15,
                    "categories": {
                        "competition": {"count": 5, "top": [...]},
                        "secondhand": {"count": 7, "top": [...]},
                        "useful_info": {"count": 3, "top": [...]},
                    },
                    "markdown": "Markdown 格式全文",
                    "plain_text": "纯文本格式全文",
                    "qq_email_html": "QQ 邮箱友好 HTML",
                }
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        logger.info("生成每日摘要: date=%s", date)

        total_count = sum(len(v) for v in categorized_results.values())

        # 各分类排序 + TOP N
        categories_report: dict[str, dict] = {}
        for cat, items in categorized_results.items():
            sorted_items = self._sort_category(cat, items)
            top_items = sorted_items[: self.top_n_per_category]
            categories_report[cat] = {
                "count": len(items),
                "top": top_items,
            }

        # 生成三种格式
        markdown = self._render_markdown(date, categories_report, total_count)
        plain_text = self._render_plain(date, categories_report, total_count)
        qq_email_html = self._render_qq_email_html(date, categories_report, total_count)

        report = {
            "date": date,
            "total_count": total_count,
            "categories": categories_report,
            "markdown": markdown,
            "plain_text": plain_text,
            "qq_email_html": qq_email_html,
        }

        logger.info(
            "✅ 摘要生成完成: 共 %d 条（competition=%d, secondhand=%d, useful_info=%d）",
            total_count,
            categories_report.get("competition", {}).get("count", 0),
            categories_report.get("secondhand", {}).get("count", 0),
            categories_report.get("useful_info", {}).get("count", 0),
        )

        return report

    def generate_from_messages(
        self,
        messages: list[dict],
        date: Optional[str] = None,
    ) -> dict:
        """从原始消息列表生成摘要（内部调用 EnhancedDetector）

        Args:
            messages: 消息列表
            date: 日期字符串

        Returns:
            同 generate() 的报告 dict
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            from enhanced_detector import EnhancedDetector

            detector = EnhancedDetector()
            categorized = detector.detect_and_categorize(messages)
        except ImportError as e:
            logger.error("无法导入 EnhancedDetector: %s", e)
            categorized = {"competition": [], "secondhand": [], "useful_info": []}

        return self.generate(categorized, date)

    def send(
        self,
        report: dict,
        use_qq: bool = True,
        use_email: bool = True,
    ) -> bool:
        """发送每日摘要

        Args:
            report: generate() 生成的报告
            use_qq: 是否通过 QQ 发送
            use_email: 是否通过邮件发送

        Returns:
            是否至少有一个渠道发送成功
        """
        success = False

        date_str = report.get("date", datetime.now().strftime("%Y-%m-%d"))
        total_count = report.get("total_count", 0)

        if total_count == 0:
            logger.info("今日无有价值信息，不发送摘要")
            return True

        # QQ 推送（简洁版）
        if use_qq:
            try:
                from push import send_qq_message as _send_qq

                qq_msg = self._render_qq_message(report)
                result = _send_qq(message=qq_msg)
                if result.get("status") == "ok":
                    logger.info("[QQ] 每日摘要发送成功")
                    success = True
                else:
                    logger.warning("[QQ] 每日摘要发送失败: %s", result.get("message"))
            except ImportError:
                logger.warning("push 模块不可用，跳过 QQ 推送")
            except Exception as e:
                logger.error("[QQ] 每日摘要发送异常: %s", e)

        # 邮件推送（完整版）
        if use_email:
            try:
                from push import send_email as _send_email

                subject = f"合工大每日巡检报告 - {date_str}"
                # 使用 QQ 邮箱 HTML 格式
                content = report.get("qq_email_html", report.get("plain_text", ""))
                is_html = bool(report.get("qq_email_html"))

                # send_email 只支持纯文本，这里用 plain_text
                plain_content = report.get("plain_text", "")
                email_ok = _send_email(subject, plain_content)
                if email_ok:
                    logger.info("[Email] 每日摘要发送成功")
                    success = True
                else:
                    logger.warning("[Email] 每日摘要发送失败")
            except ImportError:
                logger.warning("push 模块不可用，跳过邮件推送")
            except Exception as e:
                logger.error("[Email] 每日摘要发送异常: %s", e)

        return success

    # ---------- 排序逻辑 ----------

    def _sort_category(
        self, category: str, items: list[dict]
    ) -> list[dict]:
        """对某类别内的条目排序

        Args:
            category: 类别名
            items: 条目列表

        Returns:
            排序后的列表
        """
        if category == "secondhand":
            # 二手物品：高价值优先 → 价格 → 置信度
            return sorted(
                items,
                key=lambda r: (
                    0 if r.get("is_high_value") else 1,
                    -(r.get("max_price", 0) or 0),
                    -r.get("confidence", 0),
                ),
            )
        else:
            # 默认：优先级 → 置信度
            return sorted(
                items,
                key=lambda r: (
                    PRIORITY_ORDER.get(r.get("priority", "LOW"), 3),
                    -r.get("confidence", 0),
                ),
            )

    # ---------- 渲染方法 ----------

    def _render_markdown(
        self,
        date: str,
        categories: dict,
        total_count: int,
    ) -> str:
        """渲染 Markdown 格式"""
        lines: list[str] = []

        lines.append(f"# 📋 合工大每日巡检报告")
        lines.append(f"")
        lines.append(f"**日期**: {date}  |  **总计**: {total_count} 条有价值信息")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        for cat in ["competition", "secondhand", "useful_info"]:
            cat_data = categories.get(cat, {"count": 0, "top": []})
            icon = CATEGORY_ICONS.get(cat, "📌")
            name = CATEGORY_NAMES.get(cat, cat)
            count = cat_data["count"]
            top = cat_data["top"]

            lines.append(f"## {icon} {name}（{count} 条）")
            lines.append(f"")

            if not top:
                lines.append(f"_今日暂无_{name}_相关信息_")
                lines.append(f"")
                continue

            for i, item in enumerate(top, 1):
                msg = item.get("message", {})
                title = msg.get("content", "")[:60].replace("\n", " ")
                if not title:
                    title = "(无标题)"

                priority_badge = item.get("priority", "LOW")
                confidence = item.get("confidence", 0)

                lines.append(f"### {i}. {title}")
                lines.append(f"")
                lines.append(f"- **优先级**: `{priority_badge}` | **置信度**: {confidence:.0%}")
                lines.append(f"- **关键词**: {', '.join(item.get('matched_keywords', [])[:5])}")

                if cat == "competition":
                    deadline = item.get("deadline")
                    if deadline:
                        lines.append(f"- **⏰ 截止时间**: {deadline}")
                    registration = item.get("registration")
                    if registration:
                        lines.append(f"- **📝 报名方式**: {registration[:100]}")

                if cat == "secondhand":
                    prices = item.get("prices", [])
                    if prices:
                        lines.append(f"- **💰 价格**: {', '.join(prices)}")
                    item_category = item.get("item_category", "其他")
                    lines.append(f"- **📦 分类**: {item_category}")
                    if item.get("is_high_value"):
                        hv_items = item.get("high_value_items", [])
                        lines.append(f"- **⭐ 高价值物品**: {', '.join(hv_items)}")

                if cat == "useful_info":
                    subcategory = item.get("subcategory", "")
                    if subcategory:
                        lines.append(f"- **🏷️ 子类别**: {subcategory}")

                lines.append(f"")

            lines.append(f"---")
            lines.append(f"")

        if self.include_summary:
            lines.append(f"## 📊 统计概览")
            lines.append(f"")
            total = sum(c["count"] for c in categories.values())
            for cat in ["competition", "secondhand", "useful_info"]:
                cat_data = categories.get(cat, {"count": 0})
                icon = CATEGORY_ICONS.get(cat, "📌")
                name = CATEGORY_NAMES.get(cat, cat)
                bar = "█" * min(cat_data["count"], 20)
                lines.append(f"- {icon} {name}: **{cat_data['count']}** 条 {bar}")
            lines.append(f"")

        lines.append(f"---")
        lines.append(f"")
        lines.append(f"*此报告由 HFUT Daily Inspector 自动生成*")

        return "\n".join(lines)

    def _render_plain(
        self,
        date: str,
        categories: dict,
        total_count: int,
    ) -> str:
        """渲染纯文本格式（用于邮件正文）"""
        lines: list[str] = []

        lines.append("=" * 60)
        lines.append("合工大每日巡检报告")
        lines.append("=" * 60)
        lines.append(f"日期: {date}")
        lines.append(f"总计: {total_count} 条有价值信息")
        lines.append("")

        for cat in ["competition", "secondhand", "useful_info"]:
            cat_data = categories.get(cat, {"count": 0, "top": []})
            icon = CATEGORY_ICONS.get(cat, "?")
            name = CATEGORY_NAMES.get(cat, cat)
            count = cat_data["count"]
            top = cat_data["top"]

            lines.append("-" * 40)
            lines.append(f"{icon} {name}（{count} 条）")
            lines.append("-" * 40)

            if not top:
                lines.append(f"  今日暂无{name}相关信息")
                lines.append("")
                continue

            for i, item in enumerate(top, 1):
                msg = item.get("message", {})
                text = msg.get("content", "")[:80].replace("\n", " ")
                if not text:
                    text = "(无标题)"

                priority = item.get("priority", "LOW")
                confidence = item.get("confidence", 0)

                lines.append(f"  [{i}] {text}")
                lines.append(f"      优先级: {priority} | 置信度: {confidence:.0%}")

                if cat == "competition" and item.get("deadline"):
                    lines.append(f"      截止时间: {item['deadline']}")
                if cat == "secondhand" and item.get("prices"):
                    lines.append(f"      价格: {', '.join(item['prices'])}")
                    if item.get("is_high_value"):
                        lines.append(f"      ⭐高价值: {', '.join(item.get('high_value_items', []))}")

                lines.append("")

        lines.append("=" * 60)
        lines.append("此报告由 HFUT Daily Inspector 自动生成")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _render_qq_email_html(
        self,
        date: str,
        categories: dict,
        total_count: int,
    ) -> str:
        """渲染 QQ 邮箱友好 HTML 格式（含 emoji）"""
        parts: list[str] = []

        parts.append("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; color: #333;
       max-width: 680px; margin: 0 auto; padding: 20px; background: #f5f7fa; }
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff;
          padding: 24px; border-radius: 12px; text-align: center; margin-bottom: 20px; }
.header h1 { margin: 0; font-size: 22px; }
.header .meta { margin-top: 8px; opacity: 0.9; font-size: 14px; }
.category-section { background: #fff; border-radius: 12px; padding: 20px;
                    margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
.category-header { font-size: 18px; font-weight: bold; margin-bottom: 12px;
                   padding-bottom: 8px; border-bottom: 2px solid #eee; }
.category-header .count { font-size: 13px; color: #999; font-weight: normal; }
.item { padding: 12px 0; border-bottom: 1px solid #f0f0f0; }
.item:last-child { border-bottom: none; }
.item-title { font-size: 15px; font-weight: 500; color: #222; margin-bottom: 4px; }
.item-meta { font-size: 12px; color: #888; }
.item-meta span { margin-right: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
         font-weight: 600; }
.badge-high { background: #ffe0e0; color: #d32f2f; }
.badge-medium { background: #fff3e0; color: #f57c00; }
.badge-low { background: #e8f5e9; color: #388e3c; }
.deadline { color: #d32f2f; font-weight: 600; }
.price { color: #e65100; font-weight: 600; }
.high-value { color: #d32f2f; }
.empty-hint { color: #aaa; font-style: italic; padding: 8px 0; }
.summary { background: #fff; border-radius: 12px; padding: 20px;
           box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 16px; }
.summary h2 { font-size: 16px; margin: 0 0 12px 0; }
.summary-row { display: flex; align-items: center; margin-bottom: 8px; font-size: 14px; }
.summary-icon { width: 32px; font-size: 18px; }
.summary-bar { flex: 1; height: 16px; background: #eee; border-radius: 8px;
               margin: 0 12px; overflow: hidden; }
.summary-bar-fill { height: 100%; border-radius: 8px; }
.summary-count { font-weight: bold; min-width: 40px; text-align: right; }
.footer { text-align: center; color: #aaa; font-size: 12px; padding: 16px; }
.empty-section { padding: 40px; text-align: center; color: #aaa; }
</style>
</head>
<body>
""")

        # Header
        parts.append('<div class="header">')
        parts.append('<h1>📋 合工大每日巡检报告</h1>')
        parts.append(f'<div class="meta">📅 {date} &nbsp;|&nbsp; 📊 共 {total_count} 条有价值信息</div>')
        parts.append('</div>')

        # 统计概览
        if self.include_summary:
            bar_colors = {
                "competition": "#667eea",
                "secondhand": "#f57c00",
                "useful_info": "#43a047",
            }
            parts.append('<div class="summary">')
            parts.append('<h2>📊 统计概览</h2>')
            for cat in ["competition", "secondhand", "useful_info"]:
                cat_data = categories.get(cat, {"count": 0})
                icon = CATEGORY_ICONS.get(cat, "📌")
                name = CATEGORY_NAMES.get(cat, cat)
                count = cat_data["count"]
                pct = min(100, (count / max(total_count, 1)) * 100)
                color = bar_colors.get(cat, "#999")
                parts.append('<div class="summary-row">')
                parts.append(f'<span class="summary-icon">{icon}</span>')
                parts.append(f'<span style="width:70px">{name}</span>')
                parts.append('<div class="summary-bar">')
                parts.append(
                    f'<div class="summary-bar-fill" style="width:{pct}%;'
                    f'background:{color}"></div>'
                )
                parts.append('</div>')
                parts.append(f'<span class="summary-count">{count} 条</span>')
                parts.append('</div>')
            parts.append('</div>')

        # 分类详情
        for cat in ["competition", "secondhand", "useful_info"]:
            cat_data = categories.get(cat, {"count": 0, "top": []})
            icon = CATEGORY_ICONS.get(cat, "📌")
            name = CATEGORY_NAMES.get(cat, cat)
            count = cat_data["count"]
            top = cat_data["top"]

            parts.append('<div class="category-section">')
            parts.append(
                f'<div class="category-header">'
                f'{icon} {name} '
                f'<span class="count">共 {count} 条</span>'
                f'</div>'
            )

            if not top:
                parts.append(f'<div class="empty-hint">📭 今日暂无{name}相关信息</div>')
                parts.append('</div>')
                continue

            for item in top:
                msg = item.get("message", {})
                text = (msg.get("content", "") or "")[:100].replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
                if not text:
                    text = "(无标题)"

                priority = item.get("priority", "LOW")
                badge_class = {
                    "HIGH": "badge-high",
                    "MEDIUM": "badge-medium",
                    "LOW": "badge-low",
                }.get(priority, "badge-low")

                parts.append('<div class="item">')
                parts.append(f'<div class="item-title">{text}</div>')
                parts.append('<div class="item-meta">')
                parts.append(
                    f'<span class="badge {badge_class}">{priority}</span>'
                )
                parts.append(
                    f'<span>置信度: {item.get("confidence", 0):.0%}</span>'
                )

                if cat == "competition" and item.get("deadline"):
                    parts.append(
                        f'<span class="deadline">⏰ 截止: {item["deadline"]}</span>'
                    )

                if cat == "secondhand":
                    prices = item.get("prices", [])
                    if prices:
                        parts.append(
                            f'<span class="price">💰 {", ".join(prices)}</span>'
                        )
                    if item.get("is_high_value"):
                        hv = ", ".join(item.get("high_value_items", []))
                        parts.append(
                            f'<span class="high-value">⭐ {hv}</span>'
                        )

                parts.append('</div>')
                parts.append('</div>')

            parts.append('</div>')

        # Footer
        parts.append('<div class="footer">')
        parts.append('此报告由 <b>HFUT Daily Inspector</b> 自动生成<br>')
        parts.append(f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        parts.append('</div>')

        parts.append('</body></html>')

        return "\n".join(parts)

    def _render_qq_message(self, report: dict) -> str:
        """渲染 QQ 消息简洁版"""
        date = report.get("date", datetime.now().strftime("%Y-%m-%d"))
        total = report.get("total_count", 0)
        categories = report.get("categories", {})

        lines = [f"📋 合工大每日巡检 - {date}", f"共 {total} 条有价值信息", ""]

        cat_short = [
            ("competition", "🏆 创业比赛"),
            ("secondhand", "🛒 二手好物"),
            ("useful_info", "📢 其他通知"),
        ]

        for cat, label in cat_short:
            cat_data = categories.get(cat, {"count": 0, "top": []})
            count = cat_data["count"]
            top = cat_data["top"]

            if count == 0:
                continue

            lines.append(f"{label}（{count} 条）")

            for i, item in enumerate(top[:3], 1):  # QQ 消息只显示 TOP 3
                msg = item.get("message", {})
                text = (msg.get("content", "") or "")[:50].replace("\n", " ")
                lines.append(f"  {i}. {text}")

            if count > 3:
                lines.append(f"  ... 还有 {count - 3} 条，请查看邮件")

            lines.append("")

        lines.append("---")
        lines.append("详情请查看邮件 📧")

        return "\n".join(lines)


# ==================== 便捷函数 ====================

def generate_daily_digest_v2(
    categorized_results: dict[str, list[dict]],
    date: Optional[str] = None,
) -> dict:
    """便捷函数：生成每日摘要

    Args:
        categorized_results: 分类检测结果
        date: 日期字符串

    Returns:
        报告 dict
    """
    digest = DailyDigestV2()
    return digest.generate(categorized_results, date)


# ==================== 自测 ====================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # 模拟分类结果
    mock_categorized = {
        "competition": [
            {
                "category": "competition",
                "priority": "HIGH",
                "confidence": 0.85,
                "matched_keywords": ["挑战杯", "报名", "竞赛"],
                "deadline": "2026年3月15日",
                "registration": "填写问卷 https://example.com",
                "requirements": "全校本科生",
                "message": {"content": "挑战杯报名通知，截止时间2026年3月15日，请有意向的同学报名"},
            },
            {
                "category": "competition",
                "priority": "HIGH",
                "confidence": 0.72,
                "matched_keywords": ["保研", "夏令营"],
                "deadline": "2026年6月1日",
                "message": {"content": "2026年保研夏令营报名通知"},
            },
        ],
        "secondhand": [
            {
                "category": "secondhand",
                "priority": "HIGH",
                "confidence": 0.90,
                "matched_keywords": ["出闲置", "几乎全新"],
                "prices": ["2500"],
                "items": ["iPad"],
                "item_category": "电子产品",
                "is_high_value": True,
                "high_value_items": ["iPad"],
                "max_price": 2500.0,
                "message": {"content": "出闲置 iPad Air 5，几乎全新，￥2500"},
            },
            {
                "category": "secondhand",
                "priority": "MEDIUM",
                "confidence": 0.70,
                "matched_keywords": ["出售", "教材"],
                "prices": ["20"],
                "items": ["教材"],
                "item_category": "书籍教材",
                "is_high_value": False,
                "max_price": 20.0,
                "message": {"content": "出售机械原理教材，20元"},
            },
        ],
        "useful_info": [
            {
                "category": "useful_info",
                "priority": "MEDIUM",
                "confidence": 0.50,
                "matched_keywords": ["讲座", "学术报告"],
                "subcategory": "学术讲座",
                "message": {"content": "【讲座通知】明晚7点人工智能学术报告"},
            },
        ],
    }

    digest = DailyDigestV2(top_n_per_category=5)
    report = digest.generate(mock_categorized)

    print("=" * 70)
    print("DailyDigestV2 自测")
    print("=" * 70)

    print("\n--- Markdown 格式 ---\n")
    print(report["markdown"][:2000])

    print("\n--- 纯文本格式 ---\n")
    print(report["plain_text"][:1500])

    print("\n--- QQ 消息格式 ---\n")
    print(digest._render_qq_message(report))
