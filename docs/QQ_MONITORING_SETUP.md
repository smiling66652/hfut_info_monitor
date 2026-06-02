# QQ 监控搭建指南（QQ空间 + QQ群）

> 基于 NapCat + requests 的 QQ 信息监控完整方案
> 更新时间：2026-05-17

---

## 目录

1. [QQ空间爬虫方案](#1-qq空间爬虫方案)
2. [QQ群聊监控（NapCat 扩展）](#2-qq群聊监控napcat-扩展)
3. [NapCat 配置检查](#3-napcat-配置检查)
4. [Python 实现示例](#4-python-实现示例)
5. [安全建议](#5-安全建议)

---

## 1. QQ空间爬虫方案

### 1.1 技术栈

```
技术方案：Cookie + requests 直接请求 QQ 空间 API
运行环境：Python 3.9+
核心依赖：requests, hashlib, sqlite3
```

**依赖安装**

```bash
pip install requests feedparser
```

### 1.2 核心原理

QQ 空间说说接口为 HTTP API，通过 Cookie 鉴权。关键参数：

| 参数 | 说明 |
|------|------|
| `skey` | Cookie 中的认证密钥，用于计算 `g_tk` |
| `g_tk` | 基于 skey 计算的 token，必须通过校验 |
| `p_uin` | 当前登录用户的 QQ 号 |
| `uin` | 目标 QQ 号（要监控的账号） |

### 1.3 G_TK 算法实现

```python
# utils/gtk_calc.py
import hashlib

def calc_gtk(skey: str) -> str:
    """
    计算 QQ 空间 g_tk 值
    skey 从 Cookie 中提取：查找 Cookie 字符串中的 "skey=xxxxx" 部分
    """
    hash_val = 5381
    for char in skey:
        hash_val += (hash_val << 5) + ord(char)
        hash_val &= 0x7FFFFFFF  # 保留 31 位
    return str(hash_val)


def extract_skey_from_cookie(cookie: str) -> str:
    """从完整 Cookie 字符串中提取 skey"""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("skey="):
            return part[5:]
    raise ValueError("Cookie 中未找到 skey，请检查 Cookie 是否有效")
```

### 1.4 说说获取完整实现

```python
# fetcher/qzone_crawler.py
import requests
import time
import random
import json
import sqlite3
from datetime import datetime
from utils.gtk_calc import calc_gtk, extract_skey_from_cookie


class QzoneCrawler:
    """QQ空间说说爬虫"""

    SHUOSHUO_API = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"

    def __init__(self, db_path: str = "data/monitor.db"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })

    def _init_db(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS qzone_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT DEFAULT 'qzone',
                target_qq TEXT NOT NULL,
                post_id TEXT UNIQUE,
                nickname TEXT,
                content TEXT,
                attach_type TEXT,       -- 图片/转发/纯文字
                image_urls TEXT,        -- JSON 数组
                source_url TEXT,
                pub_time DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.db.commit()

    def _random_delay(self, min_sec: float = 5, max_sec: float = 15):
        """随机延迟，模拟人工浏览"""
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def fetch_shuoshuo(self, target_qq: str, cookie: str, pos: int = 0, num: int = 20) -> dict:
        """
        获取指定 QQ 的说说列表

        Args:
            target_qq: 目标 QQ 号
            cookie: 完整的 QQ Cookie 字符串
            pos: 起始位置（翻页用）
            num: 每页条数

        Returns:
            包含 msglist 和 has_more 的字典
        """
        skey = extract_skey_from_cookie(cookie)
        g_tk = calc_gtk(skey)

        params = {
            "uin": target_qq,
            "ftype": 0,
            "sort": 0,          # 0=按时间倒序
            "pos": pos,
            "num": num,
            "replynum": 0,
            "g_tk": g_tk,
            "callback": "_preloadCallback",
            "code_version": 1,
            "format": "jsonp",
            "need_private_comment": 1,
        }

        headers = {
            "Cookie": cookie,
            "Referer": f"https://user.qzone.qq.com/{target_qq}",
            "Origin": "https://user.qzone.qq.com",
        }

        try:
            resp = self.session.get(
                self.SHUOSHUO_API,
                params=params,
                headers=headers,
                timeout=15
            )

            if resp.status_code != 200:
                return {"msglist": [], "has_more": False, "error": f"HTTP {resp.status_code}"}

            # 去掉 JSONP 回调包装
            text = resp.text
            if text.startswith("_preloadCallback("):
                text = text[len("_preloadCallback("):-2]

            data = json.loads(text)

            if data.get("ret") != 0:
                return {"msglist": [], "has_more": False, "error": f"ret={data.get('ret')}, msg={data.get('msg')}"}

            return {
                "msglist": data.get("msglist", []),
                "has_more": data.get("has_more", False),
                "error": None
            }

        except requests.exceptions.Timeout:
            return {"msglist": [], "has_more": False, "error": "请求超时"}
        except json.JSONDecodeError:
            return {"msglist": [], "has_more": False, "error": "JSON 解析失败"}
        except Exception as e:
            return {"msglist": [], "has_more": False, "error": str(e)}

    def parse_and_save(self, target_qq: str, msglist: list):
        """解析说说列表并保存到数据库"""
        saved_count = 0
        for msg in msglist:
            # 提取图片
            image_urls = []
            if "image" in msg and msg["image"]:
                image_urls = [img.get("url1", "") for img in msg["image"] if img.get("url1")]

            # 提取转发内容
            content = msg.get("content", "")
            if msg.get("name") == "转发说说":
                fwd = msg.get("conlist", [{}])[0] if msg.get("conlist") else {}
                content = f"{content}\n---转发---\n{fwd.get('content', '')}"

            post_id = str(msg.get("tid", "") or msg.get("conlist", [{}])[0].get("tid", ""))

            try:
                self.db.execute(
                    """INSERT OR IGNORE INTO qzone_posts
                       (target_qq, post_id, nickname, content, attach_type, image_urls, pub_time)
                       VALUES (?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))""",
                    (
                        target_qq,
                        post_id,
                        msg.get("name", ""),
                        content,
                        msg.get("attach", "普通说说"),
                        json.dumps(image_urls, ensure_ascii=False),
                        msg.get("created_time", 0)
                    )
                )
                if self.db.total_changes:
                    saved_count += 1
            except Exception as e:
                print(f"  保存失败: {e}")

        self.db.commit()
        return saved_count

    def fetch_user(self, target_qq: str, cookie: str):
        """抓取单个用户的全部说说（自动翻页）"""
        print(f"[{target_qq}] 开始抓取...")
        all_count = 0
        pos = 0

        while True:
            result = self.fetch_shuoshuo(target_qq, cookie, pos=pos)
            if result["error"]:
                print(f"  [错误] {result['error']}")
                break

            msglist = result["msglist"]
            if not msglist:
                print(f"  无更多说说，共获取 {all_count} 条")
                break

            saved = self.parse_and_save(target_qq, msglist)
            all_count += len(msglist)
            pos += len(msglist)
            print(f"  第 {pos} 条，新增 {saved} 条，总计 {all_count}")

            if not result["has_more"]:
                print(f"  已到底，共 {all_count} 条")
                break

            self._random_delay()  # 翻页间隔

        return all_count

    def fetch_all_targets(self, targets: list, cookie: str):
        """抓取所有目标账号"""
        total = 0
        for qq in targets:
            count = self.fetch_user(qq, cookie)
            total += count
            self._random_delay(15, 30)  # 账号之间大间隔
        print(f"\n全部完成，共获取 {total} 条说说")
        return total


# === 使用示例 ===
if __name__ == "__main__":
    TARGET_QQ_ACCOUNTS = [
        "1727559019", "2842943530", "2794573164", "1576711825", "823413788",
        "3290029079", "3234986841", "2075199041", "3107961334"
    ]

    # 替换为你的真实 Cookie（从浏览器登录 QQ 空间后获取）
    COOKIE = "你的QQ空间Cookie"

    crawler = QzoneCrawler()
    crawler.fetch_all_targets(TARGET_QQ_ACCOUNTS, COOKIE)
```

### 1.5 如何获取 Cookie

```
1. 打开 Chrome，访问 https://qzone.qq.com
2. 登录你的 QQ 账号
3. 按 F12 → Network 面板
4. 刷新页面，找到任意请求
5. 在 Request Headers 中复制完整的 Cookie 值
6. 注意 Cookie 中必须包含 skey 字段
```

### 1.6 风险说明

> **QQ空间爬虫属于高风险操作，腾讯有严格的反爬机制**

| 风险等级 | 说明 |
|----------|------|
| **封号** | 短时间大量请求会触发风控，轻则冻结空间，重则封号 |
| **Cookie 失效** | QQ 登态通常 24-48h 过期，需定期刷新 |
| **滑块验证** | 异常请求会弹出滑块验证码，需人工处理 |
| **法律风险** | 爬取他人隐私信息可能涉及法律问题 |

**建议：只用小号操作，绝不使用主号。**

### 1.7 替代方案选项

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **NapCat 消息转发**（推荐） | 官方协议封装，封号风险低 | 仅能收到主动分享的内容 | ★★★★★ |
| **QQ空间 RSS** | 部分账号空间开放 RSS | 大部分人关闭了空间 | ★★☆☆☆ |
| **Selenium 模拟登录** | 可处理验证码 | 资源消耗大，仍会被检测 | ★★★☆☆ |
| **手动定期查看** | 零风险 | 效率极低 | ★☆☆☆☆ |

**最佳方案：使用 NapCat 让目标 QQ 好友通过「转发到机器人」的方式主动分享内容，避免爬虫。**

---

## 2. QQ群聊监控（NapCat 扩展）

### 2.1 NapCat 简介

NapCat 是基于 NTQQ 的无头 QQ 机器人框架，实现了 OneBot v11 协议。它直接调用 QQ 客户端协议，比传统的 go-cqhttp 更稳定。

**核心优势：**
- 支持最新的 NTQQ（QQNT）协议
- OneBot v11 标准接口，生态成熟
- 支持正向 WebSocket / 反向 WebSocket / HTTP
- 消息收发稳定，被动监听基本无风险

### 2.2 安装 NapCat

```bash
# 方式一：Docker 部署（推荐）
docker run -d \
  --name napcat \
  --restart always \
  -p 6099:6099 \
  -p 3000:3000 \
  -v napcat-data:/app/napcat/config \
  mlikiowa/napcat-docker:latest

# 方式二：Windows 直接安装
# 1. 下载 QQNT 客户端（最新版）
# 2. 下载 NapCat 安装包：https://github.com/NapNeko/NapCatQQ/releases
# 3. 解压后运行 NapCat.Shell.exe
# 4. 扫码登录
```

### 2.3 开启消息存档

NapCat 通过 WebSocket 推送消息事件，无需额外开启"存档"功能。只需配置 WebSocket 连接即可接收所有群消息。

### 2.4 OneBot API 消息监听

NapCat 实现了完整的 OneBot v11 协议，核心事件类型：

```
群消息事件：
├── message.group.normal      — 普通群消息
├── message.group.anonymous   — 匿名消息
└── notice.group_increase     — 有人入群
    notice.group_decrease     — 有人退群
    notice.group_ban          — 禁言通知

私聊消息事件：
├── message.private.friend    — 好友私聊
└── message.private.group     — 临时会话
```

---

## 3. NapCat 配置检查

### 3.1 当前状态

经检查，当前系统（MateBook E 2022）上 **未发现已运行的 NapCat 实例**。

- 无 Docker 中的 NapCat 容器
- 无本地 NapCat 安装目录
- 仅有 `/c/Users/Matebook/AppData/Local/Temp/napcat-plugin-uploads/` 空目录（历史残留）

**结论：需要从头安装部署 NapCat。**

### 3.2 推荐部署配置

```jsonc
// napcat/config/onebot11.json
{
    "http": {
        "enable": true,
        "host": "0.0.0.0",
        "port": 3000,
        "secret": "你的自定义密钥",
        "enableCors": true,
        "debug": false
    },
    "ws": {
        "enable": true,
        "host": "0.0.0.0",
        "port": 3001
    },
    "reverseWs": {
        "enable": true,
        "urls": [
            "ws://localhost:8080/ws"
        ]
    },
    "groupMessage": {
        "ignoreSelf": true     // 忽略自身发送的消息
    },
    "friendMessage": {
        "ignoreSelf": true
    },
    "heartInterval": 30000,
    "messagePostFormat": "array",   // 消息格式：array（推荐）或 string
    "reportSelfMessage": false
}
```

### 3.3 OneBot v11 API 完整列表

以下是监控场景常用的 API：

#### 消息相关

| API | 方法 | 说明 |
|-----|------|------|
| `send_group_msg` | POST | 发送群消息 |
| `send_private_msg` | POST | 发送私聊消息 |
| `send_group_forward_msg` | POST | 发送群合并转发 |
| `get_msg` | POST | 获取单条消息 |
| `get_group_msg_history` | POST | 获取群历史消息（需 VIP 或群主/管理员） |
| `delete_msg` | POST | 撤回消息 |

#### 群相关

| API | 方法 | 说明 |
|-----|------|------|
| `get_group_list` | POST | 获取群列表 |
| `get_group_info` | POST | 获取群信息 |
| `get_group_member_list` | POST | 获取群成员列表 |
| `get_group_member_info` | POST | 获取群成员信息 |
| `set_group_ban` | POST | 群禁言 |
| `set_group_whole_ban` | POST | 群全员禁言 |
| `set_group_kick` | POST | 踢出群成员 |

#### 好友相关

| API | 方法 | 说明 |
|-----|------|------|
| `get_friend_list` | POST | 获取好友列表 |
| `get_friend_info` | POST | 获取好友信息 |

#### 登录相关

| API | 方法 | 说明 |
|-----|------|------|
| `get_login_info` | POST | 获取登录号信息 |
| `get_status` | POST | 获取运行状态 |

#### 被动监听（WebSocket 事件）

无需调用 API，NapCat 通过 WebSocket 主动推送以下事件：

```jsonc
// 群消息事件格式
{
    "post_type": "message",
    "message_type": "group",
    "sub_type": "normal",
    "group_id": 123456789,
    "user_id": 987654321,
    "anonymous": null,
    "message": [
        {"type": "text", "data": {"text": "消息内容"}},
        {"type": "image", "data": {"file": "xxx.jpg", "url": "https://..."}}
    ],
    "raw_message": "消息内容",
    "sender": {
        "user_id": 987654321,
        "nickname": "昵称",
        "card": "群名片",
        "role": "member"       // owner / admin / member
    },
    "time": 1716000000,
    "self_id": 111111111,
    "message_id": 12345
}
```

### 3.4 验证 NapCat 是否正常运行

```bash
# 检查 HTTP API 是否可用
curl http://localhost:3000/get_login_info

# 期望返回：
# {"data":{"user_id":123456,"nickname":"QQ昵称"},"retcode":0,"status":"ok"}

# 获取群列表
curl http://localhost:3000/get_group_list

# 获取指定群成员
curl -X POST http://localhost:3000/get_group_member_list \
  -H "Content-Type: application/json" \
  -d '{"group_id": 123456789}'
```

---

## 4. Python 实现示例

### 4.1 群消息监听模板（WebSocket 被动接收）

```python
# fetcher/qq_group_listener.py
import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    exit(1)


class QQGroupMonitor:
    """通过 NapCat WebSocket 被动监听群消息"""

    def __init__(
        self,
        ws_url: str = "ws://localhost:3001",
        db_path: str = "data/monitor.db",
        monitored_groups: list = None
    ):
        self.ws_url = ws_url
        self.db_path = db_path
        self.monitored_groups = set(monitored_groups or [])
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS qq_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT DEFAULT 'qqgroup',
                group_id TEXT NOT NULL,
                group_name TEXT,
                user_id TEXT NOT NULL,
                nickname TEXT,
                card TEXT,
                role TEXT,
                raw_message TEXT,
                message_json TEXT,
                message_id INTEGER,
                message_type TEXT,
                timestamp INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_qq_group
                ON qq_messages(group_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_qq_user
                ON qq_messages(user_id);
        """)
        self.db.commit()

    def _save_message(self, event: dict):
        """将群消息保存到数据库"""
        try:
            self.db.execute(
                """INSERT INTO qq_messages
                   (group_id, group_name, user_id, nickname, card, role,
                    raw_message, message_json, message_id, message_type, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(event.get("group_id", "")),
                    event.get("group_name", ""),
                    str(event.get("user_id", "")),
                    event.get("sender", {}).get("nickname", ""),
                    event.get("sender", {}).get("card", ""),
                    event.get("sender", {}).get("role", ""),
                    event.get("raw_message", ""),
                    json.dumps(event.get("message", []), ensure_ascii=False),
                    event.get("message_id", 0),
                    event.get("message_type", "group"),
                    event.get("time", 0)
                )
            )
            self.db.commit()
            return True
        except Exception as e:
            print(f"[DB Error] {e}")
            return False

    def _should_monitor(self, group_id: int) -> bool:
        """判断是否需要监控该群（空列表=监控所有群）"""
        if not self.monitored_groups:
            return True
        return group_id in self.monitored_groups

    def _extract_text(self, message: list) -> str:
        """从消息段数组中提取纯文本"""
        texts = []
        for seg in message:
            if seg.get("type") == "text":
                texts.append(seg["data"].get("text", ""))
        return "".join(texts).strip()

    def _print_message(self, event: dict):
        """在终端格式化输出消息"""
        sender = event.get("sender", {})
        group_id = event.get("group_id")
        nickname = sender.get("card") or sender.get("nickname", "未知")
        text = self._extract_text(event.get("message", []))
        msg_time = datetime.fromtimestamp(event.get("time", 0)).strftime("%H:%M:%S")

        print(f"[{msg_time}] [群:{group_id}] {nickname}: {text[:100]}")

    async def _handle_event(self, raw_data: str):
        """处理 WebSocket 收到的事件"""
        try:
            event = json.loads(raw_data)

            # 只处理群消息
            if event.get("post_type") != "message":
                return
            if event.get("message_type") != "group":
                return

            group_id = event.get("group_id")
            if not self._should_monitor(group_id):
                return

            # 保存到数据库
            self._save_message(event)
            # 终端输出
            self._print_message(event)

        except json.JSONDecodeError:
            pass

    async def start(self):
        """启动 WebSocket 监听"""
        print(f"正在连接 NapCat WebSocket: {self.ws_url}")

        # 如果指定了监控群，显示列表
        if self.monitored_groups:
            print(f"监控群列表: {list(self.monitored_groups)}")
        else:
            print("监控所有群（未指定群列表）")

        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    print("已连接，开始监听群消息...")
                    async for raw_data in ws:
                        await self._handle_event(raw_data)
            except Exception as e:
                print(f"[连接断开] {e}，5 秒后重连...")
                await asyncio.sleep(5)


# === 使用示例 ===
if __name__ == "__main__":
    # 配置要监控的群号（留空列表则监控机器人加入的所有群）
    MONITORED_GROUPS = [
        # "123456789",   # 示例：替换为实际群号
    ]

    monitor = QQGroupMonitor(
        ws_url="ws://localhost:3001",
        monitored_groups=MONITORED_GROUPS
    )

    asyncio.run(monitor.start())
```

### 4.2 消息存储与查询工具

```python
# fetcher/message_store.py
import sqlite3
from datetime import datetime, timedelta
from typing import Optional


class MessageStore:
    """消息存储与查询"""

    def __init__(self, db_path: str = "data/monitor.db"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def query_group_messages(
        self,
        group_id: str,
        hours: int = 24,
        keyword: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100
    ) -> list:
        """
        查询群消息

        Args:
            group_id: 群号
            hours: 查询最近几小时的消息
            keyword: 关键词过滤
            user_id: 指定用户
            limit: 返回条数
        """
        since = int((datetime.now() - timedelta(hours=hours)).timestamp())

        sql = """
            SELECT * FROM qq_messages
            WHERE group_id = ? AND timestamp > ?
        """
        params = [group_id, since]

        if keyword:
            sql += " AND raw_message LIKE ?"
            params.append(f"%{keyword}%")

        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def get_active_users(self, group_id: str, hours: int = 24) -> list:
        """获取群内活跃用户排行"""
        since = int((datetime.now() - timedelta(hours=hours)).timestamp())
        sql = """
            SELECT user_id, nickname, card, COUNT(*) as msg_count
            FROM qq_messages
            WHERE group_id = ? AND timestamp > ?
            GROUP BY user_id
            ORDER BY msg_count DESC
            LIMIT 20
        """
        return [dict(row) for row in self.db.execute(sql, [group_id, since]).fetchall()]

    def get_all_sources_summary(self, hours: int = 24) -> dict:
        """获取所有信息源的摘要统计"""
        since = int((datetime.now() - timedelta(hours=hours)).timestamp())

        # QQ 群消息统计
        group_stats = self.db.execute("""
            SELECT group_id, COUNT(*) as count
            FROM qq_messages WHERE timestamp > ?
            GROUP BY group_id ORDER BY count DESC
        """, [since]).fetchall()

        # QQ 空间说说统计
        qzone_stats = self.db.execute("""
            SELECT target_qq, COUNT(*) as count
            FROM qzone_posts WHERE created_at >= datetime('now', ?)
            GROUP BY target_qq ORDER BY count DESC
        """, [f"-{hours} hours"]).fetchall()

        return {
            "period": f"最近 {hours} 小时",
            "qq_groups": [dict(r) for r in group_stats],
            "qzone_posts": [dict(r) for r in qzone_stats],
        }

    def search_all_messages(self, keyword: str, limit: int = 50) -> list:
        """全文搜索所有来源的消息"""
        # 搜索群消息
        group_msgs = self.db.execute("""
            SELECT 'qqgroup' as source, group_id, nickname, raw_message, timestamp
            FROM qq_messages
            WHERE raw_message LIKE ?
            ORDER BY timestamp DESC LIMIT ?
        """, [f"%{keyword}%", limit]).fetchall()

        # 搜索空间说说
        qzone_msgs = self.db.execute("""
            SELECT 'qzone' as source, target_qq as group_id, nickname, content as raw_message,
                   strftime('%s', pub_time) as timestamp
            FROM qzone_posts
            WHERE content LIKE ?
            ORDER BY pub_time DESC LIMIT ?
        """, [f"%{keyword}%", limit]).fetchall()

        all_msgs = [dict(r) for r in group_msgs] + [dict(r) for r in qzone_msgs]
        all_msgs.sort(key=lambda x: int(x.get("timestamp", 0)), reverse=True)
        return all_msgs[:limit]
```

### 4.3 消息推送（通过 NapCat 发送）

```python
# pusher/qq_push.py
import requests
import json


class QQNotifier:
    """通过 NapCat OneBot API 发送 QQ 消息"""

    def __init__(self, napcat_http: str = "http://localhost:3000"):
        self.base_url = napcat_http.rstrip("/")

    def send_private_msg(self, user_id: str, message: str) -> dict:
        """发送私聊消息"""
        resp = requests.post(f"{self.base_url}/send_private_msg", json={
            "user_id": int(user_id),
            "message": message
        }, timeout=10)
        return resp.json()

    def send_group_msg(self, group_id: str, message: str) -> dict:
        """发送群消息"""
        resp = requests.post(f"{self.base_url}/send_group_msg", json={
            "group_id": int(group_id),
            "message": message
        }, timeout=10)
        return resp.json()

    def get_login_info(self) -> dict:
        """获取机器人 QQ 号信息"""
        resp = requests.get(f"{self.base_url}/get_login_info", timeout=10)
        return resp.json()

    def get_group_list(self) -> list:
        """获取所有群列表"""
        resp = requests.post(f"{self.base_url}/get_group_list", timeout=10)
        data = resp.json()
        return data.get("data", [])

    def format_monitor_report(self, summary: dict) -> str:
        """将监控摘要格式化为推送消息"""
        lines = [f"📊 信息监控报告 ({summary['period']})\n"]

        if summary.get("qq_groups"):
            lines.append("📢 QQ群消息：")
            for g in summary["qq_groups"]:
                lines.append(f"  群{g['group_id']}: {g['count']} 条")

        if summary.get("qzone_posts"):
            lines.append("\n📖 QQ空间动态：")
            for q in summary["qzone_posts"]:
                lines.append(f"  QQ{q['target_qq']}: {q['count']} 条")

        return "\n".join(lines)


# === 使用示例 ===
if __name__ == "__main__":
    notifier = QQNotifier("http://localhost:3000")

    # 获取登录信息
    info = notifier.get_login_info()
    print(f"机器人 QQ: {info.get('data', {}).get('user_id')}")

    # 发送测试消息（替换为目标 QQ 号）
    # notifier.send_private_msg("1604220682", "监控服务已启动 ✓")
```

### 4.4 完整主入口（整合所有模块）

```python
# main.py
import asyncio
import time
from fetcher.qq_group_listener import QQGroupMonitor
from pusher.qq_push import QQNotifier
from fetcher.message_store import MessageStore


async def run_monitor():
    """启动监控主循环"""

    # 1. 初始化模块
    monitor = QQGroupMonitor(
        ws_url="ws://localhost:3001",
        monitored_groups=[]  # 留空=监控所有群
    )
    notifier = QQNotifier("http://localhost:3000")
    store = MessageStore("data/monitor.db")

    # 2. 验证 NapCat 连接
    try:
        info = notifier.get_login_info()
        bot_qq = info.get("data", {}).get("user_id")
        print(f"机器人已连接: QQ {bot_qq}")
    except Exception as e:
        print(f"NapCat 连接失败: {e}")
        print("请确保 NapCat 已启动并配置了 HTTP 端口 3000")
        return

    # 3. 启动群消息监听（后台任务）
    print("启动群消息监听...")
    monitor_task = asyncio.create_task(monitor.start())

    # 4. 定时汇总推送（每 6 小时）
    PUSH_INTERVAL = 6 * 3600
    TARGET_USER = "1604220682"  # 接收推送的 QQ 号

    async def periodic_report():
        while True:
            await asyncio.sleep(PUSH_INTERVAL)
            try:
                summary = store.get_all_sources_summary(hours=PUSH_INTERVAL // 3600)
                report = notifier.format_monitor_report(summary)
                notifier.send_private_msg(TARGET_USER, report)
                print("已发送定时汇总报告")
            except Exception as e:
                print(f"推送失败: {e}")

    report_task = asyncio.create_task(periodic_report())

    # 5. 运行
    await asyncio.gather(monitor_task, report_task)


if __name__ == "__main__":
    asyncio.run(run_monitor())
```

---

## 5. 安全建议

### 5.1 降低封号风险措施

#### QQ空间爬虫

| 措施 | 说明 |
|------|------|
| **使用小号** | 绝不用主号爬取，准备 3-5 个不常用的小号 |
| **控制频率** | 每次请求间隔 10-30 秒，翻页间隔更长 |
| **随机 User-Agent** | 模拟不同浏览器，避免指纹识别 |
| **分时段运行** | 避开深夜和凌晨（00:00-06:00），选择白天活跃时段 |
| **分批抓取** | 不要一次性抓取所有账号，每天只抓 2-3 个 |
| **异常检测** | 检测到 HTTP 403/验证码立即停止，等 1-2 小时后再试 |

#### NapCat 机器人

| 措施 | 说明 |
|------|------|
| **小号做机器人** | 用非主号登录 NapCat，主号只接收推送 |
| **不要主动发消息** | 仅被动监听，避免被检测为营销号 |
| **不要加太多群** | 机器人账号群数控制在合理范围 |
| **消息频率限制** | 推送消息间隔 > 3 秒，避免被判定为刷屏 |

### 5.2 多账号轮换方案

```python
# utils/account_pool.py
import time
import random
import sqlite3


class AccountPool:
    """QQ 小号池管理器"""

    def __init__(self, db_path: str = "data/accounts.db"):
        self.db = sqlite3.connect(db_path)
        self._init_db()
        self._cooldown = 3600  # 账号冷却时间（秒）

    def _init_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                qq TEXT PRIMARY KEY,
                cookie TEXT,
                skey TEXT,
                status TEXT DEFAULT 'active',
                last_used_at REAL DEFAULT 0,
                ban_count INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.db.commit()

    def add_account(self, qq: str, cookie: str):
        """添加账号到池中"""
        self.db.execute(
            "INSERT OR REPLACE INTO accounts (qq, cookie, skey, status) VALUES (?, ?, ?, 'active')",
            (qq, cookie, self._extract_skey(cookie))
        )
        self.db.commit()

    def get_next_account(self) -> dict:
        """获取下一个可用账号（轮换策略）"""
        now = time.time()
        account = self.db.execute("""
            SELECT * FROM accounts
            WHERE status = 'active'
              AND (last_used_at < ? OR last_used_at IS NULL)
            ORDER BY last_used_at ASC
            LIMIT 1
        """, [now - self._cooldown]).fetchone()

        if not account:
            raise Exception("所有账号都在冷却中或已失效")

        # 更新使用时间
        self.db.execute(
            "UPDATE accounts SET last_used_at = ?, total_requests = total_requests + 1 WHERE qq = ?",
            (now, account["qq"])
        )
        self.db.commit()

        return dict(account)

    def mark_banned(self, qq: str):
        """标记账号被封禁"""
        self.db.execute(
            "UPDATE accounts SET status = 'banned', ban_count = ban_count + 1 WHERE qq = ?",
            (qq,)
        )
        self.db.commit()

    def mark_cookie_expired(self, qq: str):
        """标记 Cookie 过期"""
        self.db.execute(
            "UPDATE accounts SET status = 'expired', cookie = NULL, skey = NULL WHERE qq = ?",
            (qq,)
        )
        self.db.commit()

    def get_pool_status(self) -> list:
        """获取账号池状态"""
        return [dict(r) for r in self.db.execute(
            "SELECT qq, status, total_requests, ban_count FROM accounts ORDER BY qq"
        ).fetchall()]

    @staticmethod
    def _extract_skey(cookie: str) -> str:
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("skey="):
                return part[5:]
        return ""


# === 使用示例 ===
if __name__ == "__main__":
    pool = AccountPool()

    # 添加小号
    pool.add_account("小号QQ1", "Cookie1")
    pool.add_account("小号QQ2", "Cookie2")
    pool.add_account("小号QQ3", "Cookie3")

    # 获取可用账号
    account = pool.get_next_account()
    print(f"使用账号: QQ {account['qq']}")

    # 如果被封
    # pool.mark_banned(account["qq"])

    # 查看池状态
    for acc in pool.get_pool_status():
        print(f"  QQ {acc['qq']}: {acc['status']}, 请求 {acc['total_requests']} 次")
```

### 5.3 请求频率控制

```python
# utils/rate_limiter.py
import time
import threading


class RateLimiter:
    """请求频率控制器"""

    def __init__(self, calls_per_minute: int = 10, burst: int = 3):
        """
        Args:
            calls_per_minute: 每分钟最大请求数
            burst: 突发允许的最大连续请求数
        """
        self.min_interval = 60.0 / calls_per_minute
        self.burst = burst
        self.consecutive = 0
        self.last_call = 0
        self.lock = threading.Lock()

    def wait(self):
        """在发送请求前调用，自动等待满足频率限制"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call

            # 连续请求达到 burst 次后，强制长等待
            if self.consecutive >= self.burst:
                wait_time = max(elapsed, self.min_interval * 3)
                time.sleep(wait_time)
                self.consecutive = 0

            elif elapsed < self.min_interval:
                # 随机偏移避免固定间隔
                jitter = self.min_interval * 0.2 * (hash(str(now)) % 10 / 10)
                time.sleep(self.min_interval - elapsed + jitter)

            self.last_call = time.time()
            self.consecutive += 1


# === 使用示例 ===
if __name__ == "__main__":
    # 限制为每分钟 6 次请求（每次间隔 ~10s）
    limiter = RateLimiter(calls_per_minute=6, burst=2)

    for i in range(10):
        limiter.wait()
        print(f"请求 {i + 1} 已发送")
```

### 5.4 整体安全策略总结

```
优先级排序：

1. ★★★★★  NapCat 被动监听群消息（几乎无风险）
   → 小号登录，仅接收不发送

2. ★★★☆☆  QQ空间低频爬取（中等风险）
   → 小号池 + 频率控制 + 异常检测
   → 每天只抓 2-3 个账号
   → 发现异常立即停止

3. ★★☆☆☆  QQ空间高频爬取（高风险）
   → 不推荐，仅在有充足小号时使用
   → 必须配合多账号轮换 + 随机延迟

4. ★☆☆☆☆  主动发消息/加群（极高风险）
   → 仅用于推送通知
   → 控制在最低频率
```

---

## 快速启动清单

```bash
# 1. 创建项目目录
mkdir -p qq_monitor/{fetcher,pusher,utils,data}
cd qq_monitor

# 2. 安装依赖
pip install requests websockets feedparser

# 3. 部署 NapCat（Docker 方式）
docker run -d --name napcat --restart always \
  -p 3000:3000 -p 3001:3001 \
  -v napcat-data:/app/napcat/config \
  mlikiowa/napcat-docker:latest
# 扫码登录

# 4. 验证 NapCat
curl http://localhost:3000/get_login_info

# 5. 启动群消息监听
python main.py

# 6. （可选）QQ空间爬取 — 先配置 Cookie
# 编辑 qzone_cookie.txt 写入 Cookie
# python fetcher/qzone_crawler.py
```

---

## 目标 QQ 空间账号

| # | QQ 号 | 备注 |
|---|-------|------|
| 1 | 1727559019 | |
| 2 | 2842943530 | |
| 3 | 2794573164 | |
| 4 | 1576711825 | |
| 5 | 823413788 | |
| 6 | 3290029079 | |
| 7 | 3234986841 | |
| 8 | 2075199041 | |
| 9 | 3107961334 | |
