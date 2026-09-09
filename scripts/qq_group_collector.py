"""
QQ群消息采集器 - 基于 NapCat OneBot11 API
==========================================

通过 NapCat HTTP API 获取 QQ 群消息历史，标准化存储，供 main.py 和 OCR 模块使用。

配置来源: config.json 中的 napcat 和 qq_groups 字段

使用示例:
    from scripts.qq_group_collector import QQGroupCollector

    collector = QQGroupCollector()

    # 健康检查
    print(collector.health_check())

    # 获取单群最新消息
    msgs = collector.get_group_messages_new("<测试群号>", count=20)
    for msg in msgs:
        print(f"[{msg['sender_name']}] {msg['content'][:50]}")

    # 批量采集
    result = collector.collect_from_groups(categories=["courses"], count=20)
    collector.save_messages(result)
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from config_loader import load_config, get_data_dir

# 延迟导入，避免循环依赖
_secondhand_detector = None


def _get_detector():
    """懒加载 SecondHandDetector"""
    global _secondhand_detector
    if _secondhand_detector is None:
        from scripts.secondhand_detector import SecondHandDetector
        _secondhand_detector = SecondHandDetector()
    return _secondhand_detector

logger = logging.getLogger("HFUT_Monitor.QQGroupCollector")

# 每群请求间隔（秒），避免触发频率限制
REQUEST_INTERVAL = 1.5


class QQGroupCollector:
    """QQ群消息采集器（基于 NapCat OneBot11 API）"""

    def __init__(self, api_url: str = None, token: str = None):
        """初始化，默认从 config.json 读取"""
        config = load_config()
        napcat_cfg = config.get("napcat", {})

        self.api_url = (api_url or napcat_cfg.get("api_url", "http://localhost:3001")).rstrip("/")
        self.token = token or napcat_cfg.get("token", "")
        self.bot_qq = napcat_cfg.get("bot_qq", "")
        self._group_name_cache: dict[int, str] = {}

        # 加载群配置
        self._qq_groups_config = config.get("qq_groups", [])
        for group in self._qq_groups_config:
            gid = int(group["id"])
            self._group_name_cache[gid] = group.get("name", "")

        logger.info(f"QQGroupCollector 初始化完成, api_url={self.api_url}")

    def _call_api(self, action: str, params: dict = None) -> dict:
        """调用 NapCat OneBot11 API"""
        url = f"{self.api_url}/{action}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        payload = params or {}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("retcode", -1) != 0:
                logger.warning(f"API {action} 返回错误: {data.get('retcode')}, msg={data.get('msg', '')}")
                return data

            return data

        except requests.exceptions.ConnectionError:
            logger.error(f"无法连接 NapCat API: {url}")
            return {"retcode": -1, "msg": "连接失败", "data": None}
        except requests.exceptions.Timeout:
            logger.error(f"API {action} 请求超时")
            return {"retcode": -1, "msg": "请求超时", "data": None}
        except requests.exceptions.RequestException as e:
            logger.error(f"API {action} 请求异常: {e}")
            return {"retcode": -1, "msg": str(e), "data": None}

    def health_check(self) -> bool:
        """检查 NapCat 连接"""
        result = self._call_api("get_login_info")
        if result.get("retcode", -1) == 0 and result.get("data"):
            user_id = result["data"].get("user_id", "")
            nickname = result["data"].get("nickname", "")
            logger.info(f"NapCat 连接正常: {nickname}({user_id})")
            return True
        logger.warning(f"NapCat 连接失败: {result.get('msg', '未知错误')}")
        return False

    def get_group_list(self) -> list[dict]:
        """获取机器人加入的所有群"""
        result = self._call_api("get_group_list")
        if result.get("retcode", -1) != 0 or not result.get("data"):
            logger.warning("获取群列表失败")
            return []

        groups = []
        for g in result["data"]:
            groups.append({
                "group_id": g.get("group_id"),
                "group_name": g.get("group_name", ""),
                "member_count": g.get("member_count", 0),
            })
            # 更新缓存
            self._group_name_cache[g["group_id"]] = g.get("group_name", "")

        logger.info(f"获取到 {len(groups)} 个群")
        return groups

    def get_group_messages(self, group_id: int, count: int = 20) -> list[dict]:
        """获取指定群的历史消息

        调用 get_group_msg_history，message_seq=0 获取最新消息。
        NapCat 返回的消息按时间倒序（最新在前），
        需要翻页时用最旧消息的 message_seq 继续请求。
        """
        all_messages = []
        remaining = count
        message_seq = 0  # 0 = 从最新消息开始

        while remaining > 0:
            fetch_count = min(remaining, 20)  # 每次最多取20条
            result = self._call_api("get_group_msg_history", {
                "group_id": group_id,
                "message_seq": message_seq,
                "count": fetch_count,
            })

            if result.get("retcode", -1) != 0 or not result.get("data"):
                logger.debug(f"获取群 {group_id} 消息失败: {result.get('msg', '')}")
                break

            messages = result["data"].get("messages", [])
            if not messages:
                break

            all_messages.extend(messages)
            remaining -= len(messages)

            # 用返回的最后一条（最旧）消息的 message_seq 继续翻页
            last_msg = messages[-1]
            message_seq = last_msg.get("message_seq", 0)

            # 如果返回数量少于请求数，说明已到头
            if len(messages) < fetch_count:
                break

            time.sleep(0.5)

        logger.debug(f"群 {group_id} 获取到 {len(all_messages)} 条原始消息")
        return all_messages

    def get_group_messages_new(self, group_id: int, count: int = 20) -> list[dict]:
        """获取指定群最新消息（从当前时间往前）"""
        raw_messages = self.get_group_messages(group_id, count=count)
        return [self.normalize_message(msg) for msg in raw_messages]

    def _get_group_name(self, group_id: int) -> str:
        """获取群名称，优先用缓存"""
        if group_id in self._group_name_cache:
            return self._group_name_cache[group_id]
        return str(group_id)

    def normalize_message(self, msg: dict) -> dict:
        """标准化消息格式"""
        group_id = msg.get("group_id", 0)
        sender = msg.get("sender", {})
        message_segments = msg.get("message", [])

        # 解析消息段，提取文本、图片等
        content_parts = []
        images = []
        message_type = "unknown"
        reply_to = None

        for segment in message_segments:
            seg_type = segment.get("type", "")
            data = segment.get("data", {})

            if seg_type == "text":
                text = data.get("text", "")
                content_parts.append(text)
                if message_type == "unknown":
                    message_type = "text"

            elif seg_type == "image":
                message_type = "image"
                image_info = {
                    "url": data.get("url", ""),
                    "file": data.get("file", ""),
                }
                images.append(image_info)
                content_parts.append("[图片]")

            elif seg_type == "at":
                qq = data.get("qq", "all")
                name = data.get("name", f"@{qq}")
                content_parts.append(f"@{name}")

            elif seg_type == "face":
                # 表情，跳过
                content_parts.append("[表情]")

            elif seg_type == "reply":
                message_type = "reply"
                reply_id = data.get("id", "")
                reply_to = {"id": reply_id}
                # 回复消息的正文在后续段中，content 由其他段提供

            elif seg_type == "json":
                message_type = "json"
                json_str = data.get("data", "")
                # 尝试解析 JSON（可能是名片、回复等结构化数据）
                try:
                    json_data = json.loads(json_str)
                    if json_data.get("app") == "com.tencent.miniapp_01" or "meta" in json_data:
                        content_parts.append("[小程序/卡片]")
                    else:
                        content_parts.append("[JSON数据]")
                except (json.JSONDecodeError, TypeError):
                    content_parts.append("[JSON数据]")

            elif seg_type == "forward":
                message_type = "forward"
                id_ = data.get("id", "")
                content_parts.append("[合并转发消息]")

            elif seg_type == "file":
                message_type = "file"
                file_name = data.get("file", "")
                content_parts.append(f"[文件] {file_name}")

            elif seg_type == "video":
                message_type = "video"
                content_parts.append("[视频]")

            elif seg_type == "record":
                message_type = "voice"
                content_parts.append("[语音]")

            elif seg_type == "markdown":
                message_type = "markdown"
                content_parts.append(data.get("content", ""))

            else:
                content_parts.append(f"[{seg_type}]")

        content = "".join(content_parts).strip()

        # 如果内容为空但有图片，类型设为 image
        if not content and images:
            message_type = "image"
            content = "[图片]"

        timestamp = msg.get("time", 0)
        try:
            time_str = datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "message_id": msg.get("message_id", ""),
            "message_seq": msg.get("message_seq", 0),
            "group_id": group_id,
            "group_name": self._get_group_name(group_id),
            "sender_id": sender.get("user_id", 0),
            "sender_name": sender.get("nickname", ""),
            "sender_card": sender.get("card", ""),  # 群名片
            "sender_role": sender.get("role", "member"),
            "time": time_str,
            "timestamp": timestamp,
            "message_type": message_type,
            "content": content,
            "images": images,
            "reply_to": reply_to,
            "raw": msg,
        }

    def collect_from_groups(
        self,
        group_ids: list[int] = None,
        count: int = 20,
        categories: list[str] = None,
    ) -> dict[int, list[dict]]:
        """从指定群采集消息

        Args:
            group_ids: 群号列表，None=从config读取所有群
            count: 每群获取消息数
            categories: 只采集指定类别的群（如 ["courses", "class_org"]）

        Returns:
            {group_id: [normalized_messages]}
        """
        if group_ids is None:
            group_ids = [int(g["id"]) for g in self._qq_groups_config]

        # 按 categories 过滤
        if categories:
            category_groups = set()
            for g in self._qq_groups_config:
                if g.get("category", "") in categories:
                    category_groups.add(int(g["id"]))
            group_ids = [gid for gid in group_ids if gid in category_groups]
            logger.info(f"按类别 {categories} 过滤后剩余 {len(group_ids)} 个群")

        results: dict[int, list[dict]] = {}
        success_count = 0
        fail_count = 0

        for i, group_id in enumerate(group_ids):
            group_name = self._get_group_name(group_id)
            logger.info(f"[{i + 1}/{len(group_ids)}] 采集群 {group_name}({group_id})")

            try:
                raw_messages = self.get_group_messages(group_id, count=count)
                if raw_messages:
                    normalized = [self.normalize_message(msg) for msg in raw_messages]
                    results[group_id] = normalized
                    success_count += 1
                    logger.info(f"  获取 {len(normalized)} 条消息")
                else:
                    results[group_id] = []
                    success_count += 1
            except Exception as e:
                fail_count += 1
                logger.error(f"  采集群 {group_name}({group_id}) 失败: {e}")

            # 频率控制
            if i < len(group_ids) - 1:
                time.sleep(REQUEST_INTERVAL)

        logger.info(f"采集完成: 成功 {success_count} 个群, 失败 {fail_count} 个群, "
                     f"共 {sum(len(v) for v in results.values())} 条消息")
        return results

    def full_scan_groups(
        self,
        group_ids: list = None,
        max_messages_per_group: int = 500,
        categories: list[str] = None,
    ) -> dict[int, dict]:
        """全量扫描群消息，自动翻页并标注二手物品

        Args:
            group_ids: 群号列表，None=从config读取所有群
            max_messages_per_group: 每群最多获取消息数
            categories: 只扫描指定类别的群（如 ["general_notice"]）

        Returns:
            {
                group_id: {
                    "messages": [...],           # 标准化消息列表
                    "secondhand_count": int,      # 二手消息数量
                    "secondhand_items": [...],    # 二手检测结果列表
                }
            }
        """
        if group_ids is None:
            group_ids = [int(g["id"]) for g in self._qq_groups_config]

        # 按类别过滤
        if categories:
            group_ids = self.filter_by_category(categories)

        detector = _get_detector()
        results: dict[int, dict] = {}
        success_count = 0
        fail_count = 0
        total_secondhand = 0

        for i, group_id in enumerate(group_ids):
            group_name = self._get_group_name(group_id)
            logger.info(f"[{i + 1}/{len(group_ids)}] 全量扫描群 {group_name}({group_id})")

            try:
                raw_messages = self.get_group_messages(group_id, count=max_messages_per_group)
                if raw_messages:
                    normalized = [self.normalize_message(msg) for msg in raw_messages]
                else:
                    normalized = []

                # 二手检测
                detection_results = detector.detect_batch(normalized)
                secondhand_items = [d for d in detection_results if d is not None]
                secondhand_count = len(secondhand_items)

                results[group_id] = {
                    "messages": normalized,
                    "secondhand_count": secondhand_count,
                    "secondhand_items": secondhand_items,
                }
                success_count += 1
                total_secondhand += secondhand_count
                logger.info(f"  获取 {len(normalized)} 条消息, 发现 {secondhand_count} 条二手")
            except Exception as e:
                fail_count += 1
                logger.error(f"  扫描群 {group_name}({group_id}) 失败: {e}")

            # 频率控制
            if i < len(group_ids) - 1:
                time.sleep(REQUEST_INTERVAL)

        logger.info(f"全量扫描完成: 成功 {success_count} 个群, 失败 {fail_count} 个群, "
                     f"共发现 {total_secondhand} 条二手消息")
        return results

    def scan_new_messages(
        self,
        group_ids: list,
        since_timestamp: int,
        categories: list[str] = None,
    ) -> dict[int, dict]:
        """增量扫描群消息（仅扫描指定时间之后的新消息）

        逐页获取消息直到遇到时间戳 < since_timestamp 的消息为止。

        Args:
            group_ids: 群号列表
            since_timestamp: Unix 时间戳，只扫描此时间之后的消息
            categories: 只扫描指定类别的群

        Returns:
            同 full_scan_groups 的结构
        """
        # 按类别过滤
        if categories:
            group_ids = self.filter_by_category(categories)

        detector = _get_detector()
        results: dict[int, dict] = {}
        success_count = 0
        fail_count = 0
        total_secondhand = 0

        for i, group_id in enumerate(group_ids):
            group_name = self._get_group_name(group_id)
            logger.info(f"[{i + 1}/{len(group_ids)}] 增量扫描群 {group_name}({group_id}), "
                         f"since={datetime.fromtimestamp(since_timestamp)}")

            try:
                new_messages = []
                message_seq = 0  # 从最新开始
                reached_old = False

                while not reached_old:
                    result = self._call_api("get_group_msg_history", {
                        "group_id": group_id,
                        "message_seq": message_seq,
                        "count": 20,
                    })

                    if result.get("retcode", -1) != 0 or not result.get("data"):
                        logger.debug(f"获取群 {group_id} 消息失败: {result.get('msg', '')}")
                        break

                    messages = result["data"].get("messages", [])
                    if not messages:
                        break

                    for msg in messages:
                        msg_ts = int(msg.get("time", 0))
                        if msg_ts > since_timestamp:
                            new_messages.append(msg)
                        else:
                            reached_old = True
                            break

                    if reached_old:
                        break

                    # 用最旧消息的 message_seq 继续翻页
                    last_msg = messages[-1]
                    message_seq = last_msg.get("message_seq", 0)

                    # 返回数量少于请求数，已到头
                    if len(messages) < 20:
                        break

                    time.sleep(0.5)

                normalized = [self.normalize_message(msg) for msg in new_messages]

                # 二手检测
                detection_results = detector.detect_batch(normalized)
                secondhand_items = [d for d in detection_results if d is not None]
                secondhand_count = len(secondhand_items)

                results[group_id] = {
                    "messages": normalized,
                    "secondhand_count": secondhand_count,
                    "secondhand_items": secondhand_items,
                }
                success_count += 1
                total_secondhand += secondhand_count
                logger.info(f"  获取 {len(normalized)} 条新消息, 发现 {secondhand_count} 条二手")
            except Exception as e:
                fail_count += 1
                logger.error(f"  扫描群 {group_name}({group_id}) 失败: {e}")

            # 频率控制
            if i < len(group_ids) - 1:
                time.sleep(REQUEST_INTERVAL)

        logger.info(f"增量扫描完成: 成功 {success_count} 个群, 失败 {fail_count} 个群, "
                     f"共发现 {total_secondhand} 条二手消息")
        return results

    def filter_by_category(self, categories: list[str]) -> list[int]:
        """按类别过滤群 ID 列表

        从配置中的 qq_groups 筛选指定 category 的群。

        Args:
            categories: 类别列表，如 ["general_notice", "courses"]

        Returns:
            匹配的群 ID 列表
        """
        if not categories:
            return [int(g["id"]) for g in self._qq_groups_config]

        matched = []
        for g in self._qq_groups_config:
            if g.get("category", "") in categories:
                matched.append(int(g["id"]))

        category_names = ", ".join(categories)
        logger.info(f"按类别 [{category_names}] 过滤: 匹配到 {len(matched)} 个群")
        return matched

    def save_messages(self, messages_by_group: dict, file_path: str = None) -> None:
        """保存消息到 JSON 文件

        Args:
            messages_by_group: collect_from_groups 的返回值
            file_path: 保存路径，默认 data/qq_group_messages.json
        """
        if file_path is None:
            data_dir = get_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            file_path = str(data_dir / "qq_group_messages.json")

        output = {
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_groups": len(messages_by_group),
            "total_messages": sum(len(v) for v in messages_by_group.values()),
            "groups": {},
        }

        for group_id, messages in messages_by_group.items():
            output["groups"][str(group_id)] = {
                "group_name": self._get_group_name(group_id),
                "count": len(messages),
                "messages": messages,
            }

        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"消息已保存到 {file_path} (共 {output['total_messages']} 条)")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    collector = QQGroupCollector()

    # 健康检查
    print("=" * 50)
    if not collector.health_check():
        print("NapCat 连接失败，请检查配置和服务状态")
        exit(1)

    # 获取群列表
    groups = collector.get_group_list()
    print(f"\n机器人加入 {len(groups)} 个群:")
    for g in groups[:10]:
        print(f"  {g['group_name']} ({g['group_id']}) - {g['member_count']}人")
    if len(groups) > 10:
        print(f"  ... 还有 {len(groups) - 10} 个群")

    # 获取单个群的最新消息
    print("\n" + "=" * 50)
    test_group_id = "<测试群号>"  # 第一个群
    print(f"获取群 {test_group_id} 最新 5 条消息:")
    msgs = collector.get_group_messages_new(test_group_id, count=5)
    for msg in msgs:
        sender = msg.get("sender_card") or msg.get("sender_name")
        content = msg["content"][:60] if msg["content"] else "[无内容]"
        print(f"  [{msg['time']}] {sender}: {content}")
