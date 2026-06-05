"""
统一存储调度模块
功能：统一调度 IMA + Notion 双数据库，提供双写、读取、健康检查和失败回退机制

架构层次：
    业务层调用 StorageManager
        ├── IMA（KnowledgeBaseManager）  ← 主存储
        ├── Notion（NotionManager）       ← 辅助存储
        └── 本地归档（ArchiveManager）    ← 失败回退

策略：
    - 双写：同步写入 IMA + Notion
    - 失败回退：IMA 失败 → 本地 JSON 缓存 / Notion 失败 → pending 标记
    - 三重保障：IMA → Notion → 本地归档
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from archive import ArchiveManager, get_archive
from config_loader import get_base_dir, load_config
from knowledge_base import KnowledgeBaseManager, get_kb_manager
from notion_manager import NotionManager, get_notion_manager

logger = logging.getLogger('StorageManager')

# pending 缓存文件路径
_PENDING_DIR = os.path.join(get_base_dir(), 'data')
_PENDING_FILE = os.path.join(_PENDING_DIR, 'storage_pending.json')


class StorageManager:
    """统一存储管理器

    统一调度 IMA 知识库和 Notion 数据库，提供：
    - 同步/异步双写
    - 多源搜索与读取
    - 全链路健康检查
    - 失败自动回退和重试
    """

    def __init__(self):
        """初始化存储管理器，加载各子模块"""
        # 加载配置
        self.storage_config = self._load_storage_config()
        self.notion_enabled = self.storage_config.get('notion_enabled', False)

        # 初始化子管理器
        self.ima = get_kb_manager()
        self.notion = get_notion_manager() if self.notion_enabled else None
        self.archive = get_archive()

        # 配置项
        self.primary = self.storage_config.get('primary', 'ima')
        self.fallback = self.storage_config.get('fallback', 'local_archive')
        self.auto_retry = self.storage_config.get('auto_retry_failed', True)
        self.retry_interval = self.storage_config.get('retry_interval_hours', 6)

        logger.info(
            f'存储管理器初始化: 主存储={self.primary}, '
            f'Notion={"启用" if self.notion_enabled else "未启用"}, '
            f'回退={self.fallback}'
        )

    @staticmethod
    def _load_storage_config() -> dict:
        """加载 storage 配置节"""
        try:
            config = load_config()
            return config.get('storage', {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'primary': 'ima',
                'fallback': 'local_archive',
                'notion_enabled': False,
                'auto_retry_failed': True,
                'retry_interval_hours': 6,
            }

    # ── 写入 ──────────────────────────────────────────────────

    def store(self, info: dict, targets: list | None = None) -> dict:
        """同步双写：将信息写入指定目标

        Args:
            info: 标准信息字典
            targets: 写入目标列表，默认 ["ima", "notion"]
                     可用值: "ima", "notion", "archive"

        Returns:
            {
                "ima": {"success": bool, "detail": str},
                "notion": {"success": bool, "detail": str, "page_id": str},
                "archive": {"success": bool, "detail": str}
            }
        """
        if targets is None:
            targets = ['ima']
            if self.notion_enabled:
                targets.append('notion')

        # 始终写入本地归档作为兜底
        if 'archive' not in targets:
            targets.append('archive')

        result: dict = {}

        for target in targets:
            try:
                if target == 'ima':
                    result['ima'] = self._store_to_ima(info)
                elif target == 'notion':
                    result['notion'] = self._store_to_notion(info)
                elif target == 'archive':
                    result['archive'] = self._store_to_archive(info)
                else:
                    logger.warning(f'未知存储目标: {target}')
            except Exception as e:
                logger.error(f'存储到 {target} 时发生异常: {e}')
                result[target] = {'success': False, 'detail': str(e)}

        # 如果 IMA 写入失败，自动回退到本地 JSON 缓存
        ima_result = result.get('ima', {})
        if not ima_result.get('success', False):
            self._fallback_to_local_cache(info, reason='ima_failed')

        # 如果 Notion 写入失败，标记为 pending 稍后重试
        notion_result = result.get('notion', {})
        if not notion_result.get('success', False) and 'notion' in targets:
            self._mark_as_pending(info, reason='notion_failed')

        logger.info(
            f'存储结果: IMA={"OK" if result.get("ima",{}).get("success") else "FAIL"}, '
            f'Notion={"OK" if result.get("notion",{}).get("success") else "N/A"}, '
            f'Archive={"OK" if result.get("archive",{}).get("success") else "FAIL"}'
        )

        return result

    async def store_async(self, info: dict, targets: list | None = None) -> dict:
        """异步并行写入两个数据库

        Args:
            info: 标准信息字典
            targets: 写入目标列表

        Returns:
            同 store() 的返回格式
        """
        if targets is None:
            targets = ['ima']
            if self.notion_enabled:
                targets.append('notion')

        # 始终写入本地归档
        if 'archive' not in targets:
            targets.append('archive')

        result: dict = {}

        async def _store_ima():
            return self._store_to_ima(info)

        async def _store_notion():
            return self._store_to_notion(info)

        # 创建异步任务
        tasks: list = []

        if 'ima' in targets:
            tasks.append(('ima', _store_ima()))
        if 'notion' in targets:
            tasks.append(('notion', _store_notion()))

        # 并行执行
        if tasks:
            for name, task in tasks:
                try:
                    task_result = await task
                    result[name] = task_result
                except Exception as e:
                    logger.error(f'异步存储到 {name} 时发生异常: {e}')
                    result[name] = {'success': False, 'detail': str(e)}

        # 归档同步写入（归档使用文件锁，不适合异步）
        result['archive'] = self._store_to_archive(info)

        # 失败回退
        ima_result = result.get('ima', {})
        if not ima_result.get('success', False):
            self._fallback_to_local_cache(info, reason='ima_async_failed')

        notion_result = result.get('notion', {})
        if not notion_result.get('success', False) and 'notion' in targets:
            self._mark_as_pending(info, reason='notion_async_failed')

        return result

    # ── 内部存储方法 ──────────────────────────────────────────

    def _store_to_ima(self, info: dict) -> dict:
        """存储信息到 IMA 知识库

        IMA 支持通过 URL 导入，如果 info 有 url 字段则导入 URL，
        否则记录日志标记为仅归档。
        """
        title = info.get('title', '') or info.get('name', '') or '未命名'
        url = info.get('url', '') or info.get('链接', '')

        if url:
            try:
                success = self.ima.add_from_url(url, title=title)
                if success:
                    return {'success': True, 'detail': f'已导入 IMA: {title}'}
                else:
                    return {'success': False, 'detail': f'IMA 导入失败: {title}'}
            except Exception as e:
                return {'success': False, 'detail': f'IMA 导入异常: {str(e)}'}
        else:
            # 没有 URL 的信息仅做归档记录
            logger.debug(f'信息无 URL，跳过 IMA 导入: {title}')
            return {'success': True, 'detail': f'仅归档（无URL）: {title}'}

    def _store_to_notion(self, info: dict) -> dict:
        """存储信息到 Notion 数据库"""
        if not self.notion:
            return {'success': False, 'detail': 'Notion 未启用'}

        try:
            page_id = self.notion.store_info(info)
            if page_id:
                return {
                    'success': True,
                    'detail': f'已存入 Notion',
                    'page_id': page_id,
                }
            else:
                return {
                    'success': False,
                    'detail': 'Notion 创建页面失败',
                    'page_id': '',
                }
        except Exception as e:
            return {'success': False, 'detail': f'Notion 异常: {str(e)}'}

    def _store_to_archive(self, info: dict) -> dict:
        """存储信息到本地 JSON 归档"""
        try:
            # 补充归档时间
            if not info.get('archive_time'):
                info['archive_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            success = self.archive.save(info)
            if success:
                return {'success': True, 'detail': '已归档到本地'}
            else:
                return {'success': False, 'detail': '本地归档写入失败'}
        except Exception as e:
            return {'success': False, 'detail': f'归档异常: {str(e)}'}

    # ── 读取 ──────────────────────────────────────────────────

    def search(self, query: str, source: str | None = None) -> dict:
        """从指定源搜索信息

        Args:
            query: 搜索关键词
            source: 搜索源，None 表示两个都搜
                    可选: "ima", "notion", "archive"

        Returns:
            {
                "ima": list[dict],      # IMA 搜索结果
                "notion": list[dict],   # Notion 搜索结果
                "archive": list[dict],  # 本地归档搜索结果
            }
        """
        result: dict = {}

        if source is None or source == 'ima':
            try:
                result['ima'] = self.ima.search(query)
            except Exception as e:
                logger.error(f'IMA 搜索异常: {e}')
                result['ima'] = []

        if source is None or source == 'notion':
            if self.notion:
                try:
                    result['notion'] = self.notion.search(query)
                except Exception as e:
                    logger.error(f'Notion 搜索异常: {e}')
                    result['notion'] = []
            else:
                result['notion'] = []

        if source is None or source == 'archive':
            try:
                all_items = self.archive.read_all()
                # 简单关键词过滤
                q_lower = query.lower()
                matched = [
                    item for item in all_items
                    if any(
                        q_lower in str(v).lower()
                        for v in item.values()
                    )
                ]
                result['archive'] = matched
            except Exception as e:
                logger.error(f'归档搜索异常: {e}')
                result['archive'] = []

        total = sum(len(v) for v in result.values())
        logger.info(f'搜索 "{query}" 共找到 {total} 条结果')
        return result

    def get_recent(self, days: int = 7) -> list:
        """获取近期数据

        优先从 IMA 获取，其次从本地归档读取。

        Args:
            days: 获取最近 N 天的数据

        Returns:
            信息列表
        """
        results: list = []

        # 从本地归档读取（可靠数据源）
        cutoff = datetime.now() - timedelta(days=days)
        try:
            all_items = self.archive.read_all()
            for item in all_items:
                time_str = (
                    item.get('time', '')
                    or item.get('archive_time', '')
                    or ''
                )
                if time_str:
                    try:
                        item_time = datetime.strptime(
                            time_str[:10], '%Y-%m-%d'
                        )
                        if item_time >= cutoff:
                            results.append(item)
                    except ValueError:
                        # 无法解析时间，保守保留
                        results.append(item)
                else:
                    results.append(item)
        except Exception as e:
            logger.error(f'读取近期归档失败: {e}')

        # 按时间倒序排列
        results.sort(
            key=lambda x: x.get('time', '') or x.get('archive_time', ''),
            reverse=True,
        )

        logger.info(f'获取最近 {days} 天数据，共 {len(results)} 条')
        return results

    # ── 健康检查 ──────────────────────────────────────────────

    def health_check(self) -> dict:
        """全链路健康检查

        Returns:
            {
                "ima": {"status": "ok"/"fail", "detail": str},
                "notion": {"status": "ok"/"fail"/"disabled", "detail": str},
                "local_archive": {"status": "ok"/"fail", "detail": str},
            }
        """
        result: dict = {}

        # IMA 检查
        try:
            ima_ok = self.ima.health_check()
            result['ima'] = {
                'status': 'ok' if ima_ok else 'fail',
                'detail': '连接正常' if ima_ok else '连接失败',
            }
        except Exception as e:
            result['ima'] = {'status': 'fail', 'detail': str(e)}

        # Notion 检查
        if self.notion:
            try:
                notion_ok, notion_msg = self.notion.health_check()
                result['notion'] = {
                    'status': 'ok' if notion_ok else 'fail',
                    'detail': notion_msg,
                }
            except Exception as e:
                result['notion'] = {'status': 'fail', 'detail': str(e)}
        else:
            result['notion'] = {
                'status': 'disabled',
                'detail': 'Notion 未启用',
            }

        # 本地归档检查
        try:
            archive_path = os.path.join(
                get_base_dir(), 'data', 'hfut_archive.json'
            )
            archive_ok = os.path.exists(archive_path) or os.access(
                os.path.dirname(archive_path), os.W_OK
            )
            result['local_archive'] = {
                'status': 'ok' if archive_ok else 'fail',
                'detail': '归档目录可访问' if archive_ok else '归档目录不可访问',
            }
        except Exception as e:
            result['local_archive'] = {'status': 'fail', 'detail': str(e)}

        logger.info(
            f'健康检查: IMA={result["ima"]["status"]}, '
            f'Notion={result["notion"]["status"]}, '
            f'Archive={result["local_archive"]["status"]}'
        )

        return result

    # ── 失败回退 ──────────────────────────────────────────────

    def _fallback_to_local_cache(self, info: dict, reason: str = 'unknown') -> None:
        """IMA 写入失败时回退到本地 JSON 缓存

        Args:
            info: 失败的信息
            reason: 失败原因
        """
        cache_path = os.path.join(
            _PENDING_DIR, 'ima_fallback_cache.json'
        )

        cache_entry = {
            'info': info,
            'reason': reason,
            'cached_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        try:
            os.makedirs(_PENDING_DIR, exist_ok=True)

            # 读取现有缓存
            cache: list = []
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        cache = json.load(f)
                except (json.JSONDecodeError, IOError):
                    cache = []

            cache.append(cache_entry)

            # 限制缓存大小（最多 500 条）
            if len(cache) > 500:
                cache = cache[-500:]

            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

            logger.info(f'IMA 回退缓存已保存: {reason}')
        except Exception as e:
            logger.error(f'保存 IMA 回退缓存失败: {e}')

    def _mark_as_pending(self, info: dict, reason: str = 'unknown') -> None:
        """Notion 写入失败时标记为 pending 稍后重试

        Args:
            info: 失败的信息
            reason: 失败原因
        """
        pending_entry = {
            'info': info,
            'reason': reason,
            'target': 'notion',
            'marked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'retry_count': 0,
        }

        try:
            os.makedirs(_PENDING_DIR, exist_ok=True)

            pending: list = []
            if os.path.exists(_PENDING_FILE):
                try:
                    with open(_PENDING_FILE, 'r', encoding='utf-8') as f:
                        pending = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pending = []

            pending.append(pending_entry)

            with open(_PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(pending, f, ensure_ascii=False, indent=2)

            logger.info(f'Notion pending 条目已保存: {reason}')
        except Exception as e:
            logger.error(f'保存 pending 条目失败: {e}')

    def retry_pending(self) -> dict[str, int]:
        """重试所有 pending 条目

        Returns:
            {"success": int, "failed": int, "skipped": int}
        """
        if not os.path.exists(_PENDING_FILE):
            return {'success': 0, 'failed': 0, 'skipped': 0}

        try:
            with open(_PENDING_FILE, 'r', encoding='utf-8') as f:
                pending = json.load(f)
        except (json.JSONDecodeError, IOError):
            return {'success': 0, 'failed': 0, 'skipped': 0}

        if not pending:
            return {'success': 0, 'failed': 0, 'skipped': 0}

        stats = {'success': 0, 'failed': 0, 'skipped': 0}
        remaining: list = []

        for entry in pending:
            info = entry.get('info', {})
            target = entry.get('target', 'notion')

            # 检查重试间隔
            marked_at_str = entry.get('marked_at', '')
            if marked_at_str:
                try:
                    marked_at = datetime.strptime(marked_at_str, '%Y-%m-%d %H:%M:%S')
                    elapsed = datetime.now() - marked_at
                    if elapsed < timedelta(hours=self.retry_interval):
                        remaining.append(entry)
                        stats['skipped'] += 1
                        continue
                except ValueError:
                    pass

            # 执行重试
            success = False
            if target == 'notion' and self.notion:
                try:
                    page_id = self.notion.store_info(info)
                    success = bool(page_id)
                except Exception:
                    success = False

            if success:
                stats['success'] += 1
            else:
                entry['retry_count'] = entry.get('retry_count', 0) + 1
                # 最多重试 5 次
                if entry['retry_count'] < 5:
                    remaining.append(entry)
                else:
                    logger.warning(f'pending 条目达到最大重试次数，丢弃: {info.get("title", "N/A")}')
                    stats['failed'] += 1

        # 保存剩余 pending 条目
        try:
            with open(_PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(remaining, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f'保存更新后的 pending 文件失败: {e}')

        logger.info(
            f'Pending 重试完成: 成功={stats["success"]}, '
            f'失败={stats["failed"]}, 跳过={stats["skipped"]}'
        )
        return stats


# ── 便捷函数 ─────────────────────────────────────────────────

_storage_instance: Optional[StorageManager] = None


def get_storage() -> StorageManager:
    """获取存储管理器单例"""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageManager()
    return _storage_instance
