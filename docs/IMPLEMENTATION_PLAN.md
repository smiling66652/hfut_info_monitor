# 私人秘书信息监控系统 - 实施计划

> 基于调研结果制定的分阶段实施计划
> 更新时间：2026-05-17

---

## 系统概览

```
信息源                   采集方式               存储/队列              AI处理              推送
─────────────────────────────────────────────────────────────────────────────────────────
微信公众号(4个)   →    wewe-rss           →                       →
QQ空间(9个)       →    QQ空间爬虫         →   MQTT/SQLite DB  →   Dify工作流  →   QQ 1604220682
QQ群消息          →    NapCat(已部署)     →                       →
```

**目标设备**：当前 MateBook E 2022 → 未来 Y7000P 2025（Ollama + Dify 本地部署）

---

## Phase 1：快速见效（现有系统 + wewe-rss）

> 目标：2 周内上线微信公众号监控，验证整体链路

### 1.1 部署 wewe-rss

**时间估算**：3-5 天
**依赖**：微信读书账号、可访问的服务器（或本地 Docker）

#### 步骤

```bash
# 1. 克隆项目
git clone https://github.com/cooderl/wewe-rss.git
cd wewe-rss

# 2. 配置环境变量（伪代码，实际用 docker-compose）
# wechat.reading.account=你的微信读书账号
# wechat.reading.password=你的密码
# server.port=4000

# 3. 启动服务
docker-compose up -d

# 4. 验证：访问 http://localhost:4000
# 在 Web 界面添加 4 个微信公众号
```

#### 关键配置

```yaml
# docker-compose.yml 关键配置
services:
  wewe-rss:
    image: cooderl/wewe-rss:latest
    ports:
      - "4000:4000"
    environment:
      - WECHAT_ACCOUNT=${WECHAT_ACCOUNT}
      - WECHAT_PASSWORD=${WECHAT_PASSWORD}
      - RSS_PATH=/rss  # RSS 访问路径
    volumes:
      - ./data:/app/data
```

#### RSS 输出示例

```
# 每个公众号生成独立 RSS Feed
http://localhost:4000/rss/公众号名称
```

### 1.2 搭建基础拉取服务

**时间估算**：2-3 天

```python
# fetcher/wechat_rss.py
import feedparser
import requests
import sqlite3
from datetime import datetime

class WechatRSSFetcher:
    def __init__(self, db_path="data/monitor.db"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.init_db()

    def init_db(self):
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            source_type TEXT,    -- 'wechat' | 'qzone' | 'qqgroup'
            source_name TEXT,
            title TEXT,
            content TEXT,
            url TEXT UNIQUE,
            pub_time DATETIME,
            pushed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

    def fetch_feed(self, rss_url: str, source_name: str):
        feed = feedparser.parse(rss_url)
        new_count = 0
        for entry in feed.entries:
            try:
                self.db.execute(
                    "INSERT OR IGNORE INTO articles (source_type, source_name, title, content, url, pub_time) VALUES (?,?,?,?,?,?)",
                    ("wechat", source_name, entry.title, entry.get("summary",""), entry.link, datetime.now())
                )
                new_count += 1
            except Exception: pass
        self.db.commit()
        return new_count

    def get_unpushed(self):
        return self.db.execute(
            "SELECT * FROM articles WHERE pushed=0 ORDER BY pub_time DESC LIMIT 10"
        ).fetchall()
```

### 1.3 QQ 推送集成（目标：1604220682）

```python
# pusher/qq_push.py
import requests

class QQNotifier:
    def __init__(self, napcat_url="http://localhost:3000"):
        self.base_url = napcat_url

    def send_to_user(self, target_qq: str, message: str):
        """通过 NapCat 发送私聊消息"""
        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(target_qq),
                "message": message
            }
        }
        resp = requests.post(f"{self.base_url}/", json=payload)
        return resp.json()

    def format_article(self, article: dict) -> str:
        return f"""📢 新文章推送

来源：{article['source_name']}
标题：{article['title']}
链接：{article['url']}
时间：{article['pub_time']}
"""

# 主循环
# while True:
#     articles = fetcher.get_unpushed()
#     for a in articles:
#         notifier.send_to_user("1604220682", notifier.format_article(a))
#         fetcher.mark_pushed(a['id'])
#     time.sleep(300)
```

### 1.4 Phase 1 验收标准

- [ ] wewe-rss 正常运行，4 个公众号 RSS 可访问
- [ ] 数据库中有文章记录
- [ ] 手动触发可推送一条消息到 QQ 1604220682
- [ ] 每 5 分钟自动拉取一次

---

## Phase 2：QQ 监控（QQ空间 + QQ群）

> 目标：扩展采集源，覆盖 QQ 空间 9 个账号 + QQ 群消息

### 2.1 QQ 空间爬虫

**时间估算**：5-7 天（高风险，需要反封号策略）
**依赖**：多个 QQ 小号账号、cookie 管理

#### 架构设计

```
QQ小号池(3-5个)  →  Cookie管理器  →  QQ空间爬虫(轮询)  →  数据入库
                    ↓
                 异常检测(封号/验证码)
```

#### 核心代码

```python
# fetcher/qzone_crawler.py
import requests
import json
from concurrent.futures import ThreadPoolExecutor
import time

class QzoneCrawler:
    def __init__(self, cookie_pool: list):
        self.cookie_pool = cookie_pool
        self.current_idx = 0

    def _get_next_cookie(self):
        """轮询获取 cookie"""
        cookie = self.cookie_pool[self.current_idx]
        self.current_idx = (self.current_idx + 1) % len(self.cookie_pool)
        return cookie

    def fetch_shuoshuo(self, target_qq: str, cookie: str = None):
        """获取指定QQ的说说"""
        if cookie is None:
            cookie = self._get_next_cookie()

        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = {
            "uin": target_qq,
            "ftype": 0,
            "sort": 0,
            "pos": 0,
            "num": 20,
            "g_tk": self._calc_gtk(cookie)
        }
        headers = {
            "Cookie": cookie,
            "Referer": f"https://user.qzone.qq.com/{target_qq}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            return self._parse_shuoshuo(resp.json())
        else:
            print(f"[{target_qq}] 请求失败: {resp.status_code}, 切换账号")
            return []

    def fetch_all_accounts(self, target_accounts: list):
        """多线程抓取多个账号"""
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.fetch_shuoshuo, qq): qq
                for qq in target_accounts
            }
            # 处理结果...

    def _calc_gtk(self, cookie: str) -> str:
        """计算 QQ G_TK（简化版，实际需要完整算法）"""
        # 实际实现需要解析 cookie 中的 skey 并计算 GTK
        # 参考：https://github.com/proxyee-down-org/proxyee-down/issues/371
        pass
```

#### Cookie 管理策略

```python
# utils/cookie_manager.py
import pickle
import os

class CookieManager:
    def __init__(self, cookie_file="data/cookies.pkl"):
        self.cookie_file = cookie_file
        self.cookies = self._load()

    def _load(self):
        if os.path.exists(self.cookie_file):
            with open(self.cookie_file, "rb") as f:
                return pickle.load(f)
        return []

    def add_cookie(self, qq: str, cookie: str, expired: bool = False):
        self.cookies.append({
            "qq": qq,
            "cookie": cookie,
            "expired": expired,
            "last_used": time.time()
        })
        self._save()

    def get_valid_cookie(self) -> str:
        """获取一个有效 cookie，优先使用最近未使用的"""
        valid = [c for c in self.cookies if not c["expired"]]
        if not valid:
            raise Exception("无有效 Cookie！请更新账号")
        # 选择最近最少使用的
        valid.sort(key=lambda x: x["last_used"])
        return valid[0]["cookie"]
```

#### 风险缓解措施

| 风险 | 缓解方案 |
|------|---------|
| Cookie 失效 | 多账号池 + 自动检测失效 |
| 频率限制 | 每个账号间隔 30s+，随机延迟 |
| 滑块验证 | 接入打码平台（如 2captcha）或手动处理 |
| 账号被封 | 小号专用，不影响主号 |

### 2.2 QQ 群消息监控（NapCat 扩展）

**时间估算**：2-3 天
**依赖**：NapCat 已部署

```python
# fetcher/qq_group_listener.py
import requests
import json

class QQGroupListener:
    """通过 NapCat WebSocket 监听群消息"""

    def __init__(self, napcat_ws="ws://localhost:3001"):
        self.ws_url = napcat_ws
        self.monitored_groups = []  # 要监控的群号列表

    def on_group_message(self, event: dict):
        """处理群消息事件"""
        group_id = event.get("group_id")
        if group_id not in self.monitored_groups:
            return

        message = {
            "group_id": group_id,
            "user_id": event.get("user_id"),
            "nickname": event.get("sender", {}).get("nickname"),
            "content": event.get("raw_message"),
            "timestamp": event.get("time"),
        }
        self.save_to_db(message)

    def save_to_db(self, message: dict):
        """存入数据库，等待 AI 处理"""
        pass  # 复用 Phase 1 的 DB 结构
```

### 2.3 Phase 2 验收标准

- [ ] QQ 空间 9 个账号可正常抓取（至少 1 个账号有效）
- [ ] Cookie 池管理正常，失效自动切换
- [ ] QQ 群消息能进入数据库
- [ ] 所有信息源统一入同一个 `articles` 表

---

## Phase 3：AI 处理（Dify 集成）

> 目标：对采集内容进行 AI 摘要、分类、重点提取

### 3.1 部署 Dify（本地或云端）

**时间估算**：1-2 天

```bash
# 本地部署 Dify（适合 Y7000P 2025）
git clone https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
docker-compose up -d

# 访问 http://localhost:8000 完成初始化
```

### 3.2 创建 Dify 工作流

在 Dify 控制台创建 **信息摘要工作流**：

```yaml
# Dify 工作流伪代码描述
节点1: 输入变量
  - article_title: 文章标题
  - article_content: 文章正文
  - source_type: 来源类型

节点2: LLM（摘要生成）
  prompt: |
    你是一个私人信息秘书。请对以下内容进行摘要：

    标题：{{ article_title }}
    来源：{{ source_type }}
    内容：{{ article_content }}

    要求：
    1. 摘要控制在 100 字以内
    2. 标注关键信息（人物/事件/时间）
    3. 评分重要性（1-5 分）

    输出格式（JSON）：
    {
      "summary": "...",
      "importance": 4,
      "tags": ["科技", "AI"],
      "key_points": ["...", "..."]
    }

节点3: 条件分支
  - 如果 importance >= 4：发送推送
  - 如果 importance < 4：仅存档

节点4: HTTP 请求（调用 NapCat 推送）
  - URL: http://localhost:3000/
  - Method: POST
  - Body: { "action": "send_private_msg", "params": {...} }
```

### 3.3 Python 调用 Dify API

```python
# processor/dify_client.py
import requests

class DifyClient:
    def __init__(self, api_key: str, base_url="http://localhost:8000/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def process_article(self, article: dict) -> dict:
        """调用 Dify 工作流处理文章"""
        url = f"{self.base_url}/workflows/run"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": {
                "article_title": article["title"],
                "article_content": article["content"][:2000],  # 限制长度
                "source_type": article["source_type"]
            },
            "response_mode": "blocking",
            "user": "secretary-bot"
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        return resp.json()

    def format_dify_result(self, dify_output: dict, article: dict) -> str:
        """将 Dify 输出格式化为 QQ 消息"""
        data = json.loads(dify_output["data"]["outputs"]["result"])
        return f"""
🤖 AI 摘要（重要性：{data['importance']}/5）

{data['summary']}

标签：{', '.join(data['tags'])}
原文：{article['url']}
""".strip()
```

### 3.4 Phase 3 验收标准

- [ ] Dify 工作流可正常调用
- [ ] 文章经 AI 处理后输出结构化摘要
- [ ] 重要性 >= 4 的文章自动推送到 QQ
- [ ] 低重要性文章仅存档不推送

---

## Phase 4：完整私人秘书

> 目标：本地 LLM（Ollama）+ 个性化 + 定时报告

### 4.1 部署 Ollama（Y7000P 2025）

```bash
# 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 下载中文优化模型
ollama pull qwen2:7b        # 中文能力强，7B 适合本地
ollama pull qwen2:72b       # 如果有 GPU，用 72B 效果更好

# 测试
curl http://localhost:11434/api/generate -d '{
  "model": "qwen2:7b",
  "prompt": "总结这篇文章：..."
}'
```

### 4.2 定时日报生成

```python
# reporter/daily_report.py
from datetime import datetime, timedelta
import ollama

class DailyReportGenerator:
    def __init__(self, db_path="data/monitor.db"):
        self.db_path = db_path

    def generate_daily_report(self, target_date: str = None):
        """生成每日摘要报告"""
        if target_date is None:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        articles = self.get_articles_by_date(target_date)

        # 调用本地 Ollama 生成报告
        prompt = self._build_report_prompt(articles)
        response = ollama.generate(
            model="qwen2:7b",
            prompt=prompt,
            options={"temperature": 0.3}
        )

        report = response["response"]
        self.send_report(report)
        return report

    def _build_report_prompt(self, articles: list) -> str:
        article_text = "\n".join([
            f"[{a['source_name']}] {a['title']}: {a['content'][:200]}"
            for a in articles
        ])
        return f"""
请对以下昨天的监控信息进行整理，生成一份私人日报：

{article_text}

要求：
1. 按信息源分组
2. 标注高优先级信息
3. 给出今日建议关注的要点
"""

    def send_report(self, report: str):
        """发送日报到 QQ"""
        pass  # 复用 QQNotifier
```

### 4.3 系统架构总览（Phase 4 完成）

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据采集层                                │
│  wewe-rss → 微信公众号    QQ空间爬虫 → 9个账号    NapCat → QQ群  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据处理层                                │
│  SQLite DB → 去重 → 优先级队列 → Ollama 本地推理                │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                        输出层                                   │
│  即时推送（重要性>=4）    每日定时报告    个性化过滤规则          │
│          ↓                           ↓                          │
│      QQ 1604220682           QQ 1604220682                      │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 Phase 4 验收标准

- [ ] Ollama 本地运行正常，Qwen2 模型可响应
- [ ] 每日定时生成报告并推送到 QQ
- [ ] 可配置个性化过滤规则（关键词、来源、重要性阈值）
- [ ] 系统稳定运行 7 天无异常

---

## 总体时间规划

| 阶段 | 内容 | 时间 | 依赖 |
|------|------|------|------|
| Phase 1 | wewe-rss + 基础推送 | 2 周 | 微信读书账号 |
| Phase 2 | QQ 空间 + QQ 群 | 2 周 | QQ 小号池 |
| Phase 3 | Dify AI 处理 | 1 周 | Dify 部署 |
| Phase 4 | Ollama 本地 + 日报 | 2 周 | Y7000P 到位 |
| **合计** | | **7 周** | |

---

## 风险与应对

| 风险 | 概率 | 影响 | 应对方案 |
|------|------|------|---------|
| wewe-rss 被微信官方限制 | 中 | 高 | 备选方案：直接爬微信公众号历史文章 |
| QQ 空间 Cookie 频繁失效 | 高 | 中 | 多账号池 + 自动 renew |
| NapCat 被 QQ 风控 | 中 | 高 | 限制消息频率，使用小号做机器人 |
| Dify 工作流不稳定 | 低 | 中 | 降级方案：直接调用 Ollama API |
| Y7000P 延迟到位 | 低 | 低 | Phase 1-3 在 MateBook E 上完成 |

---

## 下一步行动

1. **立即开始**：部署 wewe-rss，添加 4 个微信公众号
2. **并行准备**：申请/准备 QQ 小号（至少 3 个）用于空间爬虫
3. **本周目标**：完成 Phase 1，实现第一条自动推送
4. **沟通确认**：与用户确认 4 个微信公众号列表、9 个 QQ 空间账号列表、需要监控的 QQ 群号
