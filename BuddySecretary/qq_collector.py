"""
超级秘书 - QQ群消息采集器
基于NapCat HTTP API实现群消息获取
"""

import json
import time
import sqlite3
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QQCollector")


class QQGroupCollector:
    """QQ群消息采集器"""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.napcat_config = self.config['napcat']
        self.groups = self.config['qq_groups']
        self.storage = self.config['storage']

        self.api_url = self.napcat_config['api_url']
        self.token = self.napcat_config['token']
        self.bot_qq = self.napcat_config['bot_qq']

        self.headers = {
            'Authorization': self.token
        }

        # 初始化数据库
        self._init_database()

    def _init_database(self):
        """初始化SQLite数据库"""
        db_path = self.storage['db_path']
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 群消息表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                group_name TEXT,
                sender_id TEXT,
                sender_name TEXT,
                content TEXT,
                message_id TEXT UNIQUE,
                timestamp INTEGER,
                message_type TEXT,
                raw_data TEXT,
                is_processed INTEGER DEFAULT 0,
                category TEXT,
                summary TEXT,
                importance TEXT,
                tags TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 群文件表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                file_id TEXT UNIQUE,
                file_name TEXT,
                file_size INTEGER,
                file_url TEXT,
                sender_id TEXT,
                sender_name TEXT,
                download_path TEXT,
                is_downloaded INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 扫描进度表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_progress (
                group_id TEXT PRIMARY KEY,
                last_msg_id TEXT,
                last_scan_time INTEGER,
                total_messages INTEGER DEFAULT 0
            )
        ''')

        # 索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_group_id ON group_messages(group_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON group_messages(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON group_messages(category)')

        conn.commit()
        conn.close()
        logger.info("数据库初始化完成")

    def check_connection(self) -> bool:
        """检查NapCat连接"""
        try:
            # 尝试获取登录信息
            response = requests.get(
                f"{self.api_url}/get_login_info",
                headers=self.headers,
                timeout=5
            )
            if response.status_code == 200:
                logger.info("NapCat连接正常")
                return True
            return False
        except Exception as e:
            logger.error(f"NapCat连接失败: {e}")
            return False

    def get_group_list(self) -> List[Dict]:
        """获取群列表"""
        try:
            response = requests.post(
                f"{self.api_url}/get_group_list",
                headers=self.headers,
                json={},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    return data.get('data', [])
            logger.warning(f"获取群列表失败: {response.text}")
            return []
        except Exception as e:
            logger.error(f"获取群列表异常: {e}")
            return []

    def get_group_info(self, group_id: str) -> Optional[Dict]:
        """获取群信息"""
        try:
            response = requests.post(
                f"{self.api_url}/get_group_info",
                headers=self.headers,
                json={"group_id": group_id},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    return data.get('data')
            return None
        except Exception as e:
            logger.error(f"获取群信息异常: {e}")
            return None

    def get_group_messages(self, group_id: str, num: int = 20,
                          message_id: int = None) -> List[Dict]:
        """
        获取群消息历史

        Args:
            group_id: 群号
            num: 消息数量（最大100）
            message_id: 起始消息ID（从该消息向前获取）
        """
        try:
            payload = {
                "group_id": group_id,
                "num": min(num, 100)
            }
            if message_id:
                payload["message_id"] = message_id

            response = requests.post(
                f"{self.api_url}/get_group_msg_history",
                headers=self.headers,
                json=payload,
                timeout=15
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    return data.get('data', {}).get('messages', [])
            logger.warning(f"获取群消息失败 [{group_id}]: {response.text[:100]}")
            return []
        except Exception as e:
            logger.error(f"获取群消息异常 [{group_id}]: {e}")
            return []

    def get_group_file_list(self, group_id: str) -> List[Dict]:
        """获取群文件列表"""
        try:
            response = requests.post(
                f"{self.api_url}/get_group_file_list",
                headers=self.headers,
                json={"group_id": group_id, "folder_id": ""},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    return data.get('data', {}).get('files', [])
            return []
        except Exception as e:
            logger.error(f"获取群文件列表异常 [{group_id}]: {e}")
            return []

    def save_message(self, group_id: str, message: Dict) -> bool:
        """保存单条消息到数据库"""
        try:
            conn = sqlite3.connect(self.storage['db_path'])
            cursor = conn.cursor()

            # 提取消息字段
            msg_id = str(message.get('msg_id', ''))
            sender = message.get('sender', {})
            content = self._extract_message_content(message)

            cursor.execute('''
                INSERT OR IGNORE INTO group_messages
                (group_id, sender_id, sender_name, content, message_id,
                 timestamp, message_type, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                group_id,
                sender.get('user_id', ''),
                sender.get('nickname', ''),
                content,
                msg_id,
                message.get('time', 0),
                message.get('message_type', ''),
                json.dumps(message, ensure_ascii=False)
            ))

            conn.commit()
            affected = cursor.rowcount
            conn.close()

            return affected > 0
        except Exception as e:
            logger.error(f"保存消息失败: {e}")
            return False

    def _extract_message_content(self, message: Dict) -> str:
        """提取消息文本内容"""
        content = message.get('message', [])

        if isinstance(content, str):
            return content

        text_parts = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get('type') == 'text':
                        text_parts.append(item.get('data', {}).get('text', ''))
                    elif item.get('type') == 'image':
                        text_parts.append('[图片]')
                    elif item.get('type') == 'file':
                        text_parts.append(f"[文件: {item.get('data', {}).get('name', '')}]")

        return ''.join(text_parts)

    def full_scan_group(self, group_id: str, batch_size: int = 100,
                        delay: float = 2.0) -> int:
        """
        全量扫描群消息

        Args:
            group_id: 群号
            batch_size: 每批获取消息数
            delay: 每批间隔秒数
        """
        logger.info(f"开始全量扫描群 {group_id}...")

        total_saved = 0
        last_msg_id = None
        empty_count = 0
        max_empty = 5  # 连续5次为空认为已到上限

        while empty_count < max_empty:
            messages = self.get_group_messages(group_id, batch_size, last_msg_id)

            if not messages:
                empty_count += 1
                logger.info(f"  [{group_id}] 第{empty_count}次空批次，继续...")
                time.sleep(delay)
                continue

            empty_count = 0
            saved_count = 0

            for msg in messages:
                if self.save_message(group_id, msg):
                    saved_count += 1
                    total_saved += 1
                last_msg_id = msg.get('msg_id')

            logger.info(f"  [{group_id}] 批次保存 {saved_count} 条，累计 {total_saved} 条")

            if len(messages) < batch_size:
                break

            time.sleep(delay)

        # 更新扫描进度
        self._update_scan_progress(group_id, last_msg_id, total_saved)

        logger.info(f"群 {group_id} 全量扫描完成，共保存 {total_saved} 条消息")
        return total_saved

    def incremental_scan_group(self, group_id: str, num: int = 20) -> int:
        """增量扫描，获取最新消息"""
        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        # 获取上次扫描的最新消息ID
        cursor.execute(
            'SELECT last_msg_id FROM scan_progress WHERE group_id = ?',
            (group_id,)
        )
        row = cursor.fetchone()
        last_msg_id = row[0] if row else None

        conn.close()

        # 获取最新消息
        messages = self.get_group_messages(group_id, num)

        saved_count = 0
        new_last_msg_id = last_msg_id

        for msg in reversed(messages):  # 从旧到新
            msg_id = str(msg.get('msg_id', ''))

            if last_msg_id and msg_id == str(last_msg_id):
                break

            if self.save_message(group_id, msg):
                saved_count += 1
                new_last_msg_id = msg.get('msg_id')

        # 更新进度
        if saved_count > 0:
            self._update_scan_progress(group_id, new_last_msg_id, saved_count)
            logger.info(f"群 {group_id} 增量扫描：新增 {saved_count} 条消息")

        return saved_count

    def _update_scan_progress(self, group_id: str, last_msg_id: str, total: int):
        """更新扫描进度"""
        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO scan_progress
            (group_id, last_msg_id, last_scan_time, total_messages)
            VALUES (?, ?, ?, ?)
        ''', (group_id, last_msg_id, int(time.time()), total))

        conn.commit()
        conn.close()

    def full_scan_all_groups(self, batch_size: int = 100,
                            delay: float = 2.0) -> Dict[str, int]:
        """全量扫描所有群"""
        results = {}

        for group_id in self.groups:
            logger.info(f"\n{'='*50}")
            logger.info(f"扫描群: {group_id}")

            # 获取群名称
            group_info = self.get_group_info(group_id)
            if group_info:
                logger.info(f"群名称: {group_info.get('group_name', '未知')}")

            count = self.full_scan_group(group_id, batch_size, delay)
            results[group_id] = count

            time.sleep(delay)  # 群间间隔

        return results

    def incremental_scan_all_groups(self, num: int = 20) -> Dict[str, int]:
        """增量扫描所有群"""
        results = {}

        for group_id in self.groups:
            count = self.incremental_scan_group(group_id, num)
            results[group_id] = count

        return results

    def get_group_file(self, group_id: str, file_id: str,
                      save_path: str) -> bool:
        """下载群文件"""
        try:
            response = requests.post(
                f"{self.api_url}/download_group_file",
                headers=self.headers,
                json={
                    "group_id": group_id,
                    "file_id": file_id,
                    "name": save_path
                },
                timeout=60
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"下载群文件失败: {e}")
            return False

    def scan_group_files(self, group_id: str) -> int:
        """扫描群文件"""
        files = self.get_group_file_list(group_id)
        saved_count = 0

        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        for file in files:
            cursor.execute('''
                INSERT OR IGNORE INTO group_files
                (group_id, file_id, file_name, file_size, file_url, sender_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                group_id,
                file.get('id', ''),
                file.get('name', ''),
                file.get('size', 0),
                file.get('url', ''),
                file.get('uploader_id', '')
            ))
            saved_count += 1

        conn.commit()
        conn.close()

        logger.info(f"群 {group_id} 文件扫描完成：{saved_count} 个文件")
        return saved_count

    def get_stats(self) -> Dict:
        """获取统计数据"""
        conn = sqlite3.connect(self.storage['db_path'])
        cursor = conn.cursor()

        stats = {}

        # 总消息数
        cursor.execute('SELECT COUNT(*) FROM group_messages')
        stats['total_messages'] = cursor.fetchone()[0]

        # 各群消息数
        cursor.execute('''
            SELECT group_id, COUNT(*) as cnt
            FROM group_messages
            GROUP BY group_id
            ORDER BY cnt DESC
        ''')
        stats['messages_by_group'] = dict(cursor.fetchall())

        # 已处理消息数
        cursor.execute('SELECT COUNT(*) FROM group_messages WHERE is_processed = 1')
        stats['processed_messages'] = cursor.fetchone()[0]

        # 文件数
        cursor.execute('SELECT COUNT(*) FROM group_files')
        stats['total_files'] = cursor.fetchone()[0]

        conn.close()

        return stats


def main():
    """测试运行"""
    import os

    config_path = os.path.join(
        os.path.dirname(__file__),
        'config.json'
    )

    collector = QQGroupCollector(config_path)

    # 检查连接
    if not collector.check_connection():
        print("NapCat未连接，请先启动NapCat")
        return

    # 获取群列表
    groups = collector.get_group_list()
    print(f"机器人所在的群数量: {len(groups)}")

    for g in groups[:5]:  # 只显示前5个
        print(f"  {g.get('group_id')}: {g.get('group_name')}")


if __name__ == '__main__':
    main()
