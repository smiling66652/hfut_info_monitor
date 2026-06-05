"""
QQ群消息采集模块 (QQ Group Chat Scraper)
==========================================

基于 qq-chat-exporter (https://github.com/shuakami/qq-chat-exporter) 实现QQ群消息数据采集。

支持方案（多级退回机制）：
- Plan A: 使用 qq-chat-exporter（推荐，支持全量历史 + 定时备份）
- Plan B: 使用原生 NapCat HTTP API（OneBot协议，仅实时消息）
- Plan C: 框架留白（需要特殊环境时）

依赖环境：
1. NapCatQQ 服务运行中（http://localhost:3001）
2. qq-chat-exporter 已部署（Plan A）或 NapCat API 可用（Plan B）
3. QQ 账号已登录在线

数据输出格式（与系统统一格式兼容）：
{
    'id': '唯一ID (MD5)',
    'source': 'QQ群',
    'category': '群消息',
    'title': '消息摘要(前50字)',
    'content': '完整消息内容',
    'url': '',
    'author': '发送者昵称',
    'author_qq': '发送者QQ号',
    'group_name': '群名称',
    'group_id': '群号',
    'publish_time': '2026-06-04 10:30:00',
    'fetch_time': '2026-06-04 11:00:00',
    'message_type': 'text/image/file/...',
    'raw': {...}
}

使用示例：
    from scripts.qq_group_scraper import QQGroupScraper

    scraper = QQGroupScraper()

    # Plan A: 使用 qq-chat-exporter 全量导出
    messages = scraper.export_group_history("123456789")
    print(f"导出 {len(messages)} 条消息")

    # 设置定时自动备份
    scraper.setup_auto_backup("123456789", interval_hours=24)

    # 解析已有的导出文件
    messages = scraper.parse_exported_data("./data/qq_exports/group_123456789.json")

作者：AI Assistant
创建时间：2026-06-04
版本：v1.0.0
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger("HFUT_Monitor.QQGroupScraper")


class QQChatExporterError(Exception):
    """qq-chat-exporter 相关错误"""
    pass


class NapCatAPIError(Exception):
    """NapCat API 相关错误"""
    pass


class QQGroupScraper:
    """
    QQ群消息采集器

    支持3种采集方案的自动切换：
    - 方案A: qq-chat-exporter（全量历史 + 定时备份）
    - 方案B: NapCat HTTP API（OneBot协议，仅实时）
    - 方案C: 框架留白（待部署）

    Attributes:
        config (dict): 配置信息
        napcat_url (str): NapCat API 地址
        napcat_token (str): NapCat 认证 Token
        qce_url (str): qq-chat-exporter Web UI 地址
        qce_token (str): qq-chat-exporter 认证 Token
        active_plan (str): 当前使用的方案 (A/B/C)
        output_dir (Path): 导出数据存储目录
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化QQ群消息采集器

        Args:
            config_path: 配置文件路径，默认为项目根目录/config/config.json
        """
        self.config = self._load_config(config_path)

        # NapCat 配置（来自 config.json 的 napcat 部分）
        self.napcat_url = self.config.get("napcat", {}).get("api_url", "http://localhost:3001")
        self.napcat_token = self.config.get("napcat", {}).get("token", "")

        # qq-chat-exporter 配置
        self.qce_url = "http://localhost:40653"
        self.qce_token = ""

        # 当前使用的方案
        self.active_plan = "C"  # 默认为C（未初始化），会在setup时确定

        # 输出目录
        base_dir = Path(__file__).parent.parent
        self.output_dir = base_dir / "data" / "qq_exports"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 请求频率控制（QQ风控严格，必须控制频率）
        self.request_interval = 3  # 默认3秒间隔
        self.last_request_time = 0

        logger.info(f"✅ QQGroupScraper 初始化完成")
        logger.info(f"   NapCat API: {self.napcat_url}")
        logger.info(f"   QCE URL: {self.qce_url}")
        logger.info(f"   输出目录: {self.output_dir}")

    def _load_config(self, config_path: Optional[str] = None) -> dict:
        """
        加载配置文件

        Args:
            config_path: 配置文件路径

        Returns:
            dict: 配置字典
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "config.json"

        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)

        logger.warning(f"⚠️ 配置文件不存在: {config_path}，使用默认配置")
        return {}

    def _rate_limit(self) -> None:
        """请求频率控制，避免触发QQ风控"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time

        if elapsed < self.request_interval:
            sleep_time = self.request_interval - elapsed
            logger.debug(f"⏳ 频率控制：等待 {sleep_time:.2f} 秒")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _generate_message_id(self, content: str, timestamp: str) -> str:
        """
        生成消息唯一ID（MD5哈希）

        Args:
            content: 消息内容
            timestamp: 时间戳

        Returns:
            str: MD5格式的唯一ID
        """
        raw_string = f"{content}_{timestamp}"
        return hashlib.md5(raw_string.encode('utf-8')).hexdigest()

    def _normalize_message(self, raw_msg: Dict[str, Any], source: str = "qq_chat_exporter") -> Dict[str, Any]:
        """
        将原始消息转换为系统统一格式

        Args:
            raw_msg: 原始消息字典
            source: 数据来源标识

        Returns:
            dict: 统一格式的消息字典
        """
        try:
            if source == "qq_chat_exporter":
                # qq-chat-exporter JSON 格式解析
                content = raw_msg.get("content", raw_msg.get("text", ""))
                if isinstance(content, list):
                    # 处理复合消息类型（如图片+文字）
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "".join(text_parts)

                publish_time = raw_msg.get("time", raw_msg.get("timestamp", ""))
                if isinstance(publish_time, (int, float)):
                    publish_time = datetime.fromtimestamp(publish_time).strftime("%Y-%m-%d %H:%M:%S")

                message_type = "text"
                if isinstance(raw_msg.get("content"), list):
                    for part in raw_msg["content"]:
                        if part.get("type") in ["image", "face", "at"]:
                            message_type = part["type"]
                            break

                normalized = {
                    'id': self._generate_message_id(str(content), str(publish_time)),
                    'source': 'QQ群',
                    'category': '群消息',
                    'title': content[:50] if content else '',
                    'content': content,
                    'url': '',
                    'author': raw_msg.get("senderNickname", raw_msg.get("sender_name", raw_msg.get("nickname", ""))),
                    'author_qq': str(raw_msg.get("senderUin", raw_msg.get("user_id", raw_msg.get("senderId", "")))),
                    'group_name': raw_msg.get("groupName", raw_msg.get("group_name", "")),
                    'group_id': str(raw_msg.get("groupId", raw_msg.get("group_id", ""))),
                    'publish_time': publish_time,
                    'fetch_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'message_type': message_type,
                    'raw': raw_msg
                }

            elif source == "napcat_api":
                # NapCat OneBot 协议格式解析
                message_type = raw_msg.get("message_type", "unknown")
                content = raw_msg.get("raw_message", raw_msg.get("message", ""))

                normalized = {
                    'id': self._generate_message_id(content, str(raw_msg.get("time", ""))),
                    'source': 'QQ群',
                    'category': '群消息',
                    'title': content[:50] if content else '',
                    'content': content,
                    'url': '',
                    'author': raw_msg.get("sender", {}).get("nickname", raw_msg.get("nickname", "")),
                    'author_qq': str(raw_msg.get("user_id", raw_msg.get("sender_id", ""))),
                    'group_name': raw_msg.get("group_name", ""),
                    'group_id': str(raw_msg.get("group_id", "")),
                    'publish_time': datetime.fromtimestamp(raw_msg.get("time", time.time())).strftime("%Y-%m-%d %H:%M:%S"),
                    'fetch_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'message_type': message_type,
                    'raw': raw_msg
                }
            else:
                raise ValueError(f"未知的数据源: {source}")

            return normalized

        except Exception as e:
            logger.error(f"❌ 消息格式化失败: {e}")
            logger.debug(f"   原始数据: {raw_msg}")
            return {
                'id': hashlib.md5(str(raw_msg).encode()).hexdigest(),
                'source': 'QQ群',
                'category': '群消息',
                'title': '[解析失败]',
                'content': str(raw_msg),
                'url': '',
                'author': '未知',
                'author_qq': '',
                'group_name': '',
                'group_id': '',
                'publish_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'fetch_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'message_type': 'error',
                'raw': raw_msg
            }

    # ==================== Plan A: qq-chat-exporter ====================

    def setup_exporter(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        配置并启动 qq-chat-exporter（Plan A）

        该方法会：
        1. 检测 qq-chat-exporter 是否已安装
        2. 尝试连接到已运行的实例或启动新实例
        3. 验证服务可用性
        4. 获取认证Token

        Args:
            config: 导出器配置（可选）
                - exporter_path: qq-chat-exporter 安装路径
                - headless: 是否无头模式
                - auto_login: 是否自动登录（需要QQ扫码）

        Returns:
            bool: 是否成功启动

        Raises:
            QQChatExporterError: 启动失败时抛出

        环境要求：
            - 已安装 qq-chat-exporter v5.5+
            - NapCat 服务运行中（或使用桌面QQ NT版本）
            - QQ账号可扫码登录
        """
        logger.info("=" * 60)
        logger.info("🚀 [Plan A] 启动 qq-chat-exporter...")
        logger.info("=" * 60)

        config = config or {}
        exporter_path = config.get(
            "exporter_path",
            Path(__file__).parent.parent / "NapCat" / "qq-chat-exporter"
        )

        try:
            # 步骤1: 检查是否已安装
            if not Path(exporter_path).exists():
                logger.warning(f"⚠️ qq-chat-exporter 未安装于: {exporter_path}")
                logger.info("📦 尝试自动检测其他安装位置...")

                # 检测常见安装位置
                possible_paths = [
                    Path(__file__).parent.parent / "NapCat",
                    Path.cwd() / "qq-chat-exporter",
                    Path.home() / "Downloads" / "qq-chat-exporter",
                ]

                found = False
                for path in possible_paths:
                    if path.exists() and ("qce" in str(path).lower() or "launcher" in str(path).lower()):
                        exporter_path = path
                        found = True
                        logger.info(f"✅ 找到安装位置: {path}")
                        break

                if not found:
                    error_msg = (
                        "❌ 未找到 qq-chat-exporter 安装\n"
                        "\n请按照以下步骤安装：\n"
                        "1. 访问 https://github.com/shuakami/qq-chat-exporter/releases\n"
                        "2. 下载最新版本（推荐 v5.5.64+）\n"
                        "3. 解压到项目目录 /NapCat/ 下\n"
                        "4. 运行 launcher-user.bat 启动\n"
                        "5. 用QQ扫码登录\n"
                        "6. 复制控制台输出的Token"
                    )
                    logger.error(error_msg)
                    raise QQChatExporterError(error_msg)

            # 步骤2: 检查服务是否已在运行
            logger.info("🔍 检查 qq-chat-exporter 服务状态...")
            if self._check_qce_service():
                logger.info("✅ qq-chat-exporter 服务已运行")
            else:
                # 尝试启动服务
                logger.info("⏳ 尝试启动 qq-chat-exporter...")

                launcher_script = Path(exporter_path) / "launcher-user.bat"
                standalone_script = Path(exporter_path) / "start-standalone.bat"

                if launcher_script.exists():
                    logger.info(f"   启动脚本: {launcher_script}")

                    if config.get("headless", False):
                        # 无头模式（后台运行）
                        subprocess.Popen(
                            [str(launcher_script)],
                            cwd=str(exporter_path),
                            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    else:
                        # 前台运行（显示控制台，方便扫码）
                        subprocess.Popen(
                            [str(launcher_script)],
                            cwd=str(exporter_path),
                            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
                        )

                    # 等待服务启动
                    logger.info("⏳ 等待服务启动（最长60秒）...")
                    for i in range(12):
                        time.sleep(5)
                        if self._check_qce_service():
                            logger.info(f"✅ 服务启动成功（耗时 {(i+1)*5} 秒）")
                            break
                    else:
                        raise QQChatExporterError("❌ qq-chat-exporter 启动超时（60秒）")

                elif standalone_script.exists():
                    logger.info(f"   使用独立模式启动: {standalone_script}")
                    subprocess.Popen([str(standalone_script)], cwd=str(exporter_path))
                    time.sleep(30)  # 独立模式需要更长时间
                else:
                    raise QQChatExporterError(f"❌ 未找到启动脚本（launcher-user.bat 或 start-standalone.bat）")

            # 步骤3: 获取认证Token
            logger.info("🔑 获取认证Token...")
            self.qce_token = self._get_qce_token()
            if not self.qce_token:
                logger.warning("⚠️ 无法自动获取Token，请手动输入")
                logger.info("   提示：从 qq-chat-exporter 控制台复制Token")
                self.qce_token = input("请输入Token: ").strip()

            # 步骤4: 验证服务可用性
            logger.info("✅ 验证服务可用性...")
            if not self._verify_qce_connection():
                raise QQChatExporterError("❌ qq-chat-exporter 连接验证失败")

            # 标记当前使用的方案
            self.active_plan = "A"
            logger.info("=" * 60)
            logger.info(f"✅ [Plan A] qq-chat-exporter 启动成功！")
            logger.info(f"   Web UI: http://localhost:40653/qce-v4-tool")
            logger.info(f"   Token: {self.qce_token[:8]}..." if len(self.qce_token) > 8 else f"   Token: {self.qce_token}")
            logger.info("=" * 60)

            return True

        except QQChatExporterError:
            raise
        except Exception as e:
            error_msg = f"❌ qq-chat-exporter 启动失败: {e}"
            logger.error(error_msg)
            logger.error("💡 将尝试退回到 Plan B (NapCat HTTP API)")
            raise QQChatExporterError(error_msg)

    def _check_qce_service(self) -> bool:
        """检查 qq-chat-exporter 服务是否在运行"""
        try:
            response = requests.get(
                f"{self.qce_url}/health",
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            # 尝试另一个健康检查端点
            try:
                response = requests.get(
                    f"{self.qce_url}/",
                    timeout=5
                )
                return response.status_code == 200
            except Exception:
                return False

    def _get_qce_token(self) -> str:
        """获取 qq-chat-exporter 的认证Token"""
        # 注意：Token 通常在控制台输出中显示
        # 这里尝试通过API获取（如果支持的话）
        try:
            response = requests.post(
                f"{self.qce_url}/api/auth/token",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("token", "")
        except Exception as e:
            logger.debug(f"   自动获取Token失败: {e}")

        # 尝试从环境变量读取
        env_token = os.environ.get("QCE_TOKEN", "")
        if env_token:
            return env_token

        return ""

    def _verify_qce_connection(self) -> bool:
        """验证 qq-chat-exporter 连接是否正常"""
        try:
            headers = {"Authorization": f"Bearer {self.qce_token}"}
            response = requests.get(
                f"{self.qce_url}/api/status",
                headers=headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"   连接验证失败: {e}")
            return False

    def export_group_history(
        self,
        group_id: str,
        output_format: str = 'json',
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        导出指定群的全部历史消息（Plan A - 推荐）

        通过 qq-chat-exporter 导出群的完整聊天记录，
        支持全量扫描（所有历史消息）和增量扫描（仅新消息）。

        Args:
            group_id: 目标群号（字符串格式，如 "123456789"）
            output_format: 输出格式 ('json' | 'html' | 'txt')
                - json: 结构化JSON（便于程序解析）【推荐】
                - html: 可视化HTML报告（便于人工查看）
                - txt: 纯文本格式
            **kwargs: 其他可选参数
                - start_time: 开始时间（ISO格式，如 "2026-01-01T00:00:00"）
                - end_time: 结束时间（ISO格式）
                - include_images: 是否包含图片（默认True）
                - output_file: 自定义输出文件路径

        Returns:
            list: 统一格式的消息列表

        Raises:
            QQChatExporterError: 导出失败时抛出

        示例：
            >>> scraper = QQGroupScraper()
            >>> scraper.setup_exporter()
            >>> messages = scraper.export_group_history("123456789")
            >>> print(f"导出 {len(messages)} 条消息")
        """
        logger.info("=" * 60)
        logger.info(f"📥 [Plan A] 导出群历史消息: {group_id}")
        logger.info(f"   格式: {output_format.upper()}")
        logger.info("=" * 60)

        # 确保已初始化
        if self.active_plan == "C":
            logger.info("⚠️ 尚未初始化，尝试启动 qq-chat-exporter...")
            try:
                self.setup_exporter()
            except QQChatExporterError as e:
                logger.error(f"❌ Plan A 失败: {e}")
                logger.info("💡 退回到 Plan B (NapCat HTTP API)...")
                return self._export_via_napcat(group_id, **kwargs)

        # 参数校验
        if output_format not in ['json', 'html', 'txt']:
            raise ValueError(f"不支持的输出格式: {output_format}，支持: json/html/txt")

        try:
            # 构建请求参数
            export_params = {
                "groupId": group_id,
                "format": output_format,
                "includeImages": kwargs.get("include_images", True),
                "startTime": kwargs.get("start_time", ""),
                "endTime": kwargs.get("end_time", "")
            }

            # 发送导出请求
            self._rate_limit()
            logger.info(f"   发送导出请求到 {self.qce_url}/api/export ...")

            headers = {
                "Authorization": f"Bearer {self.qce_token}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                f"{self.qce_url}/api/export",
                headers=headers,
                json=export_params,
                timeout=300  # 大量数据可能需要较长时间
            )

            if response.status_code != 200:
                error_text = response.text[:500]
                raise QQChatExporterError(
                    f"导出请求失败 (HTTP {response.status_code}): {error_text}"
                )

            result = response.json()

            # 处理导出结果
            if result.get("status") == "error":
                raise QQChatExporterError(f"导出任务失败: {result.get('message', '未知错误')}")

            # 获取导出数据
            if result.get("downloadUrl"):
                # 从URL下载数据
                self._rate_limit()
                download_response = requests.get(result["downloadUrl"], timeout=120)
                data = download_response.json() if output_format == 'json' else download_response.text
            elif result.get("data"):
                data = result["data"]
            else:
                raise QQChatExporterError("导出结果中没有数据")

            # 保存原始数据
            output_file = kwargs.get("output_file") or \
                          str(self.output_dir / f"group_{group_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{output_format}")

            with open(output_file, "w", encoding="utf-8") as f:
                if isinstance(data, (list, dict)):
                    json.dump(data, f, ensure_ascii=False, indent=2)
                else:
                    f.write(data)

            logger.info(f"   💾 原始数据保存至: {output_file}")

            # 解析并标准化消息
            if isinstance(data, list):
                messages = [self._normalize_message(msg, "qq_chat_exporter") for msg in data]
            elif isinstance(data, dict) and "messages" in data:
                messages = [self._normalize_message(msg, "qq_chat_exporter") for msg in data["messages"]]
            else:
                logger.warning(f"⚠️ 未能识别的数据结构，尝试智能解析...")
                messages = self._smart_parse_exported_data(data, output_format)

            logger.info("=" * 60)
            logger.info(f"✅ [Plan A] 导出完成！共 {len(messages)} 条消息")
            logger.info(f"   群号: {group_id}")
            logger.info(f"   时间范围: {messages[0]['publish_time'] if messages else 'N/A'} ~ {messages[-1]['publish_time'] if messages else 'N/A'}")
            logger.info("=" * 60)

            return messages

        except QQChatExporterError:
            raise
        except requests.exceptions.ConnectionError:
            error_msg = "❌ 无法连接到 qq-chat-exporter 服务"
            logger.error(error_msg)
            logger.error("   请确保 qq-chat-exporter 正在运行 (http://localhost:40653)")
            logger.info("💡 尝试退回到 Plan B...")
            return self._export_via_napcat(group_id, **kwargs)
        except Exception as e:
            error_msg = f"❌ 导出失败: {e}"
            logger.error(error_msg)
            logger.exception(e)
            raise QQChatExporterError(error_msg)

    def setup_auto_backup(
        self,
        group_id: str,
        interval_hours: float = 24,
        **kwargs
    ) -> bool:
        """
        设置定时自动备份（Plan A）

        配置 qq-chat-exporter 的定时备份功能，定期自动导出新的群消息。
        适用于长期监控场景。

        Args:
            group_id: 目标群号
            interval_hours: 备份间隔（小时），默认24小时
            **kwargs: 其他可选参数
                - max_backup_count: 最大备份数量（超出后删除最早的）
                - backup_format: 备份格式 ('json'|'html'|'txt')，默认json
                - notification: 完成后是否通知（需配置推送）

        Returns:
            bool: 是否设置成功

        示例：
            >>> scraper = QQGroupScraper()
            >>> scraper.setup_exporter()
            >>> scraper.setup_auto_backup("123456789", interval_hours=12)
            ✅ 自动备份已设置：每12小时备份一次
        """
        logger.info("=" * 60)
        logger.info(f"⏰ [Plan A] 设置定时自动备份")
        logger.info(f"   群号: {group_id}")
        logger.info(f"   间隔: {interval_hours} 小时")
        logger.info("=" * 60)

        # 确保已初始化
        if self.active_plan == "C":
            try:
                self.setup_exporter()
            except QQChatExporterError as e:
                logger.error(f"❌ 无法启动 qq-chat-exporter: {e}")
                logger.warning("⚠️ 定时备份功能需要 qq-chat-exporter，跳过设置")
                return False

        try:
            # 构建备份计划配置
            backup_config = {
                "groupId": group_id,
                "intervalHours": interval_hours,
                "format": kwargs.get("backup_format", "json"),
                "maxBackups": kwargs.get("max_backup_count", 30),
                "enabled": True,
                "createdAt": datetime.now().isoformat()
            }

            # 发送配置请求
            self._rate_limit()
            headers = {
                "Authorization": f"Bearer {self.qce_token}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                f"{self.qce_url}/api/backup/schedule",
                headers=headers,
                json=backup_config,
                timeout=30
            )

            if response.status_code != 200:
                raise QQChatExporterError(
                    f"设置备份计划失败 (HTTP {response.status_code}): {response.text[:200]}"
                )

            result = response.json()

            if result.get("success"):
                # 保存备份配置到本地
                config_file = self.output_dir / f"backup_config_{group_id}.json"
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(backup_config, f, ensure_ascii=False, indent=2)

                logger.info("=" * 60)
                logger.info(f"✅ [Plan A] 定时备份设置成功！")
                logger.info(f"   群号: {group_id}")
                logger.info(f"   备份间隔: 每 {interval_hours} 小时")
                logger.info(f"   最大保留: {kwargs.get('max_backup_count', 30)} 个备份")
                logger.info(f"   配置文件: {config_file}")
                logger.info("=" * 60)

                return True
            else:
                raise QQChatExporterError(f"备份计划创建失败: {result.get('message', '未知错误')}")

        except QQChatExporterError:
            raise
        except Exception as e:
            logger.error(f"❌ 设置定时备份失败: {e}")
            logger.error("💡 可能的原因:")
            logger.error("   1. qq-chat-exporter 版本不支持定时备份功能")
            logger.error("   2. 权限不足")
            logger.error("   3. 网络问题")
            return False

    # ==================== Plan B: NapCat HTTP API ====================

    def _export_via_napcat(self, group_id: str, **kwargs) -> List[Dict[str, Any]]:
        """
        通过 NapCat HTTP API 导出群消息（Plan B - 备选方案）

        使用 OneBot v11 协议直接调用 NapCat API 获取群消息。
        注意：此方案只能获取**实时消息**（程序启动后的新消息），
        无法获取历史消息。适合作为实时监控的补充。

        Args:
            group_id: 目标群号
            **kwargs: 其他参数（忽略，保持接口一致）

        Returns:
            list: 统一格式的消息列表
        """
        logger.info("=" * 60)
        logger.info(f"📡 [Plan B] 通过 NapCat API 获取群消息: {group_id}")
        logger.info("=" * 60)

        self.active_plan = "B"

        try:
            # 验证 NapCat 连接
            if not self._check_napcat_connection():
                raise NapCatAPIError("无法连接到 NapCat 服务")

            # 获取群消息历史（如果API支持）
            messages = []

            # 方法1: 尝试获取群消息历史记录（部分NapCat版本支持）
            try:
                messages = self._get_group_msg_history(group_id)
            except Exception as e:
                logger.warning(f"⚠️ 获取历史消息失败: {e}")
                logger.info("   Plan B 仅支持实时消息监听，历史消息需要 Plan A (qq-chat-exporter)")

            # 如果没有历史消息，说明只能获取实时消息
            if not messages:
                logger.info("ℹ️ NapCat API 不提供历史消息查询")
                logger.info("   建议：使用 setup_realtime_monitor() 进行实时消息监听")
                logger.info("   或切换到 Plan A (qq-chat-exporter) 以获取全量历史")

            logger.info("=" * 60)
            logger.info(f"✅ [Plan B] 完成，获得 {len(messages)} 条消息")
            logger.info("=" * 60)

            return messages

        except NapCatAPIError:
            raise
        except Exception as e:
            logger.error(f"❌ NapCat API 方案失败: {e}")
            logger.error("💡 所有方案均失败，返回空列表")
            return []

    def _check_napcat_connection(self) -> bool:
        """检查 NapCat API 连接状态"""
        try:
            headers = {"Authorization": f"Bearer {self.napcat_token}"}
            response = requests.get(
                f"{self.napcat_url}/get_login_info",
                headers=headers,
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"   NapCat 连接失败: {e}")
            return False

    def _get_group_msg_history(self, group_id: str) -> List[Dict[str, Any]]:
        """
        获取群消息历史（NapCat API）

        注意：此功能取决于 NapCat 版本和插件支持，
        不是所有版本的 NapCat 都支持获取历史消息。

        Args:
            group_id: 群号

        Returns:
            list: 消息列表
        """
        messages = []

        try:
            headers = {
                "Authorization": f"Bearer {self.napcat_token}",
                "Content-Type": "application/json"
            }

            # 尝试调用 get_group_msg_history 接口（OneBot扩展）
            for page in range(1, 11):  # 最多翻10页
                self._rate_limit()

                response = requests.post(
                    f"{self.napcat_url}/get_group_msg_history",
                    headers=headers,
                    json={
                        "group_id": int(group_id),
                        "message_seq": 0  # 0 表示从最新开始
                    },
                    timeout=30
                )

                if response.status_code != 200:
                    logger.debug(f"   第{page}页请求失败: HTTP {response.status_code}")
                    break

                result = response.json()
                if result.get("status") != "ok":
                    logger.debug(f"   第{page}页返回异常: {result}")
                    break

                page_messages = result.get("data", {}).get("messages", [])
                if not page_messages:
                    break

                for msg in page_messages:
                    normalized = self._normalize_message(msg, "napcat_api")
                    messages.append(normalized)

                logger.info(f"   📄 第{page}页: 获得 {len(page_messages)} 条消息")

                # 检查是否有更多页面
                if len(page_messages) < 20:  # 每页通常20条
                    break

                time.sleep(1)  # 页间延迟

        except Exception as e:
            logger.warning(f"⚠️ 获取群消息历史失败: {e}")
            logger.info("   这可能是因为：")
            logger.info("   1. NapCat 版本不支持此接口")
            logger.info("   2. 需要安装额外的历史消息插件")
            logger.info("   3. API权限不足")

        return messages

    def get_group_info(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        获取群信息（Plan B）

        Args:
            group_id: 群号

        Returns:
            dict: 群信息（群名、成员数等），失败返回None
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.napcat_token}",
                "Content-Type": "application/json"
            }

            self._rate_limit()
            response = requests.post(
                f"{self.napcat_url}/get_group_info",
                headers=headers,
                json={"group_id": int(group_id)},
                timeout=15
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            return None

        except Exception as e:
            logger.error(f"❌ 获取群信息失败: {e}")
            return None

    # ==================== 数据解析工具方法 ====================

    def parse_exported_data(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析已导出的数据文件（通用解析器）

        支持多种来源的导出文件：
        - qq-chat-exporter 导出的 JSON
        - 本模块之前导出的统一格式JSON
        - 手动整理的自定义格式

        Args:
            file_path: 导出文件路径（绝对或相对路径）

        Returns:
            list: 统一格式的消息列表

        示例：
            >>> scraper = QQGroupScraper()
            >>> messages = scraper.parse_exported_data("./data/qq_exports/group_123456789.json")
            >>> for msg in messages[:10]:
            ...     print(f"[{msg['publish_time']}] {msg['author']}: {msg['title']}")
        """
        logger.info(f"📂 解析导出文件: {file_path}")

        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"❌ 文件不存在: {file_path}")
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 自动检测数据格式并选择对应的解析策略
            if isinstance(data, list):
                # 可能是消息列表或统一格式数组
                if data and "id" in data[0]:
                    # 已经是统一格式
                    logger.info(f"   ✅ 检测到统一格式，共 {len(data)} 条")
                    return data
                else:
                    # 原始消息列表
                    logger.info(f"   🔍 检测到原始消息列表，正在标准化...")
                    return [self._normalize_message(msg, "qq_chat_exporter") for msg in data]

            elif isinstance(data, dict):
                # 字典格式，可能是带元数据的导出
                if "messages" in data:
                    # qq-chat-exporter 标准格式
                    logger.info(f"   🔍 检测到 qq-chat-exporter 格式")
                    return [
                        self._normalize_message(msg, "qq_chat_exporter")
                        for msg in data["messages"]
                    ]
                elif "data" in data and isinstance(data["data"], list):
                    # 包装格式
                    logger.info(f"   🔍 检测到包装格式")
                    return [
                        self._normalize_message(msg, "qq_chat_exporter")
                        for msg in data["data"]
                    ]
                elif "chatHistory" in data or "history" in data:
                    # 其他可能的键名
                    key = "chatHistory" if "chatHistory" in data else "history"
                    logger.info(f"   🔍 检测到 '{key}' 键")
                    return [
                        self._normalize_message(msg, "qq_chat_exporter")
                        for msg in data[key]
                    ]
                else:
                    # 单个对象或未知格式
                    logger.warning(f"⚠️ 未知的字典格式，尝试智能解析...")
                    return self._smart_parse_exported_data(data, file_path.suffix.lstrip('.'))

            else:
                logger.error(f"❌ 无法识别的数据类型: {type(data)}")
                return []

        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 解析失败: {e}")
            # 尝试作为纯文本处理（TXT格式）
            return self._parse_txt_format(file_path)
        except Exception as e:
            logger.error(f"❌ 文件解析失败: {e}")
            logger.exception(e)
            return []

    def _smart_parse_exported_data(self, data: Any, format_hint: str = "json") -> List[Dict[str, Any]]:
        """
        智能解析非标准格式的导出数据

        Args:
            data: 原始数据
            format_hint: 格式提示

        Returns:
            list: 消息列表
        """
        messages = []
        logger.debug(f"   智能解析模式，提示格式: {format_hint}")

        if isinstance(data, dict):
            # 遍历字典寻找消息数组
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 0:
                    if isinstance(value[0], dict):
                        # 可能是消息列表
                        sample = value[0]
                        msg_indicators = ['content', 'text', 'message', 'msg', 'sender', 'user']
                        if any(indicator in sample for indicator in msg_indicators):
                            logger.info(f"   ✅ 在 '{key}' 键中发现 {len(value)} 条消息")
                            for item in value:
                                try:
                                    normalized = self._normalize_message(item, "qq_chat_exporter")
                                    messages.append(normalized)
                                except Exception as e:
                                    logger.debug(f"   跳过无效条目: {e}")
                            return messages

        elif isinstance(data, str):
            # 纯文本处理
            lines = data.split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    messages.append({
                        'id': hashlib.md5(line.encode()).hexdigest(),
                        'source': 'QQ群',
                        'category': '群消息',
                        'title': line[:50],
                        'content': line,
                        'url': '',
                        'author': '',
                        'author_qq': '',
                        'group_name': '',
                        'group_id': '',
                        'publish_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'fetch_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'message_type': 'text',
                        'raw': {'raw_text': line}
                    })

        logger.warning(f"⚠️ 智能解析完成，提取 {len(messages)} 条记录（可能不完整）")
        return messages

    def _parse_txt_format(self, file_path: Path) -> List[Dict[str, Any]]:
        """解析 TXT 格式的导出文件"""
        messages = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 尝试常见的 TXT 格式
            # 格式1: [时间] 昵称: 内容
            pattern1 = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.+?):\s*(.*)')

            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line:
                    continue

                match = pattern1.match(line)
                if match:
                    time_str, author, content = match.groups()
                    messages.append({
                        'id': self._generate_message_id(content, time_str),
                        'source': 'QQ群',
                        'category': '群消息',
                        'title': content[:50],
                        'content': content,
                        'url': '',
                        'author': author,
                        'author_qq': '',
                        'group_name': '',
                        'group_id': '',
                        'publish_time': time_str,
                        'fetch_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'message_type': 'text',
                        'raw': {'line_number': line_num, 'raw_line': line}
                    })

            logger.info(f"   ✅ TXT 解析完成，提取 {len(messages)} 条消息")

        except Exception as e:
            logger.error(f"❌ TXT 解析失败: {e}")

        return messages

    # ==================== 批量操作和多群支持 ====================

    def export_multiple_groups(
        self,
        group_ids: List[str],
        output_format: str = 'json',
        **kwargs
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        批量导出多个群的消息（多群支持）

        依次导出每个目标群的历史消息，结果以群号为键的字典返回。

        Args:
            group_ids: 群号列表
            output_format: 输出格式
            **kwargs: 传递给 export_group_history 的额外参数

        Returns:
            dict: {群号: [消息列表]}

        示例：
            >>> scraper = QQGroupScraper()
            >>> scraper.setup_exporter()
            >>> results = scraper.export_multiple_groups(["111222333", "444555666"])
            >>> for gid, msgs in results.items():
            ...     print(f"群 {gid}: {len(msgs)} 条消息")
        """
        logger.info("=" * 60)
        logger.info(f"📦 批量导出 {len(group_ids)} 个群")
        logger.info("=" * 60)

        results = {}
        success_count = 0
        fail_count = 0

        for idx, group_id in enumerate(group_ids, 1):
            logger.info(f"\n[{idx}/{len(group_ids)}] 处理群: {group_id}")

            try:
                messages = self.export_group_history(group_id, output_format=output_format, **kwargs)
                results[group_id] = messages
                success_count += 1
                logger.info(f"   ✅ 成功: {len(messages)} 条消息")

            except Exception as e:
                logger.error(f"   ❌ 失败: {e}")
                results[group_id] = []
                fail_count += 1

            # 群间延迟（避免频繁请求）
            if idx < len(group_ids):
                delay = max(self.request_interval, 5)
                logger.info(f"   ⏳ 等待 {delay} 秒后继续下一个群...")
                time.sleep(delay)

        logger.info("\n" + "=" * 60)
        logger.info(f"✅ 批量导出完成")
        logger.info(f"   成功: {success_count}/{len(group_ids)} 个群")
        logger.info(f"   失败: {fail_count}/{len(group_ids)} 个群")
        logger.info("=" * 60)

        return results

    # ==================== 工具方法和便捷函数 ====================

    def check_environment(self) -> Dict[str, Any]:
        """
        检查运行环境状态

        检查各项依赖服务的可用性，用于诊断部署问题。

        Returns:
            dict: 环境状态报告
                {
                    'napcat_available': bool,
                    'qce_available': bool,
                    'recommended_plan': str,
                    'issues': list[str]
                }
        """
        logger.info("🔍 检查运行环境...")

        status = {
            'timestamp': datetime.now().isoformat(),
            'napcat_available': False,
            'qce_available': False,
            'active_plan': self.active_plan,
            'recommended_plan': 'C',
            'issues': [],
            'recommendations': []
        }

        # 检查 NapCat
        logger.info("   检查 NapCat 服务...")
        status['napcat_available'] = self._check_napcat_connection()
        if status['napcat_available']:
            logger.info("   ✅ NapCat 可用")
        else:
            issue = "❌ NapCat 服务不可用 (http://localhost:3001)"
            status['issues'].append(issue)
            status['recommendations'].append("请启动 NapCat 服务并确认端口 3001 可访问")

        # 检查 qq-chat-exporter
        logger.info("   检查 qq-chat-exporter 服务...")
        status['qce_available'] = self._check_qce_service()
        if status['qce_available']:
            logger.info("   ✅ qq-chat-exporter 可用")
        else:
            issue = "❌ qq-chat-exporter 服务不可用 (http://localhost:40653)"
            status['issues'].append(issue)
            status['recommendations'].append(
                "请安装并启动 qq-chat-exporter: "
                "https://github.com/shuakami/qq-chat-exporter/releases"
            )

        # 确定推荐方案
        if status['qce_available'] and status['napcat_available']:
            status['recommended_plan'] = 'A'
            status['recommendations'].insert(0, "推荐使用 Plan A (qq-chat-exporter) 获取全量历史")
        elif status['napcat_available']:
            status['recommended_plan'] = 'B'
            status['recommendations'].insert(0, "推荐使用 Plan B (NapCat API) 进行实时监控")
        else:
            status['recommended_plan'] = 'C'
            status['recommendations'].insert(0, "⚠️ 环境未就绪，请先部署 NapCat 和/或 qq-chat-exporter")

        # 输出摘要
        logger.info("\n" + "=" * 60)
        logger.info("📊 环境检查结果:")
        logger.info(f"   NapCat: {'✅ 可用' if status['napcat_available'] else '❌ 不可用'}")
        logger.info(f"   QCE: {'✅ 可用' if status['qce_available'] else '❌ 不可用'}")
        logger.info(f"   推荐方案: Plan {status['recommended_plan']}")
        if status['issues']:
            logger.info("\n   发现的问题:")
            for issue in status['issues']:
                logger.info(f"      {issue}")
        logger.info("=" * 60)

        return status

    def get_statistics(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析消息统计信息

        对一组消息进行统计分析，了解群活跃度等指标。

        Args:
            messages: 消息列表（统一格式）

        Returns:
            dict: 统计信息
        """
        if not messages:
            return {
                'total_messages': 0,
                'unique_authors': 0,
                'date_range': None,
                'message_types': {},
                'top_authors': []
            }

        from collections import Counter

        authors = Counter(msg['author'] for msg in messages if msg.get('author'))
        types = Counter(msg.get('message_type', 'unknown') for msg in messages)
        times = [msg.get('publish_time', '') for msg in messages if msg.get('publish_time')]
        times.sort()

        stats = {
            'total_messages': len(messages),
            'unique_authors': len(authors),
            'date_range': (times[0], times[-1]) if times else None,
            'message_types': dict(types.most_common()),
            'top_authors': authors.most_common(10),
            'average_per_day': None
        }

        # 计算日均消息数
        if times and len(times) >= 2:
            try:
                start = datetime.strptime(times[0], "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(times[-1], "%Y-%m-%d %H:%M:%S")
                days = max((end - start).days, 1)
                stats['average_per_day'] = round(len(messages) / days, 2)
            except Exception:
                pass

        return stats


# ==================== 便捷函数 ====================

def quick_export(group_id: str, **kwargs) -> List[Dict[str, Any]]:
    """
    快速导出单个群的消息（便捷函数）

    一行代码即可完成初始化、启动、导出的完整流程。

    Args:
        group_id: 群号
        **kwargs: 传递给 QQGroupScraper.export_group_history 的参数

    Returns:
        list: 统一格式的消息列表

    示例：
        >>> from scripts.qq_group_scraper import quick_export
        >>> messages = quick_export("123456789")
        >>> print(f"导出 {len(messages)} 条消息")
    """
    scraper = QQGroupScraper()
    return scraper.export_group_history(group_id, **kwargs)


def check_scraper_status() -> Dict[str, Any]:
    """
    检查采集模块状态（便捷函数）

    Returns:
        dict: 环境状态报告
    """
    scraper = QQGroupScraper()
    return scraper.check_environment()


# ==================== 主程序入口 ====================

if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    print("=" * 70)
    print("  QQ 群消息采集模块 - 测试程序")
    print("  QQ Group Chat Scraper - Test")
    print("=" * 70)
    print()

    # 创建采集器实例
    scraper = QQGroupScraper()
    print(f"✅ 采集器初始化成功")
    print()

    # 步骤1: 环境检查
    print("[步骤1] 检查运行环境...")
    env_status = scraper.check_environment()
    print(f"   推荐方案: Plan {env_status['recommended_plan']}")
    print()

    # 步骤2: 如果环境就绪，进行测试导出
    if env_status['recommended_plan'] in ['A', 'B']:
        # 从配置读取测试用的群号（如果有）
        test_group_id = input("请输入测试群号（留空则跳过）: ").strip()

        if test_group_id:
            print(f"\n[步骤2] 测试导出群: {test_group_id}")

            try:
                if env_status['recommended_plan'] == 'A':
                    # Plan A: 使用 qq-chat-exporter
                    print("   使用 Plan A (qq-chat-exporter)...")
                    messages = scraper.export_group_history(test_group_id, output_format='json')

                else:
                    # Plan B: 使用 NapCat API
                    print("   使用 Plan B (NapCat API)...")
                    messages = scraper._export_via_napcat(test_group_id)

                print(f"\n   ✅ 导出成功！共 {len(messages)} 条消息")

                if messages:
                    # 显示前5条消息预览
                    print("\n   前5条消息预览:")
                    print("   " + "-" * 60)
                    for i, msg in enumerate(messages[:5], 1):
                        print(f"   [{i}] [{msg['publish_time']}] {msg['author']}: {msg['title'][:40]}")
                    print("   " + "-" * 60)

                    # 显示统计信息
                    stats = scraper.get_statistics(messages)
                    print(f"\n   📊 统计信息:")
                    print(f"      总消息数: {stats['total_messages']}")
                    print(f"      不同作者: {stats['unique_authors']} 人")
                    if stats['average_per_day']:
                        print(f"      日均消息: {stats['average_per_day']} 条/天")
                    if stats['top_authors']:
                        print(f"      最活跃用户: {stats['top_authors'][0][0]} ({stats['top_authors'][0][1]} 条)")

            except Exception as e:
                print(f"\n   ❌ 导出失败: {e}")
                print("   请检查：")
                print("   1. NapCat 服务是否正在运行")
                print("   2. qq-chat-exporter 是否正确安装和启动")
                print("   3. QQ账号是否已登录")
                print("   4. 网络连接是否正常")
    else:
        print("\n[步骤2] 环境未就绪，跳过测试导出")
        print("\n   请先完成以下部署工作：")
        print("   1. 安装并启动 NapCat (http://localhost:3001)")
        print("   2. 安装 qq-chat-exporter (https://github.com/shuakami/qq-chat-exporter)")
        print("   3. 用QQ扫码登录")
        print("\n   详细部署指南请参考代码中的文档注释。")

    print("\n" + "=" * 70)
    print("  测试程序结束")
    print("=" * 70)
