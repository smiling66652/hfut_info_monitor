"""
IMA 知识库管理模块
功能：封装 IMA 知识库 API 调用，提供统一的存储和检索接口

调用方式：通过子进程调用 ima_api.cjs（Node.js 脚本）
- 支持 sync（subprocess.run）和 async（asyncio.create_subprocess_exec）两种方式
- 添加失败时回退到本地缓存文件 data/kb_pending.json
"""

import json
import logging
import os
import asyncio
import subprocess
from datetime import datetime
from typing import Optional

logger = logging.getLogger('KnowledgeBase')

# 默认知识库 ID（合工大官网信息库）
DEFAULT_KB_ID = "QrT55rMB1Or293j-413d10j4JUOKYpRIR6BVZxwrv8c="

# Node.js 和 ima_api.cjs 路径
NODE_PATH = r"C:\Users\Matebook\.workbuddy\binaries\node\versions\22.22.2\node.exe"
IMA_API_CJS = r"C:\Users\Matebook\.workbuddy\skills\腾讯ima\ima_api.cjs"

# 本地缓存文件路径
_PENDING_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data',
)
_PENDING_CACHE_FILE = os.path.join(_PENDING_CACHE_DIR, 'kb_pending.json')


class KnowledgeBaseManager:
    """IMA 知识库管理器"""

    def __init__(self, kb_id: str | None = None):
        """初始化知识库管理器

        Args:
            kb_id: 知识库 ID，默认使用合工大官网信息库
        """
        self.kb_id = kb_id or DEFAULT_KB_ID
        logger.info(f"知识库管理器初始化，kb_id: {self.kb_id[:8]}...")

    # ─── 底层调用 ─────────────────────────────────────────────

    def _call_api_sync(self, endpoint: str, body: dict) -> dict:
        """同步调用 IMA API（通过 subprocess）

        Args:
            endpoint: API 路径，如 "openapi/wiki/v1/search_knowledge"
            body: 请求体 dict

        Returns:
            API 响应 dict，失败时返回 {"code": -1, "msg": "..."}

        Raises:
            无异常，错误通过返回值 code 字段传递
        """
        body_json = json.dumps(body, ensure_ascii=False)
        cmd = [NODE_PATH, IMA_API_CJS, endpoint, body_json]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                encoding='utf-8',
                errors='replace',
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip() if result.stderr else f"进程退出码: {result.returncode}"
                logger.error(f"API 调用失败 [{endpoint}]: {err_msg}")
                # 尝试解析 stderr 中的 JSON 错误信息
                try:
                    err_data = json.loads(err_msg)
                    return {"code": err_data.get("code", -1), "msg": err_data.get("msg", err_msg)}
                except (json.JSONDecodeError, TypeError):
                    return {"code": -1, "msg": err_msg}

            resp_text = result.stdout.strip()
            if not resp_text:
                return {"code": -1, "msg": "API 返回空响应"}

            return json.loads(resp_text)

        except subprocess.TimeoutExpired:
            logger.error(f"API 调用超时 [{endpoint}]")
            return {"code": -1, "msg": "API 调用超时（60s）"}
        except FileNotFoundError:
            logger.error(f"找不到 Node.js: {NODE_PATH}")
            return {"code": -1, "msg": f"找不到 Node.js: {NODE_PATH}"}
        except json.JSONDecodeError as e:
            logger.error(f"API 响应 JSON 解析失败 [{endpoint}]: {e}")
            return {"code": -1, "msg": f"JSON 解析失败: {e}"}
        except Exception as e:
            logger.error(f"API 调用异常 [{endpoint}]: {e}")
            return {"code": -1, "msg": str(e)}

    async def _call_api_async(self, endpoint: str, body: dict) -> dict:
        """异步调用 IMA API（通过 asyncio.create_subprocess_exec）

        Args:
            endpoint: API 路径
            body: 请求体 dict

        Returns:
            API 响应 dict
        """
        body_json = json.dumps(body, ensure_ascii=False)
        cmd = [NODE_PATH, IMA_API_CJS, endpoint, body_json]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode != 0:
                err_msg = stderr.decode('utf-8', errors='replace').strip()
                logger.error(f"API 异步调用失败 [{endpoint}]: {err_msg}")
                try:
                    err_data = json.loads(err_msg)
                    return {"code": err_data.get("code", -1), "msg": err_data.get("msg", err_msg)}
                except (json.JSONDecodeError, TypeError):
                    return {"code": -1, "msg": err_msg}

            resp_text = stdout.decode('utf-8', errors='replace').strip()
            if not resp_text:
                return {"code": -1, "msg": "API 返回空响应"}

            return json.loads(resp_text)

        except asyncio.TimeoutError:
            logger.error(f"API 异步调用超时 [{endpoint}]")
            return {"code": -1, "msg": "API 调用超时（60s）"}
        except FileNotFoundError:
            logger.error(f"找不到 Node.js: {NODE_PATH}")
            return {"code": -1, "msg": f"找不到 Node.js: {NODE_PATH}"}
        except json.JSONDecodeError as e:
            logger.error(f"API 异步响应 JSON 解析失败 [{endpoint}]: {e}")
            return {"code": -1, "msg": f"JSON 解析失败: {e}"}
        except Exception as e:
            logger.error(f"API 异步调用异常 [{endpoint}]: {e}")
            return {"code": -1, "msg": str(e)}

    def _call_api(self, endpoint: str, body: dict) -> dict:
        """统一同步调用入口（供同步方法使用）"""
        return self._call_api_sync(endpoint, body)

    # ─── 公共接口 ─────────────────────────────────────────────

    def health_check(self) -> bool:
        """检查知识库连接是否正常

        Returns:
            True 表示连接正常
        """
        resp = self._call_api(
            "openapi/wiki/v1/get_knowledge_base",
            {"ids": [self.kb_id]},
        )
        if resp.get("code") == 0:
            data = resp.get("data", {})
            # 响应结构: data.infos.<kb_id>.name
            infos = data.get("infos", {})
            kb_info = infos.get(self.kb_id, {})
            if kb_info:
                name = kb_info.get("name", "未知")
                logger.info(f"知识库连接正常: {name}")
                return True
        logger.warning(f"知识库连接检查失败: {resp.get('msg', '未知错误')}")
        return False

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """搜索知识库内容

        Args:
            query: 搜索关键词
            limit: 最大返回数量（API 不直接支持 limit，需客户端截取）

        Returns:
            匹配结果列表，每项为 dict
        """
        all_results = []
        cursor = ""

        while True:
            resp = self._call_api(
                "openapi/wiki/v1/search_knowledge",
                {
                    "query": query,
                    "knowledge_base_id": self.kb_id,
                    "cursor": cursor,
                },
            )
            if resp.get("code") != 0:
                logger.error(f"搜索知识库失败: {resp.get('msg', '未知错误')}")
                break

            data = resp.get("data", {})
            info_list = data.get("info_list", [])
            all_results.extend(info_list)

            if data.get("is_end", True) or len(all_results) >= limit:
                break
            cursor = data.get("next_cursor", "")

        results = all_results[:limit]
        logger.info(f"搜索知识库 '{query}'，返回 {len(results)} 条结果")
        return results

    async def search_async(self, query: str, limit: int = 20) -> list[dict]:
        """异步搜索知识库内容"""
        all_results = []
        cursor = ""

        while True:
            resp = await self._call_api_async(
                "openapi/wiki/v1/search_knowledge",
                {
                    "query": query,
                    "knowledge_base_id": self.kb_id,
                    "cursor": cursor,
                },
            )
            if resp.get("code") != 0:
                logger.error(f"搜索知识库失败: {resp.get('msg', '未知错误')}")
                break

            data = resp.get("data", {})
            info_list = data.get("info_list", [])
            all_results.extend(info_list)

            if data.get("is_end", True) or len(all_results) >= limit:
                break
            cursor = data.get("next_cursor", "")

        results = all_results[:limit]
        logger.info(f"搜索知识库 '{query}'，返回 {len(results)} 条结果")
        return results

    def list_contents(self, cursor: str = "", limit: int = 50) -> list[dict]:
        """浏览知识库内容列表

        Args:
            cursor: 分页游标，首次传空字符串
            limit: 每页数量（1~50）

        Returns:
            内容列表
        """
        resp = self._call_api(
            "openapi/wiki/v1/get_knowledge_list",
            {
                "knowledge_base_id": self.kb_id,
                "cursor": cursor,
                "limit": min(max(limit, 1), 50),
            },
        )
        if resp.get("code") != 0:
            logger.error(f"浏览知识库失败: {resp.get('msg', '未知错误')}")
            return []

        data = resp.get("data", {})
        info_list = data.get("info_list", [])
        logger.info(f"浏览知识库，返回 {len(info_list)} 条，is_end={data.get('is_end', True)}")
        return info_list

    def list_contents_with_paging(self, limit: int = 50) -> list[dict]:
        """浏览知识库所有内容（自动翻页）

        Args:
            limit: 每页数量

        Returns:
            全部内容列表
        """
        all_items = []
        cursor = ""

        while True:
            resp = self._call_api(
                "openapi/wiki/v1/get_knowledge_list",
                {
                    "knowledge_base_id": self.kb_id,
                    "cursor": cursor,
                    "limit": min(max(limit, 1), 50),
                },
            )
            if resp.get("code") != 0:
                logger.error(f"浏览知识库失败: {resp.get('msg', '未知错误')}")
                break

            data = resp.get("data", {})
            info_list = data.get("info_list", [])
            all_items.extend(info_list)

            if data.get("is_end", True):
                break
            cursor = data.get("next_cursor", "")

        logger.info(f"浏览知识库完成，共 {len(all_items)} 条内容")
        return all_items

    def create_folder(self, folder_name: str) -> str | None:
        """创建文件夹

        Args:
            folder_name: 文件夹名称

        Returns:
            成功返回 folder_id（media_id），失败返回 None
        """
        resp = self._call_api(
            "openapi/wiki/v1/add_knowledge",
            {
                "media_type": 3,
                "title": folder_name,
                "knowledge_base_id": self.kb_id,
            },
        )
        if resp.get("code") == 0:
            data = resp.get("data", {})
            folder_id = data.get("media_id") or data.get("folder_id")
            if folder_id:
                logger.info(f"创建文件夹成功: {folder_name} -> {folder_id}")
                return folder_id

        logger.error(f"创建文件夹失败: {resp.get('msg', '未知错误')}")
        return None

    def find_folder(self, folder_name: str) -> str | None:
        """通过搜索定位文件夹 ID

        Args:
            folder_name: 文件夹名称

        Returns:
            匹配的 folder_id，未找到返回 None
        """
        results = self.search(folder_name, limit=50)
        for item in results:
            if (item.get("media_type") == 3
                    and item.get("title") == folder_name
                    and item.get("media_id", "").startswith("folder_")):
                logger.info(f"找到文件夹: {folder_name} -> {item['media_id']}")
                return item["media_id"]
        return None

    def find_or_create_folder(self, folder_name: str) -> str | None:
        """查找文件夹，不存在则创建

        Args:
            folder_name: 文件夹名称

        Returns:
            folder_id，失败返回 None
        """
        folder_id = self.find_folder(folder_name)
        if folder_id:
            return folder_id
        return self.create_folder(folder_name)

    def add_from_url(self, url: str, title: str | None = None, folder_id: str | None = None) -> bool:
        """从 URL 导入网页到知识库

        Args:
            url: 网页 URL
            title: 可选标题（日志用）
            folder_id: 目标文件夹 ID，None 表示根目录

        Returns:
            成功返回 True
        """
        body: dict = {
            "knowledge_base_id": self.kb_id,
            "urls": [url],
        }
        if folder_id:
            body["folder_id"] = folder_id

        resp = self._call_api("openapi/wiki/v1/import_urls", body)
        if resp.get("code") == 0:
            data = resp.get("data", {})
            results = data.get("results", {})
            result = results.get(url, {})
            ret_code = result.get("ret_code", -1)
            if ret_code == 0:
                display = title or url
                logger.info(f"URL 导入成功: {display}")
                return True
            else:
                logger.error(f"URL 导入失败（ret_code={ret_code}）: {result.get('msg', '未知错误')}")
                return False

        logger.error(f"URL 导入失败: {resp.get('msg', '未知错误')}")
        self._cache_pending_url(url, title, folder_id)
        return False

    async def add_from_url_async(self, url: str, title: str | None = None, folder_id: str | None = None) -> bool:
        """异步从 URL 导入网页到知识库"""
        body: dict = {
            "knowledge_base_id": self.kb_id,
            "urls": [url],
        }
        if folder_id:
            body["folder_id"] = folder_id

        resp = await self._call_api_async("openapi/wiki/v1/import_urls", body)
        if resp.get("code") == 0:
            data = resp.get("data", {})
            results = data.get("results", {})
            result = results.get(url, {})
            ret_code = result.get("ret_code", -1)
            if ret_code == 0:
                display = title or url
                logger.info(f"URL 导入成功（异步）: {display}")
                return True
            else:
                logger.error(f"URL 导入失败（ret_code={ret_code}）: {result.get('msg', '未知错误')}")
                return False

        logger.error(f"URL 导入失败（异步）: {resp.get('msg', '未知错误')}")
        self._cache_pending_url(url, title, folder_id)
        return False

    def add_text_note(self, title: str, content: str, folder_id: str | None = None) -> bool:
        """添加文本内容到知识库

        Plan A: 通过 import_urls 无法用于纯文本，尝试创建知识条目
        Plan B: 如果 API 不支持纯文本添加，回退到本地缓存

        Args:
            title: 标题
            content: 文本内容
            folder_id: 目标文件夹 ID

        Returns:
            成功返回 True，回退到本地缓存也返回 True（但会记录日志）
        """
        # 尝试通过 search_knowledge 检查是否已有同名内容（避免重复）
        existing = self.search(title, limit=5)
        for item in existing:
            if item.get("title") == title:
                logger.info(f"知识库中已存在同名内容: {title}，跳过添加")
                return True

        # IMA API 不支持直接添加纯文本知识条目（需要 media_id 或 note_id）
        # 回退到本地缓存
        logger.info(f"纯文本无法直接添加到知识库，缓存到本地: {title}")
        self._cache_pending_text(title, content, folder_id)
        return True

    # ─── 本地缓存（回退机制） ──────────────────────────────

    @staticmethod
    def _ensure_cache_dir():
        """确保缓存目录存在"""
        os.makedirs(_PENDING_CACHE_DIR, exist_ok=True)

    def _load_pending_cache(self) -> list[dict]:
        """加载本地待处理缓存"""
        if not os.path.exists(_PENDING_CACHE_FILE):
            return []
        try:
            with open(_PENDING_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"读取缓存文件失败，将覆盖: {e}")
            return []

    def _save_pending_cache(self, cache: list[dict]):
        """保存待处理缓存"""
        self._ensure_cache_dir()
        try:
            with open(_PENDING_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存缓存文件失败: {e}")

    def _cache_pending_url(self, url: str, title: str | None, folder_id: str | None):
        """将失败的 URL 导入任务缓存到本地"""
        cache = self._load_pending_cache()
        cache.append({
            "type": "url",
            "url": url,
            "title": title,
            "folder_id": folder_id,
            "knowledge_base_id": self.kb_id,
            "created_at": datetime.now().isoformat(),
        })
        self._save_pending_cache(cache)
        logger.info(f"已缓存 URL 导入任务到 {_PENDING_CACHE_FILE}")

    def _cache_pending_text(self, title: str, content: str, folder_id: str | None):
        """将失败的文本添加任务缓存到本地"""
        cache = self._load_pending_cache()
        cache.append({
            "type": "text",
            "title": title,
            "content": content,
            "folder_id": folder_id,
            "knowledge_base_id": self.kb_id,
            "created_at": datetime.now().isoformat(),
        })
        self._save_pending_cache(cache)
        logger.info(f"已缓存文本添加任务到 {_PENDING_CACHE_FILE}")

    def retry_pending(self) -> dict[str, int]:
        """重试所有待处理缓存

        Returns:
            {"url_success": n, "url_failed": n, "text_cached": n}
        """
        cache = self._load_pending_cache()
        if not cache:
            logger.info("没有待处理的缓存任务")
            return {"url_success": 0, "url_failed": 0, "text_cached": 0}

        stats = {"url_success": 0, "url_failed": 0, "text_cached": 0}
        remaining = []

        for item in cache:
            if item["type"] == "url":
                url = item["url"]
                title = item.get("title")
                folder_id = item.get("folder_id")
                if self.add_from_url(url, title, folder_id):
                    stats["url_success"] += 1
                else:
                    stats["url_failed"] += 1
                    remaining.append(item)
            elif item["type"] == "text":
                # 文本类型目前无法通过 API 添加，保持缓存
                stats["text_cached"] += 1
                remaining.append(item)

        self._save_pending_cache(remaining)
        logger.info(f"重试完成: URL 成功 {stats['url_success']}，"
                     f"失败 {stats['url_failed']}，文本缓存 {stats['text_cached']}")
        return stats

    # ─── 日期文件夹工具 ─────────────────────────────────────

    @staticmethod
    def today_folder_name() -> str:
        """返回今天日期格式的文件夹名，如 '2026-06-04'"""
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def source_folder_names() -> list[str]:
        """返回按来源分类的文件夹名列表"""
        return ["官网通知", "QQ群消息", "QQ空间动态"]


# ─── 便捷函数 ─────────────────────────────────────────────

def get_kb_manager(kb_id: str | None = None) -> KnowledgeBaseManager:
    """获取知识库管理器实例"""
    return KnowledgeBaseManager(kb_id)
