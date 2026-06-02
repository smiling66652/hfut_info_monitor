# wewe-rss 微信公众号 RSS 订阅实战部署指南

> 本指南专门针对合肥工业大学相关微信公众号订阅场景，帮助你快速部署 wewe-rss 并集成到现有监控系统。

---

## 目录

1. [Docker 部署步骤](#1-docker-部署步骤)
2. [微信公众号订阅步骤](#2-微信公众号订阅步骤)
3. [RSS API 获取方法](#3-rss-api-获取方法)
4. [Python RSS 读取示例](#4-python-rss-读取示例)
5. [与现有系统集成](#5-与现有系统集成)

---

## 1. Docker 部署步骤

### 1.1 方式一：MySQL 版本（推荐）

```bash
# 创建 Docker 网络
docker network create wewe-rss

# 启动 MySQL 数据库
docker run -d \
  --name wewe-mysql \
  -e MYSQL_ROOT_PASSWORD=wewe123456 \
  -e MYSQL_DATABASE=wewe-rss \
  -e TZ='Asia/Shanghai' \
  -v wewe-mysql-data:/var/lib/mysql \
  --network wewe-rss \
  --restart unless-stopped \
  mysql:8.3.0 --mysql-native-password=ON

# 等待 MySQL 启动（约 10 秒）
sleep 10

# 启动 wewe-rss 服务
docker run -d \
  --name wewe-rss \
  -p 4000:4000 \
  -e DATABASE_URL='mysql://root:wewe123456@wewe-mysql:3306/wewe-rss?schema=public&connect_timeout=30&pool_timeout=30&socket_timeout=30' \
  -e AUTH_CODE=hfut2024 \
  -e CRON_EXPRESSION='0 */30 * * * *' \
  -e SERVER_ORIGIN_URL=http://localhost:4000 \
  -e FEED_MODE=fulltext \
  --network wewe-rss \
  --restart unless-stopped \
  cooderl/wewe-rss:latest
```

### 1.2 方式二：SQLite 版本（轻量级）

```yaml
# docker-compose.yml
version: '3.8'

services:
  wewe-rss:
    image: cooderl/wewe-rss-sqlite:latest
    container_name: wewe-rss
    ports:
      - "4000:4000"
    environment:
      - AUTH_CODE=hfut2024
      - CRON_EXPRESSION=0 */30 * * * *
      - SERVER_ORIGIN_URL=http://localhost:4000
      - FEED_MODE=fulltext
      - ENABLE_CLEAN_HTML=true
      - UPDATE_DELAY_TIME=60s
    volumes:
      - ./wewe-data:/app/data
    restart: unless-stopped
```

```bash
# 启动服务
docker-compose up -d
```

### 1.3 验证部署

```bash
# 检查容器状态
docker ps | grep wewe

# 访问 Web 界面
# http://localhost:4000
```

### 1.4 关键环境变量说明

| 变量名 | 说明 | 推荐值 |
|--------|------|--------|
| `AUTH_CODE` | API 访问授权码 | `hfut2024` |
| `CRON_EXPRESSION` | 订阅更新频率（默认每天 5:35 和 17:35） | `0 */30 * * * *` (每30分钟) |
| `FEED_MODE` | 设置为 `fulltext` 获取完整文章内容 | `fulltext` |
| `SERVER_ORIGIN_URL` | 服务器访问地址（反向代理时必填） | `http://你的公网IP:4000` |
| `UPDATE_DELAY_TIME` | 更新间隔，防止触发频率限制 | `60s` |

### 1.5 初始设置流程

1. **首次访问**：打开 http://localhost:4000
2. **输入授权码**：填入 `AUTH_CODE` 设置的值
3. **添加微信读书账号**：
   - 点击「账号管理」→「添加账号」
   - 使用微信扫描二维码登录微信读书
   - **重要**：不要勾选「24小时后自动登出」
4. **完成**：开始订阅公众号

---

## 2. 微信公众号订阅步骤

### 2.1 获取公众号文章链接

#### 方法一：微信内分享

1. 打开微信，搜索目标公众号
2. 进入公众号主页，点击任意文章
3. 点击右上角「...」→「复制链接」
4. 获取类似链接：`https://mp.weixin.qq.com/s/xxxxxxxxxxxxxxxx`

#### 方法二：微信读书搜索

1. 打开微信读书网页版：https://weread.qq.com/
2. 搜索公众号名称
3. 找到公众号的微信读书主页链接

### 2.2 目标公众号信息

| 公众号名称 | 订阅链接获取方式 |
|-----------|----------------|
| 合肥工业大学 | 微信搜索，关注后获取文章链接 |
| 合肥工业大学宣城校区 | 同上 |
| 合肥工业大学宣城校区学生工作 | 同上 |
| 合肥工业大学教务处 | 同上 |

### 2.3 订阅操作步骤

1. 登录 wewe-rss Web 界面
2. 进入「订阅源管理」→「添加订阅」
3. 粘贴公众号文章链接
4. 点击获取，等待系统识别公众号
5. 确认订阅

**⚠️ 注意**：
- 添加订阅后建议等待 1-2 分钟再添加下一个
- 短时间内添加太多可能导致临时封禁
- 如遇封禁，等待 24 小时后重试

### 2.4 找到公众号的微信读书账号

wewe-rss 通过微信读书获取文章，因此需要确保公众号在微信读书上有收录：

1. 在微信读书中搜索公众号名称
2. 如果找到，说明可以直接订阅
3. 如果找不到，可以尝试通过文章链接添加，wewe-rss 会自动关联

---

## 3. RSS API 获取方法

### 3.1 获取单个公众号 RSS

登录 wewe-rss 后：

1. 进入「订阅源管理」
2. 找到已订阅的公众号
3. 点击复制 RSS 链接按钮

### 3.2 RSS 地址格式

```
# 基础格式
http://localhost:4000/rss/公众号名称

# 带授权码格式（远程访问时必须）
http://localhost:4000/rss/公众号名称?auth_code=hfut2024

# 完整文章格式（推荐）
http://localhost:4000/rss/公众号名称?auth_code=hfut2024&mode=fulltext
```

### 3.3 目标公众号 RSS 地址

假设服务器运行在 `http://localhost:4000`，授权码为 `hfut2024`：

| 公众号名称 | RSS 地址 |
|-----------|---------|
| 合肥工业大学 | `http://localhost:4000/rss/合肥工业大学?auth_code=hfut2024` |
| 合肥工业大学宣城校区 | `http://localhost:4000/rss/合肥工业大学宣城校区?auth_code=hfut2024` |
| 合肥工业大学宣城校区学生工作 | `http://localhost:4000/rss/合肥工业大学宣城校区学生工作?auth_code=hfut2024` |
| 合肥工业大学教务处 | `http://localhost:4000/rss/合肥工业大学教务处?auth_code=hfut2024` |

### 3.4 API 端点一览

```
# RSS 订阅
GET /rss/:name                    # 获取 RSS 源
GET /rss/verify/:name             # 验证并更新单个订阅

# 账号管理
GET /api/accounts                # 获取账号列表
POST /api/accounts               # 添加账号
DELETE /api/accounts/:id         # 删除账号

# 订阅源管理
GET /api/sources                  # 获取订阅源列表
POST /api/sources                 # 添加订阅源
DELETE /api/sources/:id          # 删除订阅源
POST /api/sources/refresh        # 刷新所有订阅源

# OPML 导出
GET /api/opml                     # 导出 OPML
```

---

## 4. Python RSS 读取示例

### 4.1 安装依赖

```bash
pip install feedparser requests schedule
```

### 4.2 基础 RSS 读取

```python
import feedparser
import requests
from datetime import datetime

# 配置
RSS_BASE_URL = "http://localhost:4000"
AUTH_CODE = "hfut2024"

# 公众号列表
WECHAT_ACCOUNTS = [
    "合肥工业大学",
    "合肥工业大学宣城校区",
    "合肥工业大学宣城校区学生工作",
    "合肥工业大学教务处",
]

def get_rss_url(account_name: str) -> str:
    """构建 RSS URL"""
    import urllib.parse
    encoded_name = urllib.parse.quote(account_name)
    return f"{RSS_BASE_URL}/rss/{encoded_name}?auth_code={AUTH_CODE}&mode=fulltext"

def fetch_articles(account_name: str) -> list:
    """获取单个公众号的最新文章"""
    rss_url = get_rss_url(account_name)

    try:
        feed = feedparser.parse(rss_url)

        articles = []
        for entry in feed.entries:
            article = {
                "title": entry.title,
                "link": entry.link,
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "source": account_name,
            }

            # 尝试获取完整内容
            if hasattr(entry, "content"):
                article["content"] = entry.content[0].value
            else:
                article["content"] = article["summary"]

            articles.append(article)

        return articles

    except Exception as e:
        print(f"[{account_name}] 获取失败: {e}")
        return []

def main():
    """获取所有公众号的最新文章"""
    print(f"[{datetime.now()}] 开始抓取...")

    all_articles = []
    for account in WECHAT_ACCOUNTS:
        articles = fetch_articles(account)
        all_articles.extend(articles)
        print(f"[{account}] 获取到 {len(articles)} 篇文章")

    print(f"总计: {len(all_articles)} 篇文章")

    # 打印最新文章
    for article in all_articles[:5]:
        print(f"\n--- {article['source']} ---")
        print(f"标题: {article['title']}")
        print(f"链接: {article['link']}")

if __name__ == "__main__":
    main()
```

### 4.3 定时检查更新

```python
import feedparser
import sqlite3
import schedule
import time
import urllib.parse
from datetime import datetime
from threading import Thread

# ============== 配置 ==============
RSS_BASE_URL = "http://localhost:4000"
AUTH_CODE = "hfut2024"
DB_PATH = "hfut_articles.db"

WECHAT_ACCOUNTS = [
    "合肥工业大学",
    "合肥工业大学宣城校区",
    "合肥工业大学宣城校区学生工作",
    "合肥工业大学教务处",
]

# ============== 数据库 ==============
class ArticleDB:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.init_db()

    def init_db(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                content TEXT,
                summary TEXT,
                pub_date TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                pushed INTEGER DEFAULT 0
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_link ON articles(link)
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pushed ON articles(pushed)
        """)
        self.db.commit()

    def is_exists(self, link: str) -> bool:
        cursor = self.db.execute(
            "SELECT 1 FROM articles WHERE link = ?", (link,)
        )
        return cursor.fetchone() is not None

    def save_article(self, article: dict) -> bool:
        if self.is_exists(article["link"]):
            return False

        self.db.execute("""
            INSERT INTO articles (source, title, link, content, summary, pub_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            article["source"],
            article["title"],
            article["link"],
            article.get("content", ""),
            article.get("summary", ""),
            article.get("published", "")
        ))
        self.db.commit()
        return True

    def get_unpushed(self, limit: int = 10) -> list:
        cursor = self.db.execute("""
            SELECT id, source, title, link, content, summary
            FROM articles
            WHERE pushed = 0
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))
        columns = ["id", "source", "title", "link", "content", "summary"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def mark_pushed(self, article_id: int):
        self.db.execute(
            "UPDATE articles SET pushed = 1 WHERE id = ?",
            (article_id,)
        )
        self.db.commit()

# ============== RSS 抓取 ==============
def fetch_all_feeds():
    """抓取所有公众号更新"""
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] 开始定时抓取...")

    db = ArticleDB(DB_PATH)
    new_count = 0

    for account in WECHAT_ACCOUNTS:
        encoded_name = urllib.parse.quote(account)
        rss_url = f"{RSS_BASE_URL}/rss/{encoded_name}?auth_code={AUTH_CODE}&mode=fulltext"

        try:
            feed = feedparser.parse(rss_url)

            for entry in feed.entries[:10]:  # 只取最新 10 篇
                article = {
                    "source": account,
                    "title": entry.title,
                    "link": entry.link,
                    "summary": entry.get("summary", ""),
                    "published": entry.get("published", ""),
                }

                if hasattr(entry, "content"):
                    article["content"] = entry.content[0].value
                else:
                    article["content"] = article["summary"]

                if db.save_article(article):
                    new_count += 1
                    print(f"  [新] {account}: {article['title'][:30]}...")

        except Exception as e:
            print(f"  [错误] {account}: {e}")

    print(f"本次新增 {new_count} 篇文章")

    # 处理新文章（推送等）
    if new_count > 0:
        process_new_articles(db)

    print(f"{'='*50}\n")

def process_new_articles(db: ArticleDB):
    """处理新文章：可以在这里添加推送逻辑"""
    articles = db.get_unpushed(limit=10)

    for article in articles:
        # TODO: 推送到 QQ / 发送邮件 / 调用 Dify 等
        print(f"  [待推送] {article['source']}: {article['title']}")

        # 标记已处理（实际使用时根据推送结果决定）
        # db.mark_pushed(article['id'])

# ============== 主程序 ==============
def run_schedule():
    """运行定时任务"""
    # 每 30 分钟执行一次
    schedule.every(30).minutes.do(fetch_all_feeds)

    print("定时任务已启动，每 30 分钟检查一次更新")
    print("按 Ctrl+C 退出\n")

    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    # 首次运行立即抓取一次
    fetch_all_feeds()

    # 启动定时任务
    run_schedule()
```

### 4.4 过滤关键词示例

```python
def filter_important_articles(db: ArticleDB, keywords: list) -> list:
    """根据关键词过滤重要文章"""
    articles = db.get_unpushed(limit=50)

    important = []
    for article in articles:
        title = article["title"].lower()
        content = article.get("content", "").lower()

        for keyword in keywords:
            if keyword.lower() in title or keyword.lower() in content:
                article["matched_keyword"] = keyword
                important.append(article)
                break

    return important

# 使用示例
IMPORTANT_KEYWORDS = [
    "通知", "公告", "考试", "成绩",
    "放假", "开学", "选课", "补考",
    "奖学金", "评优", "比赛", "竞赛"
]

important = filter_important_articles(db, IMPORTANT_KEYWORDS)
print(f"找到 {len(important)} 篇重要文章")
```

---

## 5. 与现有系统集成

### 5.1 集成架构

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  wewe-rss   │───▶│  Python     │───▶│  hfut_info  │
│  (Port 4000)│    │  Fetcher    │    │  _monitor   │
└─────────────┘    └─────────────┘    └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  SQLite DB  │
                   └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  NapCat QQ  │
                   │  (Port 3000)│
                   └─────────────┘
```

### 5.2 配置更新示例

在现有 `hfut_info_monitor` 项目中添加 wewe-rss 支持：

```python
# config.py - 添加新的配置项

# ============== wewe-rss 配置 ==============
WEWE_RSS_CONFIG = {
    "base_url": "http://localhost:4000",
    "auth_code": "hfut2024",  # 与 docker-compose 中的 AUTH_CODE 一致
    "update_interval": 30,    # 分钟
    "accounts": [
        "合肥工业大学",
        "合肥工业大学宣城校区",
        "合肥工业大学宣城校区学生工作",
        "合肥工业大学教务处",
    ],
    # 关键词过滤
    "keywords": [
        "通知", "公告", "考试", "成绩",
        "放假", "开学", "选课", "补考",
        "奖学金", "评优", "比赛", "竞赛",
        "转发", "紧急", "重要"
    ]
}
```

### 5.3 完整的集成模块

```python
# wechat_rss_monitor.py - 微信公众号监控模块

import feedparser
import sqlite3
import urllib.parse
import time
from datetime import datetime
from typing import Optional

class WechatRSSMonitor:
    """微信公众号 RSS 监控器"""

    def __init__(self, config: dict, db_path: str):
        self.config = config
        self.db_path = db_path
        self.db = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wechat_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                content TEXT,
                summary TEXT,
                pub_date TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                pushed INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def _get_rss_url(self, account: str) -> str:
        """构建 RSS URL"""
        encoded = urllib.parse.quote(account)
        return (
            f"{self.config['base_url']}/rss/{encoded}"
            f"?auth_code={self.config['auth_code']}"
            f"&mode=fulltext"
        )

    def fetch_articles(self, account: str) -> list:
        """获取单个公众号的文章"""
        rss_url = self._get_rss_url(account)

        try:
            feed = feedparser.parse(rss_url)
            articles = []

            for entry in feed.entries:
                article = {
                    "source": account,
                    "title": entry.title,
                    "link": entry.link,
                    "summary": entry.get("summary", ""),
                    "pub_date": entry.get("published", ""),
                }

                if hasattr(entry, "content"):
                    article["content"] = entry.content[0].value
                else:
                    article["content"] = article["summary"]

                articles.append(article)

            return articles

        except Exception as e:
            print(f"[{account}] 获取失败: {e}")
            return []

    def save_article(self, article: dict) -> bool:
        """保存文章到数据库"""
        try:
            self.db.execute("""
                INSERT OR IGNORE INTO wechat_articles
                (source, title, link, content, summary, pub_date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                article["source"],
                article["title"],
                article["link"],
                article.get("content", ""),
                article.get("summary", ""),
                article.get("pub_date", "")
            ))
            self.db.commit()
            return True
        except Exception:
            return False

    def is_important(self, article: dict) -> Optional[str]:
        """检查文章是否包含重要关键词"""
        text = (article["title"] + " " + article.get("content", "")).lower()

        for keyword in self.config["keywords"]:
            if keyword.lower() in text:
                return keyword

        return None

    def fetch_all(self) -> dict:
        """抓取所有公众号并返回统计"""
        stats = {
            "total": 0,
            "new": 0,
            "important": 0,
            "articles": []
        }

        for account in self.config["accounts"]:
            articles = self.fetch_articles(account)

            for article in articles:
                stats["total"] += 1

                if self.save_article(article):
                    stats["new"] += 1

                    matched = self.is_important(article)
                    if matched:
                        article["matched_keyword"] = matched
                        stats["important"] += 1
                        stats["articles"].append(article)

        return stats

    def get_unpushed_important(self, limit: int = 20) -> list:
        """获取未推送的重要文章"""
        cursor = self.db.execute("""
            SELECT id, source, title, link, content, summary
            FROM wechat_articles
            WHERE pushed = 0
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

        columns = ["id", "source", "title", "link", "content", "summary"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def mark_pushed(self, article_id: int):
        """标记文章已推送"""
        self.db.execute(
            "UPDATE wechat_articles SET pushed = 1 WHERE id = ?",
            (article_id,)
        )
        self.db.commit()

# ============== 使用示例 ==============
if __name__ == "__main__":
    from config import WEWE_RSS_CONFIG

    monitor = WechatRSSMonitor(
        config=WEWE_RSS_CONFIG,
        db_path="data/hfut_monitor.db"
    )

    # 单次抓取
    stats = monitor.fetch_all()
    print(f"抓取完成: 新增 {stats['new']} 篇, 重要 {stats['important']} 篇")

    # 获取重要文章进行推送
    important = monitor.get_unpushed_important()
    for article in important:
        print(f"\n【重要】{article['source']}")
        print(f"标题: {article['title']}")
        print(f"关键词: {article.get('matched_keyword', 'N/A')}")
        print(f"链接: {article['link']}")

        # TODO: 推送到 QQ

        # monitor.mark_pushed(article['id'])
```

### 5.4 定时任务配置

使用 systemd 管理定时任务（Linux）或 Windows 任务计划程序：

#### Linux (systemd)

```bash
# /etc/systemd/system/wewe-monitor.service
[Unit]
Description=HFUT WeChat RSS Monitor
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/hfut_info_monitor
ExecStart=/usr/bin/python3 wechat_rss_monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# 启用服务
sudo systemctl enable wewe-monitor
sudo systemctl start wewe-monitor
```

#### Windows 任务计划程序

```powershell
# 创建定时任务：每 30 分钟运行一次
$action = New-ScheduledTaskAction -Execute "python" -Argument "wechat_rss_monitor.py"
$trigger = New-ScheduledTaskTrigger -Once -At "09:00" -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "HFUT-RSS-Monitor" -Action $action -Trigger $trigger -Settings $settings -Description "合肥工业大学微信公众号 RSS 监控"
```

---

## 附录

### A. 常见问题

| 问题 | 解决方案 |
|------|---------|
| RSS 返回空 | 检查 wewe-rss 是否正常运行，微信读书账号是否登录 |
| 提示频率限制 | 等待 24 小时，减少更新频率 |
| 无法获取文章内容 | 确保 `FEED_MODE=fulltext` 已设置 |
| 远程无法访问 | 配置 `SERVER_ORIGIN_URL` 为公网地址 |

### B. Docker 管理命令

```bash
# 查看日志
docker logs -f wewe-rss

# 重启服务
docker restart wewe-rss

# 更新镜像
docker pull cooderl/wewe-rss:latest
docker-compose down && docker-compose up -d

# 完全重置（会丢失订阅数据）
docker-compose down -v
docker-compose up -d
```

### C. 相关资源

- GitHub: https://github.com/cooderl/wewe-rss
- Docker Hub: https://hub.docker.com/r/cooderl/wewe-rss
- 微信读书: https://weread.qq.com/

---

*最后更新：2026-05-17*
