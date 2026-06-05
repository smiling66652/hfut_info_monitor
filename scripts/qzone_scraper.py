"""
QQ空间动态采集模块
功能：使用 qzone-api 库实现QQ空间说说/动态的采集
支持：二维码登录、全量扫描、增量扫描、数据持久化

多级退回机制：
- Plan A：使用 qzone-api 库（已成功安装）
- Plan B：使用 requests 直接请求 m.qzone.cn（备选方案）
- Plan C：框架留白，标注环境要求（最终兜底）

作者：CodeBuddy Code
日期：2026-06-04
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

# 添加 scripts 目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import get_data_dir, get_log_dir

# 设置日志
logger = logging.getLogger('QzoneScraper')

# 时区配置：北京时间（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))

# 数据存储目录
DATA_DIR = get_data_dir()
QZONE_DATA_FILE = DATA_DIR / 'qzone_shuos.json'
QZONE_COOKIE_FILE = DATA_DIR / 'qzone_cookies.json'


# =============================================================================
# Plan A：使用 qzone-api 库（主方案）
# =============================================================================

try:
    from qzone_api import QzoneApi
    from qzone_api.login import QzoneLogin
    QZONE_API_AVAILABLE = True
    logger.info("✅ qzone-api 库加载成功（Plan A 可用）")
except ImportError as e:
    QZONE_API_AVAILABLE = False
    logger.warning(f"⚠️ qzone-api 库不可用：{e}，将尝试 Plan B")


class QzoneScraperPlanA:
    """
    Plan A：基于 qzone-api 的QQ空间采集器
    - 支持二维码登录
    - 支持全量/增量采集
    - 自动速率控制
    - 完善的异常处理
    """

    def __init__(self):
        """初始化采集器"""
        self.api: Optional[QzoneApi] = None
        self.login_handler: Optional[QzoneLogin] = None
        self.cookies: Dict[str, str] = {}
        self.skey: str = ''
        self.qq: str = ''
        self.g_tk: int = 0
        self.is_logged_in: bool = False

        # 速率控制参数（秒）
        self.min_delay = 1.0   # 最小间隔
        self.max_delay = 3.0   # 最大间隔
        self.last_request_time: float = 0.0

    async def login(self, timeout: int = 120) -> Dict[str, Any]:
        """
        二维码登录QQ空间
        
        Args:
            timeout: 二维码有效时间（秒），默认120秒
            
        Returns:
            dict: 登录结果 {
                'success': bool,
                'message': str,
                'qq': str (可选),
                'cookies': dict (可选)
            }
            
        使用方法：
        >>> scraper = QzoneScraperPlanA()
        >>> result = await scraper.login(timeout=120)
        >>> if result['success']:
        ...     print(f"登录成功，QQ号：{result['qq']}")
        """
        logger.info("🔐 开始QQ空间二维码登录...")

        try:
            if not QZONE_API_AVAILABLE:
                return {
                    'success': False,
                    'message': 'qzone-api 库不可用'
                }

            # 初始化登录处理器
            self.login_handler = QzoneLogin()

            # 启动后台线程：1.5秒后自动打开二维码图片（PowerShell ASCII 二维码不清晰）
            def _open_qr_image():
                time.sleep(1.5)
                qr_path = Path(os.getcwd()) / 'QR.png'
                if qr_path.exists():
                    logger.info(f"📱 二维码图片已保存：{qr_path}")
                    try:
                        os.startfile(str(qr_path))
                    except Exception:
                        subprocess.run(['start', str(qr_path)], shell=True)

            threading.Thread(target=_open_qr_image, daemon=True).start()

            # 执行登录（会生成二维码并等待扫码）
            login_result = await self.login_handler.login(timeout=timeout)

            if login_result.get('code') != 0:
                error_msg = login_result.get('msg', '未知错误')
                logger.error(f"❌ 登录失败：{error_msg}")
                return {
                    'success': False,
                    'message': error_msg
                }

            # 提取登录信息
            self.cookies = login_result.get('cookies', {})
            self.skey = login_result.get('skey', '')
            self.qq = str(login_result.get('qq', ''))
            self.g_tk = login_result.get('bkn', 0)
            self.is_logged_in = True

            # 保存 cookies 以便后续使用
            await self._save_cookies()

            logger.info(f"✅ 登录成功！QQ号：{self.qq}")
            return {
                'success': True,
                'message': '登录成功',
                'qq': self.qq,
                'cookies': self.cookies
            }

        except Exception as e:
            logger.error(f"❌ 登录过程异常：{e}")
            return {
                'success': False,
                'message': f'登录异常：{str(e)}'
            }

    async def _save_cookies(self) -> None:
        """保存 cookies 到本地文件"""
        try:
            cookie_data = {
                'cookies': self.cookies,
                'skey': self.skey,
                'qq': self.qq,
                'g_tk': self.g_tk,
                'save_time': datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(QZONE_COOKIE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cookie_data, f, ensure_ascii=False, indent=2)
            logger.debug("Cookies 已保存到本地")
        except Exception as e:
            logger.warning(f"保存 cookies 失败：{e}")

    async def load_saved_cookies(self) -> bool:
        """
        加载之前保存的 cookies
        
        Returns:
            bool: 是否成功加载
        """
        if not QZONE_COOKIE_FILE.exists():
            logger.info("未找到已保存的 cookies 文件")
            return False

        try:
            with open(QZONE_COOKIE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.cookies = data.get('cookies', {})
            self.skey = data.get('skey', '')
            self.qq = str(data.get('qq', ''))
            self.g_tk = data.get('g_tk', 0)
            self.is_logged_in = True

            save_time = data.get('save_time', '未知')
            logger.info(f"✅ 已加载保存的 cookies（保存于 {save_time}），QQ号：{self.qq}")
            return True

        except Exception as e:
            logger.warning(f"加载 cookies 失败：{e}")
            return False

    async def _rate_limit(self) -> None:
        """速率控制：确保请求间隔在合理范围内"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time

        if elapsed < self.min_delay:
            wait_time = self.min_delay - elapsed + random.uniform(0, self.max_delay - self.min_delay)
            logger.debug(f"速率控制：等待 {wait_time:.2f} 秒")
            await asyncio.sleep(wait_time)

        self.last_request_time = time.time()

    async def scrape_all_shuos(self, target_qq: str) -> List[Dict[str, Any]]:
        """
        获取指定QQ的全部历史说说（全量扫描）
        
        Args:
            target_qq: 目标QQ号
            
        Returns:
            list[dict]: 统一格式的说说列表
            
        示例：
        >>> scraper = QzoneScraperPlanA()
        >>> await scraper.load_saved_cookies()  # 或先登录
        >>> shuos = await scraper.scrape_all_shuos("123456789")
        >>> print(f"共获取 {len(shuos)} 条说说")
        """
        logger.info(f"📥 开始全量扫描说说（目标QQ：{target_qq}）")

        if not self.is_logged_in:
            logger.error("未登录或未加载 cookies，无法采集")
            return []

        all_shuos = []
        pos = 0
        page_size = 20  # 每页数量
        max_pages = 500  # 最大页数限制，防止无限循环
        consecutive_empty_pages = 0  # 连续空页数计数器

        try:
            self.api = QzoneApi()

            for page in range(1, max_pages + 1):
                logger.info(f"📄 正在获取第 {page} 页（pos={pos}）")

                # 速率控制
                await self._rate_limit()

                # 调用 API 获取数据
                raw_data = await self.api.get_messages_list(
                    target_qq=int(target_qq),
                    g_tk=self.g_tk,
                    cookies=self._format_cookies(),
                    pos=pos,
                    num=page_size
                )

                # 解析数据
                shuos = self._parse_raw_data(raw_data, target_qq)

                if not shuos:
                    consecutive_empty_pages += 1
                    logger.warning(f"⚠️ 第 {page} 页无数据")
                    
                    # 连续2页无数据则停止
                    if consecutive_empty_pages >= 2:
                        logger.info("连续多页无数据，停止采集")
                        break
                else:
                    consecutive_empty_pages = 0
                    all_shuos.extend(shuos)
                    logger.info(f"  ✅ 本页获取 {len(shuos)} 条，累计 {len(all_shuos)} 条")

                # 更新位置
                pos += page_size

                # 如果本页返回数量少于页大小，说明已到末尾
                if shuos and len(shuos) < page_size:
                    logger.info("已达最后一页，停止采集")
                    break

            logger.info(f"✅ 全量扫描完成，共获取 {len(all_shuos)} 条说说")
            return all_shuos

        except Exception as e:
            logger.error(f"❌ 全量扫描异常：{e}")
            return all_shuos  # 返回已采集的数据

    async def scrape_new_shuos(self, target_qq: str, since_timestamp: int) -> List[Dict[str, Any]]:
        """
        获取指定时间戳之后的新说说（增量扫描）
        
        Args:
            target_qq: 目标QQ号
            since_timestamp: 起始时间戳（Unix时间戳，秒）
            
        Returns:
            list[dict]: 统一格式的新说说列表
            
        示例：
        >>> # 获取最近7天的新说说
        >>> import time
        >>> since = int(time.time()) - 7 * 24 * 3600
        >>> new_shuos = await scraper.scrape_new_shuos("123456789", since)
        """
        logger.info(
            f"🔄 开始增量扫描（目标QQ：{target_qq}，"
            f"起始时间：{datetime.fromtimestamp(since_timestamp, tz=BEIJING_TZ).strftime('%Y-%m-%d %H:%M')}）"
        )

        if not self.is_logged_in:
            logger.error("未登录或未加载 cookies，无法采集")
            return []

        all_new_shuos = []
        pos = 0
        page_size = 20
        max_pages = 50  # 增量扫描限制页数

        try:
            self.api = QzoneApi()

            for page in range(1, max_pages + 1):
                await self._rate_limit()

                raw_data = await self.api.get_messages_list(
                    target_qq=int(target_qq),
                    g_tk=self.g_tk,
                    cookies=self._format_cookies(),
                    pos=pos,
                    num=page_size
                )

                shuos = self._parse_raw_data(raw_data, target_qq)

                if not shuos:
                    logger.info("无更多新说说")
                    break

                # 过滤出新说说
                for shuo in shuos:
                    shuo_time = shuo.get('publish_timestamp', 0)
                    if shuo_time > since_timestamp:
                        all_new_shuos.append(shuo)
                    else:
                        # 遇到旧说说，停止扫描
                        logger.info(f"遇到旧说说（{shuo.get('publish_time')}），停止扫描")
                        return all_new_shuos

                logger.info(f"📄 第 {page} 页：新增 {len([s for s in shuos if s.get('publish_timestamp', 0) > since_timestamp])} 条")

                pos += page_size

                # 如果本页都是旧说说，提前退出
                latest_time = max(s.get('publish_timestamp', 0) for s in shuos)
                if latest_time <= since_timestamp:
                    break

            logger.info(f"✅ 增量扫描完成，共获取 {len(all_new_shuos)} 条新说说")
            return all_new_shuos

        except Exception as e:
            logger.error(f"❌ 增量扫描异常：{e}")
            return all_new_shuos

    def _parse_raw_data(self, raw_data: Optional[Any], target_qq: str) -> List[Dict[str, Any]]:
        """
        解析原始API数据为统一格式
        
        Args:
            raw_data: qzone-api 返回的原始数据
            target_qq: 目标QQ号
            
        Returns:
            list[dict]: 标准化的说说列表
        """
        if not raw_data:
            return []

        shuos = []

        try:
            # 处理非字典类型的返回（如返回 list、error dict 等）
            if not isinstance(raw_data, dict):
                logger.warning(f"API返回非字典类型数据：{type(raw_data).__name__}")
                return []

            # 检查是否返回错误状态（来自 parse_feed_data 的 error 响应）
            if raw_data.get('status') == 'error':
                msg = raw_data.get('message', '未知错误')
                # 常见错误码含义提示
                if 'NoneType' in msg:
                    msg = '对方QQ空间设置了权限，无法访问'
                logger.warning(f"🔒 无法访问 QQ {target_qq} 的空间：{msg}")
                return []

            # 尝试从不同字段提取说说列表
            msglist = raw_data.get('msglist', [])
            if not msglist:
                # parse_feed_data 包装格式：{'status': 'ok', 'data': [...]}
                data_field = raw_data.get('data')
                if isinstance(data_field, list):
                    msglist = data_field
                elif isinstance(data_field, dict):
                    msglist = data_field.get('msglist', [])

            if not msglist:
                logger.info(f"QQ {target_qq} 没有公开的说说数据")
                return []

            for item in msglist:
                try:
                    parsed = self.parse_shuo_data(item, target_qq)
                    if parsed:
                        shuos.append(parsed)
                except Exception as e:
                    logger.warning(f"解析单条说说失败：{e}")
                    continue

        except Exception as e:
            logger.error(f"解析原始数据失败：{e}")

        return shuos

    def parse_shuo_data(self, raw_item: Dict, target_qq: str) -> Optional[Dict[str, Any]]:
        """
        解析单条原始说说数据为统一格式
        
        统一格式规范：
        - id: 唯一ID（MD5哈希）
        - source: 来源标识（'QQ空间'）
        - category: 分类（'说说'/'转发'/'留言'）
        - title: 内容摘要（前50字）
        - content: 完整内容
        - url: 说说的URL链接
        - author: 作者昵称
        - author_qq: 作者QQ号
        - publish_time: 发布时间（YYYY-MM-DD HH:MM:SS）
        - publish_time_int: 发布时间戳（秒）
        - fetch_time: 采集时间（YYYY-MM-DD HH:MM:SS）
        - raw: 原始数据备份
        
        Args:
            raw_item: 单条说说的原始数据字典
            target_qq: 目标QQ号
            
        Returns:
            dict or None: 解析后的标准化数据
        """
        try:
            # 提取基本信息
            content = raw_item.get('content', '')
            if not content:
                content = raw_item.get(' concren ', '')  # 备用字段

            # 判断分类
            category = '说说'
            if raw_item.get('type') == 2 or ('转发了' in content or '//@' in content):
                category = '转发'

            # 构造唯一ID（基于内容+时间戳的MD5）
            create_time = raw_item.get('created_time', 0)
            id_str = f"{target_qq}_{create_time}_{content[:100]}"
            unique_id = hashlib.md5(id_str.encode('utf-8')).hexdigest()

            # 提取发布时间
            publish_time_str = self._parse_time(raw_item)
            publish_time_int = create_time or self._convert_time_to_timestamp(publish_time_str)

            # 生成说说URL
            url = f"https://user.qzone.qq.com/{target_qq}"

            # 标准化数据
            parsed = {
                'id': unique_id,
                'source': 'QQ空间',
                'category': category,
                'title': content[:50] if content else '(无内容)',
                'content': content,
                'url': url,
                'author': raw_item.get('name', ''),
                'author_qq': target_qq,
                'publish_time': publish_time_str,
                'publish_time_int': publish_time_int,
                'fetch_time': datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'raw': raw_item  # 保留原始数据以便后续分析
            }

            return parsed

        except Exception as e:
            logger.warning(f"解析说说数据异常：{e}")
            return None

    def _format_cookies(self) -> str:
        """将 cookies 字典格式化为字符串"""
        return '; '.join(f"{k}={v}" for k, v in self.cookies.items())

    @staticmethod
    def _parse_time(raw_item: Dict) -> str:
        """
        从原始数据中提取和标准化时间
        
        支持多种时间格式：
        - Unix时间戳（秒/毫秒）
        - 字符串格式（YYYY-MM-DD HH:MM:SS 等）
        
        Args:
            raw_item: 原始数据项
            
        Returns:
            str: 格式化的时间字符串（YYYY-MM-DD HH:MM:SS）
        """
        time_fields = ['created_time', 'createTime', 'time', 'pubtime']

        for field in time_fields:
            value = raw_item.get(field)
            if value is None:
                continue

            # 时间戳类型
            if isinstance(value, (int, float)):
                # 判断是秒还是毫秒级时间戳
                timestamp = value
                if timestamp > 1e12:  # 毫秒级
                    timestamp = timestamp / 1000
                
                try:
                    dt = datetime.fromtimestamp(timestamp, tz=BEIJING_TZ)
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                except (OSError, ValueError):
                    continue

            # 字符串类型
            elif isinstance(value, str):
                return value  # 直接返回字符串

        return datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def _convert_time_to_timestamp(time_str: str) -> int:
        """
        将时间字符串转换为 Unix 时间戳
        
        Args:
            time_str: 时间字符串
            
        Returns:
            int: Unix 时间戳（秒）
        """
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y/%m/%d %H:%M:%S',
            '%Y-%m-%d',
            '%Y/%m/%d'
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt).replace(tzinfo=BEIJING_TZ)
                return int(dt.timestamp())
            except ValueError:
                continue

        return 0


class DataPersistence:
    """
    数据持久化管理器
    功能：
    - 保存说说数据到JSON文件
    - 支持追加模式
    - 自动去重（基于唯一ID）
    - 读取历史数据
    """

    def __init__(self, file_path: Path = QZONE_DATA_FILE):
        """
        初始化持久化管理器
        
        Args:
            file_path: JSON文件路径
        """
        self.file_path = file_path
        self._ensure_dir_exists()

    def _ensure_dir_exists(self) -> None:
        """确保数据目录存在"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def save_shuos(self, shuos: List[Dict[str, Any]], mode: str = 'append') -> tuple[int, int]:
        """
        保存说说数据到JSON文件
        
        Args:
            shuos: 要保存的说说列表
            mode: 模式，'append' 为追加（去重），'overwrite' 为覆盖
            
        Returns:
            tuple[int, int]: (成功保存的数量, 跳过的重复数量)
        """
        if not shuos:
            logger.warning("没有数据需要保存")
            return 0, 0

        saved_count = 0
        skipped_count = 0

        try:
            if mode == 'append':
                # 追加模式：读取已有数据，去重后合并
                existing_data = self.read_all()
                existing_ids = {item.get('id') for item in existing_data}

                for shuo in shuos:
                    if shuo.get('id') not in existing_ids:
                        existing_data.append(shuo)
                        existing_ids.add(shuo.get('id'))
                        saved_count += 1
                    else:
                        skipped_count += 1

                final_data = existing_data
            else:
                # 覆盖模式
                final_data = shuos
                saved_count = len(shuos)

            # 写入文件
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, ensure_ascii=False, indent=2)

            logger.info(
                f"💾 数据保存成功：新增 {saved_count} 条，"
                f"跳过重复 {skipped_count} 条，"
                f"总计 {len(final_data)} 条"
            )
            return saved_count, skipped_count

        except Exception as e:
            logger.error(f"❌ 保存数据失败：{e}")
            return 0, len(shuos)

    def read_all(self) -> List[Dict[str, Any]]:
        """
        读取所有已保存的说说数据
        
        Returns:
            list[dict]: 所有说说数据列表
        """
        if not self.file_path.exists():
            return []

        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"读取数据失败：{e}")
            return []

    def read_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        读取指定日期的说说数据
        
        Args:
            date_str: 日期字符串（YYYY-MM-DD）
            
        Returns:
            list[dict]: 匹配的说说列表
        """
        all_data = self.read_all()
        return [
            item for item in all_data
            if item.get('publish_time', '').startswith(date_str)
        ]

    def read_recent(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        读取最近N天的说说数据
        
        Args:
            days: 天数，默认7天
            
        Returns:
            list[dict]: 最近的说说列表
        """
        cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=days)).strftime('%Y-%m-%d')
        all_data = self.read_all()

        return [
            item for item in all_data
            if item.get('publish_time', '').replace(' 00:00:00', '').replace(' 00:00', '') >= cutoff
        ]

    def get_latest_timestamp(self, target_qq: str) -> int:
        """
        获取指定QQ号的最新采集时间戳（用于增量采集）
        
        Args:
            target_qq: 目标QQ号
            
        Returns:
            int: 最新时间戳（秒），如果没有记录则返回0
        """
        all_data = self.read_all()
        qq_data = [item for item in all_data if item.get('author_qq') == target_qq]

        if not qq_data:
            return 0

        return max(item.get('publish_time_int', 0) for item in qq_data)


# =============================================================================
# Plan B：使用 requests 直接请求（备选方案）
# =============================================================================

class QzoneScraperPlanB:
    """
    Plan B：基于 requests 的简单采集器
    适用场景：
    - qzone-api 不可用时
    - 只需采集公开说说时
    
    限制：
    - 需要手动提供 Cookie
    - 可能只能获取部分数据
    - 容易触发反爬机制
    """

    def __init__(self, cookies: str = ''):
        """
        初始化备选采集器
        
        Args:
            cookies: 手动提供的 Cookie 字符串
        """
        import requests as req
        self.session = req.Session()
        self.cookies = cookies
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://user.qzone.qq.com/',
            'Accept': 'application/json, text/plain, */*'
        }

    async def login(self, timeout: int = 120) -> Dict[str, Any]:
        """
        Plan B 不支持自动登录，需要手动提供 Cookie
        """
        logger.warning("⚠️ Plan B 不支持自动登录，请通过配置传入 Cookie")
        return {
            'success': False,
            'message': 'Plan B 需要手动提供 Cookie，不支持二维码登录'
        }

    def set_cookies(self, cookies: str) -> None:
        """设置 Cookie"""
        self.cookies = cookies

    async def scrape_public_shuos(self, target_qq: str) -> List[Dict[str, Any]]:
        """
        尝试采集公开说说（基础版本）
        
        注意：此方法可能只能获取有限的公开数据，
        且容易受到腾讯反爬机制的影响。
        """
        logger.info(f"📡 [Plan B] 尝试采集公开说说（目标QQ：{target_qq}）")

        if not self.cookies:
            logger.error("未设置 Cookie，无法采集")
            return []

        import requests as req

        try:
            # 尝试访问移动版 QQ 空间
            url = f'https://m.qzone.cn/{target_qq}'
            headers = {**self.headers, 'Cookie': self.cookies}

            response = req.get(url, headers=headers, timeout=15)
            response.encoding = 'utf-8'

            if response.status_code != 200:
                logger.error(f"请求失败，状态码：{response.status_code}")
                return []

            # TODO: 这里需要根据实际页面结构进行解析
            # 由于移动端页面结构复杂，此处仅返回示例数据
            logger.warning("⚠️ [Plan B] 移动端页面解析尚未完整实现")

            return []

        except Exception as e:
            logger.error(f"[Plan B] 采集失败：{e}")
            return []


# =============================================================================
# Plan C：框架留白（最终兜底）
# =============================================================================

class QzoneScraperPlanC:
    """
    Plan C：模块框架（当 A/B 方案都不可用时的兜底）
    
    用途：
    - 明确标注需要的依赖和环境
    - 提供接口定义供其他模块调用
    - 记录待办事项和环境要求
    
    环境要求：
    - Python 3.8+
    - qzone-api 库（或等效替代品）
    - 网络：能正常访问腾讯服务器（国内直连即可）
    - 可选：VPN/代理（如果网络受限）
    """

    REQUIREMENTS = """
    ## QQ空间采集模块 - 环境要求

    ### 必需依赖
    - **Python**: >= 3.8
    - **qzone-api**: >= 0.0.10（用于 Plan A）
      ```bash
      pip install qzone-api
      ```

    ### 网络要求
    - 能正常访问以下域名：
      - ssl.ptlogin2.qq.com（登录服务）
      - user.qzone.qq.com（QQ空间主站）
      - taotao.qq.com（说说服务）

    ### 可选增强
    - **代理设置**：如果网络受限，可设置 HTTP_PROXY/HTTPS_PROXY
    - **Cookie 导入**：可通过浏览器导出 Cookie 加速登录流程

    ### 当前状态
    - ❌ Plan A (qzone-api): 未安装或运行异常
    - ⚠️ Plan B (requests): 仅支持有限功能
    - ℹ️ Plan C (框架): 接口就绪，等待环境配置

    ### 待办事项
    - [ ] 安装 qzone-api 库
    - [ ] 配置网络环境（如有必要）
    - [ ] 测试登录功能
    - [ ] 验证数据采集效果
    """

    def __init__(self):
        """初始化框架"""
        logger.warning("ℹ️ 当前使用 Plan C（框架留白模式）")
        logger.warning(self.REQUIREMENTS)

    async def login(self, timeout: int = 120) -> Dict[str, Any]:
        """框架占位：返回未实现提示"""
        return {
            'success': False,
            'message': '[Plan C] 功能未实现，请参考文档配置环境'
        }

    async def scrape_all_shuos(self, target_qq: str) -> List[Dict[str, Any]]:
        """框架占位：返回空列表"""
        logger.warning(f"[Plan C] 采集功能未实现（目标QQ：{target_qq}）")
        return []

    async def scrape_new_shuos(self, target_qq: str, since_timestamp: int) -> List[Dict[str, Any]]:
        """框架占位：返回空列表"""
        return []


# =============================================================================
# 统一入口：自动选择最佳方案
# =============================================================================

def get_scraper(plan: str = 'auto') -> object:
    """
    获取QQ空间采集器实例（自动选择最优方案）
    
    Args:
        plan: 强制指定方案 ('A', 'B', 'C', 'auto')
              - 'auto': 自动选择（默认，优先 Plan A）
              
    Returns:
        采集器实例（PlanA/PlanB/PlanC）
        
    选择逻辑：
    1. 如果强制指定 plan，直接返回对应方案
    2. 如果是 auto：
       - qzone-api 可用 → Plan A
       - 否则 → Plan B
       - 都不行 → Plan C（兜底）
    """
    if plan == 'A' or (plan == 'auto' and QZONE_API_AVAILABLE):
        logger.info("🎯 使用 Plan A（qzone-api 方案）")
        return QzoneScraperPlanA()

    elif plan == 'B':
        logger.info("🎯 使用 Plan B（requests 方案）")
        return QzoneScraperPlanB()

    else:
        logger.info("🎯 使用 Plan C（框架兜底方案）")
        return QzoneScraperPlanC()


# =============================================================================
# 便捷函数
# =============================================================================

async def quick_scan(target_qq: str, use_cache: bool = True, fullscan: bool = False) -> List[Dict[str, Any]]:
    """
    快速采集函数（便捷入口）

    Args:
        target_qq: 目标QQ号
        use_cache: 是否优先使用已缓存的 Cookie（默认True）
        fullscan: 是否进行全量扫描（False为增量扫描）

    Returns:
        list[dict]: 采集到的说说列表

    使用示例：
    ```python
    # 快速增量扫描
    shuos = await quick_scan("123456789")

    # 全量扫描
    all_shuos = await quick_scan("123456789", fullscan=True)
    ```
    """
    scraper = get_scraper()

    # 尝试加载缓存的 Cookie
    if use_cache and hasattr(scraper, 'load_saved_cookies'):
        await scraper.load_saved_cookies()

    # 如果未登录，提示用户
    if hasattr(scraper, 'is_logged_in') and not scraper.is_logged_in:
        logger.warning("⚠️ 未登录，请先调用 scraper.login() 或确保有缓存 Cookie")
        return []

    # 执行采集
    persistence = DataPersistence()

    if fullscan:
        # 全量扫描
        shuos = await scraper.scrape_all_shuos(target_qq)
    else:
        # 增量扫描：获取最近7天的新数据
        since_ts = int(time.time()) - 7 * 24 * 3600
        # 如果有历史数据，使用最新的时间戳
        latest_ts = persistence.get_latest_timestamp(target_qq)
        if latest_ts > 0:
            since_ts = latest_ts

        shuos = await scraper.scrape_new_shuos(target_qq, since_ts)

    # 保存数据
    if shuos:
        persistence.save_shuos(shuos, mode='append')

    return shuos


def _load_qq_accounts(config_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    从 config.json 加载 qq_accounts 配置（兼容新旧格式）

    旧格式：["1727559019", "2842943530", ...]
    新格式：[{"qq": "1727559019", "name": "好友A", "enabled": true, "scan_type": "public"}, ...]

    Args:
        config_path: config.json 的路径，默认使用 config_loader 路径

    Returns:
        list[dict]: 统一格式的账号配置列表
    """
    if config_path is None:
        try:
            from config_loader import get_config_path
            config_path = str(get_config_path())
        except ImportError:
            config_path = str(Path(__file__).parent.parent / 'config' / 'config.json')

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"配置文件不存在：{config_path}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"配置文件格式错误：{e}")
        return []

    raw_accounts = config.get('qq_accounts', [])

    # 兼容旧格式（字符串列表 → 自动转换）
    normalized = []
    for item in raw_accounts:
        if isinstance(item, str):
            normalized.append({
                'qq': item,
                'name': '',
                'enabled': True,
                'scan_type': 'public'
            })
        elif isinstance(item, dict):
            normalized.append({
                'qq': str(item.get('qq', '')),
                'name': item.get('name', ''),
                'enabled': item.get('enabled', True),
                'scan_type': item.get('scan_type', 'public')
            })
        else:
            logger.warning(f"跳过未知格式的账号配置项：{item}")

    return normalized


async def batch_scan_from_config(config_path=None, fullscan=False) -> Dict[str, List[Dict[str, Any]]]:
    """从 config.json 读取配置并批量采集多个 QQ 空间

    Args:
        config_path: config.json 的路径，默认使用 config_loader 路径
        fullscan: 是否全量扫描（False 为增量扫描，默认）

    Returns:
        dict: {qq: shuos_list} 的映射
    """
    logger.info("🚀 开始批量采集（从 config.json 配置）")

    # 1. 读取并解析 qq_accounts
    accounts = _load_qq_accounts(config_path)
    if not accounts:
        logger.error("未找到任何 QQ 账号配置")
        return {}

    enabled_accounts = [a for a in accounts if a.get('enabled', True) and a.get('qq')]
    if not enabled_accounts:
        logger.warning("没有启用的 QQ 账号")
        return {}

    logger.info(f"📋 共 {len(enabled_accounts)} 个启用账号待采集")

    # 2. 初始化采集器
    scraper = get_scraper()

    # 3. 自动检测 Cookie
    if hasattr(scraper, 'load_saved_cookies'):
        loaded = await scraper.load_saved_cookies()
        if not loaded:
            logger.warning("⚠️ 未找到缓存 Cookie，正在尝试登录...")
            login_result = await scraper.login(timeout=120)
            if not login_result.get('success'):
                logger.error(f"❌ 登录失败：{login_result.get('message')}，无法采集")
                return {}

    # 4. 逐个采集
    results: Dict[str, List[Dict[str, Any]]] = {}
    persistence = DataPersistence()

    for idx, account in enumerate(enabled_accounts, 1):
        qq = account['qq']
        name = account.get('name', '')
        label = f"{name}({qq})" if name else qq

        logger.info(f"\n{'='*40}")
        logger.info(f"[{idx}/{len(enabled_accounts)}] 采集 {label} ...")
        logger.info(f"{'='*40}")

        try:
            if fullscan:
                shuos = await scraper.scrape_all_shuos(qq)
            else:
                since_ts = int(time.time()) - 7 * 24 * 3600
                latest_ts = persistence.get_latest_timestamp(qq)
                if latest_ts > 0:
                    since_ts = latest_ts
                shuos = await scraper.scrape_new_shuos(qq, since_ts)

            results[qq] = shuos

            # 采集后立即保存
            if shuos:
                saved, dupes = persistence.save_shuos(shuos, mode='append')
                logger.info(f"💾 {label}：新增 {saved} 条，重复 {dupes} 条")
            else:
                logger.info(f"📭 {label}：无新数据")

        except Exception as e:
            logger.error(f"❌ {label} 采集失败：{e}，跳过继续")
            results[qq] = []

        # 账号间间隔，避免请求过快
        if idx < len(enabled_accounts):
            delay = random.uniform(1.0, 3.0)
            logger.info(f"⏳ 等待 {delay:.1f} 秒后继续下一个...")
            await asyncio.sleep(delay)

    # 汇总
    total = sum(len(v) for v in results.values())
    success = sum(1 for v in results.values() if v)
    logger.info(f"\n{'='*40}")
    logger.info(f"✅ 批量采集完成：{success}/{len(enabled_accounts)} 个账号有新数据，共 {total} 条")
    logger.info(f"{'='*40}")

    return results


# =============================================================================
# 主程序：测试和演示
# =============================================================================

async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("  QQ空间动态采集模块 - 测试程序")
    print("=" * 60 + "\n")

    # 显示可用方案
    print(f"📋 当前状态:")
    print(f"  - Plan A (qzone-api): {'✅ 可用' if QZONE_API_AVAILABLE else '❌ 不可用'}")
    print(f"\n💾 数据目录: {DATA_DIR}")
    print(f"📄 数据文件: {QZONE_DATA_FILE}")
    print()

    # 功能选择菜单
    print("请选择操作模式：")
    print("  1. 手动输入QQ号采集")
    print("  2. 从 config.json 批量采集")
    print()
    mode = input("请输入选项（1/2，默认1）：").strip()

    if mode == '2':
        # ========== 从配置批量采集 ==========
        fullscan_input = input("全量扫描？(y/N)：").strip().lower()
        fullscan = fullscan_input == 'y'

        print(f"\n📥 开始批量采集（{'全量' if fullscan else '增量'}模式）...")
        results = await batch_scan_from_config(fullscan=fullscan)

        if results:
            print(f"\n📊 采集汇总：")
            for qq, shuos in results.items():
                status = f"{len(shuos)} 条新数据" if shuos else "无新数据"
                print(f"  - QQ {qq}: {status}")
        else:
            print("\n📭 未能采集到任何数据")
        return

    # ========== 手动输入QQ号采集（原有逻辑） ==========

    # 获取采集器
    scraper = get_scraper()

    # 尝试登录（如果支持）
    if hasattr(scraper, 'is_logged_in'):
        print("\n🔐 步骤 1：登录QQ空间")
        print("-" * 40)
        print("即将生成二维码，请准备手机扫码...\n")

        result = await scraper.login(timeout=120)

        if result.get('success'):
            print(f"\n✅ 登录成功！QQ号：{result.get('qq')}\n")
        else:
            print(f"\n❌ 登录失败：{result.get('message')}")
            print("\n提示：也可以通过浏览器导出 Cookie 后手动导入\n")
            return

    # 演示采集（支持输入多个QQ号，用逗号/空格/中文顿号分隔）
    target_qqs_input = input("\n请输入要采集的QQ号（多个用逗号或空格分隔，按回车跳过）：").strip()

    if target_qqs_input:
        # 支持逗号、中文逗号、顿号、空格分隔
        import re
        target_qqs = re.split(r'[,，、\s]+', target_qqs_input)
        target_qqs = [q.strip() for q in target_qqs if q.strip().isdigit()]

        if target_qqs:
            print(f"\n📥 共 {len(target_qqs)} 个QQ号，开始采集...")
            for idx, target_qq in enumerate(target_qqs, 1):
                print(f"\n{'='*40}")
                print(f"  [{idx}/{len(target_qqs)}] 采集 QQ {target_qq} 的说说...")
                print(f"{'='*40}")

                try:
                    await asyncio.sleep(1)  # 间隔1秒避免请求过快
                    shuos = await scraper.scrape_all_shuos(target_qq)

                    print(f"\n✅ [{idx}/{len(target_qqs)}] QQ {target_qq}：获取 {len(shuos)} 条说说")

                    if shuos:
                        # 显示前2条作为预览
                        print(f"📝 最新说说预览：")
                        for i, shuo in enumerate(shuos[:2], 1):
                            print(f"  [{i}] {shuo.get('publish_time', 'N/A')} - {shuo.get('title', shuo.get('content', 'N/A'))[:50]}...")

                        # 保存数据
                        persistence = DataPersistence()
                        saved, dupes = persistence.save_shuos(shuos)
                        print(f"💾 已保存（新增 {saved} 条，重复 {dupes} 条）")
                except Exception as e:
                    print(f"❌ [{idx}/{len(target_qqs)}] QQ {target_qq} 采集失败：{e}")

            print(f"\n{'='*40}")
            print(f"✅ 全部采集完成！")

    print("\n" + "=" * 60)
    print("  测试结束")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 运行主程序
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
    except Exception as e:
        print(f"\n❌ 程序异常：{e}")
        logging.exception("详细错误信息：")
