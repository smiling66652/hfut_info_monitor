"""
Notion 数据库管理模块
功能：封装 Notion API，提供统一的页面增删改查接口

API 版本：2022-06-28
Base URL：https://api.notion.com/v1
认证方式：Bearer Token（环境变量 NOTION_API_KEY 或 config.json）
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

from config_loader import get_base_dir, load_config

logger = logging.getLogger('NotionManager')

# Notion API 配置
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
REQUEST_TIMEOUT = 15  # 秒


class NotionManager:
    """Notion API 管理器

    管理 Notion 数据库的页面创建、查询、更新和搜索操作。
    支持从环境变量和 config.json 读取配置（环境变量优先级更高）。
    """

    def __init__(self, api_key: str | None = None, database_id: str | None = None):
        """初始化 Notion 管理器

        Args:
            api_key: Notion API Key，省略则从环境变量/配置文件读取
            database_id: 目标数据库 ID，省略则从环境变量/配置文件读取
        """
        self.api_key = api_key or self._resolve_api_key()
        self.database_id = database_id or self._resolve_database_id()
        self.archive_db_id = self._resolve_archive_db_id()

        if not self.api_key:
            logger.warning("未设置 Notion API Key，Notion 功能不可用")
        if not self.database_id:
            logger.warning("未设置 Notion 数据库 ID，数据库操作不可用")

    # ── 配置解析 ──────────────────────────────────────────────

    @staticmethod
    def _resolve_api_key() -> str:
        """解析 API Key：环境变量 > config.json"""
        env_key = os.environ.get('NOTION_API_KEY', '')
        if env_key:
            return env_key

        try:
            config = load_config()
            notion_config = config.get('notion', {})
            return notion_config.get('api_key', '')
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return ''

    @staticmethod
    def _resolve_database_id() -> str:
        """解析数据库 ID：环境变量 > config.json"""
        env_id = os.environ.get('NOTION_DATABASE_ID', '')
        if env_id:
            return env_id

        try:
            config = load_config()
            notion_config = config.get('notion', {})
            return notion_config.get('database_id', '')
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return ''

    @staticmethod
    def _resolve_archive_db_id() -> str:
        """解析归档数据库 ID"""
        env_id = os.environ.get('NOTION_ARCHIVE_DB_ID', '')
        if env_id:
            return env_id

        try:
            config = load_config()
            notion_config = config.get('notion', {})
            return notion_config.get('archive_db_id', '')
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return ''

    # ── HTTP 基础方法 ─────────────────────────────────────────

    def _headers(self) -> dict:
        """构建通用请求头"""
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json',
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        body: dict | None = None,
    ) -> tuple[int, dict]:
        """发送 API 请求（带超时和异常保护）

        Args:
            method: HTTP 方法（GET/POST/PATCH）
            endpoint: API 端点路径（如 /pages）
            body: 请求体字典

        Returns:
            (status_code, response_json)
        """
        url = f'{NOTION_API_BASE}{endpoint}'

        try:
            if method == 'GET':
                resp = requests.get(
                    url, headers=self._headers(), timeout=REQUEST_TIMEOUT
                )
            elif method == 'POST':
                resp = requests.post(
                    url, headers=self._headers(), json=body or {},
                    timeout=REQUEST_TIMEOUT,
                )
            elif method == 'PATCH':
                resp = requests.patch(
                    url, headers=self._headers(), json=body or {},
                    timeout=REQUEST_TIMEOUT,
                )
            else:
                return 405, {'error': f'不支持的 HTTP 方法: {method}'}

            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = {'error': resp.text}

            return resp.status_code, data

        except requests.exceptions.Timeout:
            logger.error(f'Notion API 请求超时: {method} {endpoint}')
            return 408, {'error': '请求超时'}
        except requests.exceptions.ConnectionError as e:
            logger.error(f'Notion API 连接失败: {e}')
            return 503, {'error': f'连接失败: {str(e)}'}
        except requests.exceptions.RequestException as e:
            logger.error(f'Notion API 请求异常: {e}')
            return 500, {'error': f'请求异常: {str(e)}'}

    # ── 核心方法 ──────────────────────────────────────────────

    def health_check(self) -> tuple[bool, str]:
        """验证 Notion API 连接状态

        Returns:
            (是否正常, 状态描述)
        """
        if not self.api_key or not self.database_id:
            return False, 'API Key 或数据库 ID 未配置'

        status_code, data = self._request('GET', f'/databases/{self.database_id}')

        if status_code == 200:
            db_title = ''
            if isinstance(data, dict):
                title_list = data.get('title', [])
                if title_list and isinstance(title_list, list):
                    db_title = ''.join(
                        t.get('plain_text', '')
                        for t in title_list
                        if isinstance(t, dict)
                    )
            msg = f'连接正常 (数据库: {db_title or "未命名"})'
            logger.info(f'Notion 健康检查: {msg}')
            return True, msg
        elif status_code == 401:
            return False, 'API Key 无效'
        elif status_code == 404:
            return False, '数据库 ID 无效或不存在'
        else:
            error_msg = data.get('message', data.get('error', '未知错误'))
            return False, f'API 错误 [{status_code}]: {error_msg}'

    def add_page(self, properties: dict) -> str:
        """在数据库中创建新页面

        Args:
            properties: Notion 页面属性字典（遵循 Notion Properties 格式）

        Returns:
            page_id（新建页面 ID），失败返回空字符串

        示例 properties 格式：
            {
                "Name": {"title": [{"text": {"content": "页面标题"}}]},
                "来源": {"select": {"name": "QQ群"}},
                "日期": {"date": {"start": "2026-06-04"}},
                "内容": {"rich_text": [{"text": {"content": "正文"}}]},
                "链接": {"url": "https://example.com"},
                "分类": {"select": {"name": "通知"}},
            }
        """
        if not self.database_id:
            logger.error('未配置数据库 ID，无法创建页面')
            return ''

        body: dict = {
            'parent': {'database_id': self.database_id},
            'properties': properties,
        }

        status_code, data = self._request('POST', '/pages', body)

        if status_code == 200 and isinstance(data, dict):
            page_id = data.get('id', '')
            if page_id:
                logger.info(f'页面创建成功: {page_id}')
                return page_id

        error_msg = data.get('message', '未知错误') if isinstance(data, dict) else str(data)
        logger.error(f'页面创建失败 [{status_code}]: {error_msg}')
        return ''

    def search(self, query: str, limit: int = 20) -> list:
        """搜索 Notion 工作区中的页面和数据库

        Args:
            query: 搜索关键词
            limit: 最大返回数量

        Returns:
            搜索结果列表
        """
        body: dict = {
            'query': query,
            'page_size': min(limit, 100),
        }

        status_code, data = self._request('POST', '/search', body)

        if status_code == 200 and isinstance(data, dict):
            results = data.get('results', [])
            logger.info(f'搜索 "{query}"，返回 {len(results)} 条结果')
            return results

        error_msg = data.get('message', '未知错误') if isinstance(data, dict) else str(data)
        logger.error(f'搜索失败 [{status_code}]: {error_msg}')
        return []

    def query_database(self, filter_params: dict | None = None) -> list:
        """按条件查询数据库

        Args:
            filter_params: Notion 过滤器字典（遵循 Notion Filter 格式），
                          为 None 时不添加过滤条件

        Returns:
            匹配的页面列表

        示例 filter_params：
            {
                "property": "分类",
                "select": {"equals": "通知"}
            }
        """
        if not self.database_id:
            logger.error('未配置数据库 ID，无法查询')
            return []

        body: dict = {}
        if filter_params:
            body['filter'] = filter_params

        all_results: list = []
        has_more = True
        start_cursor = ''

        # 分页获取全部结果
        while has_more:
            if start_cursor:
                body['start_cursor'] = start_cursor

            status_code, data = self._request(
                'POST', f'/databases/{self.database_id}/query', body
            )

            if status_code == 200 and isinstance(data, dict):
                results = data.get('results', [])
                all_results.extend(results)
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor', '')
            else:
                error_msg = data.get('message', '未知错误') if isinstance(data, dict) else str(data)
                logger.error(f'数据库查询失败 [{status_code}]: {error_msg}')
                break

        logger.info(f'数据库查询返回 {len(all_results)} 条结果')
        return all_results

    def update_page(self, page_id: str, properties: dict) -> bool:
        """更新指定页面的属性

        Args:
            page_id: 目标页面 ID
            properties: 要更新的属性字典（遵循 Notion Properties 格式）

        Returns:
            是否更新成功
        """
        if not page_id:
            logger.error('page_id 为空，无法更新')
            return False

        body: dict = {'properties': properties}

        status_code, data = self._request('PATCH', f'/pages/{page_id}', body)

        if status_code == 200:
            logger.info(f'页面更新成功: {page_id}')
            return True

        error_msg = data.get('message', '未知错误') if isinstance(data, dict) else str(data)
        logger.error(f'页面更新失败 [{status_code}]: {error_msg}')
        return False

    # ── 信息存储 ──────────────────────────────────────────────

    def store_info(self, info: dict) -> str:
        """将标准格式的监控信息存入 Notion

        自动映射字段：
            title  → Name（标题）
            source → 来源（单选）
            time   → 日期（日期）
            content→ 内容（富文本）
            url    → 链接（URL）
            category→分类（单选）

        Args:
            info: 标准信息字典，可包含 title, source, time, content, url, category 等字段

        Returns:
            page_id（成功时），空字符串（失败时）
        """
        if not info:
            logger.warning('store_info 收到空信息，跳过')
            return ''

        # 构建 Notion 页面属性
        properties: dict = {}

        # 标题 → Name（title 类型）
        title_text = info.get('title', '') or info.get('name', '') or '未命名'
        properties['Name'] = {
            'title': [{'text': {'content': str(title_text)}}]
        }

        # 来源 → 来源（select 类型）
        source_val = info.get('source', '') or info.get('来源', '')
        if source_val:
            properties['来源'] = {
                'select': {'name': str(source_val)}
            }

        # 时间 → 日期（date 类型）
        time_val = info.get('time', '') or info.get('日期', '') or info.get('date', '')
        if time_val:
            # 尝试标准化日期格式
            try:
                date_str = self._normalize_date(str(time_val))
                properties['日期'] = {
                    'date': {'start': date_str}
                }
            except (ValueError, TypeError):
                logger.debug(f'日期格式无法解析，跳过: {time_val}')

        # 内容 → 内容（rich_text 类型）
        content_val = info.get('content', '') or info.get('内容', '')
        if content_val:
            # Notion rich_text 限制单段 2000 字符
            truncated = str(content_val)[:2000]
            properties['内容'] = {
                'rich_text': [{'text': {'content': truncated}}]
            }

        # URL → 链接（url 类型）
        url_val = info.get('url', '') or info.get('链接', '')
        if url_val:
            properties['链接'] = {'url': str(url_val)}

        # 分类 → 分类（select 类型）
        category_val = info.get('category', '') or info.get('分类', '')
        if category_val:
            properties['分类'] = {
                'select': {'name': str(category_val)}
            }

        # 创建页面
        page_id = self.add_page(properties)
        if page_id:
            logger.info(f'信息已存入 Notion: {title_text}')
        else:
            logger.error(f'信息存入 Notion 失败: {title_text}')

        return page_id

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """标准化日期字符串为 ISO 8601 格式

        支持格式：
            - 2026-06-04
            - 2026-06-04 15:30:00
            - 2026/06/04
            - 2026年6月4日

        Returns:
            YYYY-MM-DD 格式字符串
        """
        date_str = date_str.strip()

        # 尝试多种格式解析
        formats = [
            '%Y-%m-%d',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y/%m/%d',
            '%Y/%m/%d %H:%M:%S',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        # 中文格式：2026年6月4日
        try:
            cleaned = date_str.replace('年', '-').replace('月', '-').replace('日', '')
            dt = datetime.strptime(cleaned, '%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # 如果都解析失败，尝试只提取日期部分
        if ' ' in date_str:
            return date_str.split(' ')[0]
        if 'T' in date_str:
            return date_str.split('T')[0]

        # 无法解析，返回原字符串
        return date_str


# ── 便捷函数 ─────────────────────────────────────────────────

_notion_instance: Optional[NotionManager] = None


def get_notion_manager() -> NotionManager:
    """获取 Notion 管理器单例"""
    global _notion_instance
    if _notion_instance is None:
        _notion_instance = NotionManager()
    return _notion_instance
