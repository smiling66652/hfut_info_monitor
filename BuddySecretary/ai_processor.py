"""
超级秘书 - AI信息处理模块
使用本地Ollama进行消息分类、摘要、重要性评估
"""

import json
import sqlite3
import requests
import time
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AIProcessor")


class OllamaProcessor:
    """基于Ollama的AI处理器"""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.ai_config = self.config['ai_processing']
        self.storage = self.config['storage']

        self.api_url = self.ai_config['api_url']
        self.model = self.ai_config['model']

        self.classify_prompt = self.ai_config['classify_prompt']
        self.summary_prompt = self.ai_config['summary_prompt']
        self.importance_prompt = self.ai_config['importance_prompt']

    def _call_llm(self, prompt: str, timeout: int = 30) -> Optional[str]:
        """
        调用Ollama LLM

        Args:
            prompt: 提示词
            timeout: 超时秒数

        Returns:
            LLM回复文本，失败返回None
        """
        try:
            response = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=timeout
            )

            if response.status_code == 200:
                data = response.json()
                return data.get('response', '').strip()
            return None
        except requests.exceptions.Timeout:
            logger.warning("LLM调用超时")
            return None
        except Exception as e:
            logger.error(f"LLM调用异常: {e}")
            return None

    def check_connection(self) -> bool:
        """检查Ollama连接"""
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                current_model = self.model.split(':')[0]
                for m in models:
                    if current_model in m.get('name', ''):
                        logger.info(f"Ollama连接正常，当前模型: {m.get('name')}")
                        return True
                logger.warning(f"当前模型 {self.model} 未找到")
                return True  # 仍视为连接正常
            return False
        except Exception as e:
            logger.error(f"Ollama连接失败: {e}")
            return False

    def classify(self, content: str) -> str:
        """
        消息分类

        Args:
            content: 消息内容

        Returns:
            分类名称
        """
        if not content or len(content.strip()) < 5:
            return "闲聊灌水"

        prompt = self.classify_prompt.format(content=content[:500])
        result = self._call_llm(prompt, timeout=15)

        if result:
            # 提取分类名称（去除引号等）
            result = result.strip().strip('"').strip("'")

            # 验证分类是否有效
            valid_categories = [
                "课程学习", "校园活动", "通知公告",
                "资源分享", "闲聊灌水", "问题求助"
            ]

            for cat in valid_categories:
                if cat in result:
                    return cat

            return result if result in valid_categories else "闲聊灌水"

        return "未分类"

    def summarize(self, content: str) -> str:
        """
        生成摘要

        Args:
            content: 消息内容

        Returns:
            摘要文本
        """
        if not content or len(content.strip()) < 20:
            return content[:100] if content else ""

        prompt = self.summary_prompt.format(content=content[:1000])
        result = self._call_llm(prompt, timeout=20)

        return result.strip() if result else content[:100]

    def assess_importance(self, content: str) -> str:
        """
        评估重要性

        Args:
            content: 消息内容

        Returns:
            重要性等级: 紧急/重要/一般
        """
        if not content:
            return "一般"

        prompt = self.importance_prompt.format(content=content[:500])
        result = self._call_llm(prompt, timeout=15)

        if result:
            result = result.strip().strip('"').strip("'")

            if "紧急" in result:
                return "紧急"
            elif "重要" in result:
                return "重要"

        return "一般"

    def extract_keywords(self, content: str) -> List[str]:
        """
        提取关键词

        Args:
            content: 消息内容

        Returns:
            关键词列表
        """
        keywords = []

        # 基于规则的关键词提取
        keyword_rules = {
            "截止日期": ["截止", "deadline", "DDL", "截止日期", "截至"],
            "考试": ["考试", "期中", "期末", "测验"],
            "作业": ["作业", "assignment", "due"],
            "课程": ["课程", "上课", "教室", "老师", "教授"],
            "活动": ["活动", "比赛", "讲座", "宣讲"],
            "通知": ["通知", "公告", "重要", "紧急"],
            "资料": ["资料", "课件", "PPT", "PDF", "文件"],
            "招募": ["招募", "报名", "招新", "纳新"]
        }

        content_lower = content.lower()
        for category, words in keyword_rules.items():
            for word in words:
                if word.lower() in content_lower:
                    if category not in keywords:
                        keywords.append(category)
                    break

        return keywords[:5]  # 最多5个标签


class MessageProcessor:
    """消息处理器 - 主控类"""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.storage = self.config['storage']
        self.ai_config = self.config['ai_processing']

        self.ollama = OllamaProcessor(config_path)

    def process_unprocessed_messages(self, limit: int = 50,
                                     delay: float = 1.0) -> int:
        """
        处理未处理的消息

        Args:
            limit: 每次处理数量
            delay: 处理间隔秒数

        Returns:
            处理的消息数量
        """
        if not self.ai_config.get('enable'):
            logger.info("AI处理未启用")
            return 0

        if not self.ollama.check_connection():
            logger.error("Ollama未连接，跳过AI处理")
            return 0

        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        # 获取未处理的消息
        cursor.execute('''
            SELECT id, group_id, content
            FROM group_messages
            WHERE is_processed = 0
            AND content IS NOT NULL
            AND length(content) > 3
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))

        rows = cursor.fetchall()
        processed_count = 0

        for row in rows:
            msg_id, group_id, content = row

            try:
                # AI处理
                category = self.ollama.classify(content)
                summary = self.ollama.summarize(content)
                importance = self.ollama.assess_importance(content)
                keywords = self.ollama.extract_keywords(content)

                # 更新数据库
                cursor.execute('''
                    UPDATE group_messages
                    SET is_processed = 1,
                        category = ?,
                        summary = ?,
                        importance = ?,
                        tags = ?
                    WHERE id = ?
                ''', (
                    category,
                    summary,
                    importance,
                    json.dumps(keywords, ensure_ascii=False),
                    msg_id
                ))

                processed_count += 1
                logger.info(f"处理消息 [{msg_id}] -> {category} | {importance}")

                time.sleep(delay)

            except Exception as e:
                logger.error(f"处理消息 {msg_id} 失败: {e}")
                # 标记为已处理，避免重复处理
                cursor.execute(
                    'UPDATE group_messages SET is_processed = 1 WHERE id = ?',
                    (msg_id,)
                )

        conn.commit()
        conn.close()

        logger.info(f"本次处理了 {processed_count} 条消息")
        return processed_count

    def process_all_pending(self, batch_size: int = 50,
                           delay: float = 1.0) -> int:
        """处理所有待处理消息"""
        total = 0

        while True:
            count = self.process_unprocessed_messages(batch_size, delay)
            total += count

            if count < batch_size:
                break

            logger.info(f"已处理 {total} 条，继续...")

        logger.info(f"全部处理完成，共 {total} 条消息")
        return total

    def generate_daily_summary(self) -> Dict:
        """生成每日摘要报告"""
        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        today = datetime.now().strftime('%Y-%m-%d')

        # 获取今日消息统计
        cursor.execute('''
            SELECT COUNT(*) FROM group_messages
            WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
        ''')
        today_count = cursor.fetchone()[0]

        # 按分类统计
        cursor.execute('''
            SELECT category, COUNT(*) as cnt
            FROM group_messages
            WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
            AND is_processed = 1
            GROUP BY category
            ORDER BY cnt DESC
        ''')
        category_stats = dict(cursor.fetchall())

        # 重要消息
        cursor.execute('''
            SELECT content, summary, group_id, timestamp
            FROM group_messages
            WHERE importance = '紧急'
            AND date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
            ORDER BY timestamp DESC
            LIMIT 10
        ''')
        urgent_messages = cursor.fetchall()

        # 重要消息
        cursor.execute('''
            SELECT content, summary, group_id, category, timestamp
            FROM group_messages
            WHERE importance = '重要'
            AND date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
            ORDER BY timestamp DESC
            LIMIT 20
        ''')
        important_messages = cursor.fetchall()

        conn.close()

        return {
            'date': today,
            'total_messages': today_count,
            'category_stats': category_stats,
            'urgent_messages': urgent_messages,
            'important_messages': important_messages
        }

    def search_messages(self, keyword: str = None,
                       category: str = None,
                       importance: str = None,
                       group_id: str = None,
                       limit: int = 50) -> List[Dict]:
        """搜索消息"""
        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        query = 'SELECT * FROM group_messages WHERE 1=1'
        params = []

        if keyword:
            query += ' AND content LIKE ?'
            params.append(f'%{keyword}%')

        if category:
            query += ' AND category = ?'
            params.append(category)

        if importance:
            query += ' AND importance = ?'
            params.append(importance)

        if group_id:
            query += ' AND group_id = ?'
            params.append(group_id)

        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)

        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        results = [dict(zip(columns, row)) for row in rows]
        conn.close()

        return results


def main():
    """测试运行"""
    import os

    config_path = os.path.join(
        os.path.dirname(__file__),
        'config.json'
    )

    processor = MessageProcessor(config_path)

    # 检查Ollama连接
    if not processor.ollama.check_connection():
        print("Ollama未运行，请先启动Ollama")
        print("在Y7000P上运行: ollama serve")
        return

    # 测试分类
    test_content = "明天操作系统考试，地点在教学楼A301，记得带学生证"
    category = processor.ollama.classify(test_content)
    print(f"分类测试: {test_content[:30]}... -> {category}")

    # 测试摘要
    summary = processor.ollama.summarize(test_content)
    print(f"摘要测试: {summary}")

    # 测试重要性
    importance = processor.ollama.assess_importance(test_content)
    print(f"重要性: {importance}")


if __name__ == '__main__':
    main()
