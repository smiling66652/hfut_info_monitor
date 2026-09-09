# 每日巡检系统 — 全源多级降级方案（含 QQ空间好友动态）

> 更新时间: 2026-06-08  
> 项目路径: `D:\项目整理\01-代码项目\hfut_info_monitor\`

---

## 系统总览

```
                  ┌─────────────────────────────────────────────┐
                  │           智能检测 & 过滤                    │
                  │  enhanced_detector.py (竞赛/二手/有用信息)     │
                  └────────────────┬────────────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
   ┌─────▼──────┐           ┌──────▼──────┐          ┌──────▼──────┐
   │  QQ群监控   │           │ 公众号监控   │          │ QQ空间+好友  │
   │  A→B→C→D   │           │  A→B→C→D    │          │  A→B→C→D→E  │
   └─────┬──────┘           └──────┬──────┘          └──────┬──────┘
         │                         │                         │
         └─────────────────────────┼─────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      每日摘要 (V2)            │
                    │  daily_digest_v2.py          │
                    │  → Markdown / HTML / 纯文本   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │        推送渠道              │
                    │  QQ邮件 | QQ消息(NapCat)      │
                    └─────────────────────────────┘
```

---

## 一、QQ群消息监控

| 方案 | 工具 | 能力 | 稳定性 | 状态 |
|------|------|------|--------|------|
| **Plan A** | NapCat OneBot HTTP API | 实时消息监听 + 推送 | ⭐⭐⭐⭐ | ✅ 已部署 |
| **Plan B** | qq-chat-exporter | 历史消息批量导出 | ⭐⭐⭐⭐ | ⬜ 待部署 |
| **Plan C** | NcatBot (Python SDK) | Python封装实时监听 | ⭐⭐⭐⭐ | ⬜ 备选 |
| **Plan D** | QQ消息管理器导出 | 官方客户端导出 | ⭐⭐⭐⭐⭐ | ⬜ 兜底 |

**文件**: `scripts/qq_group_scraper.py` (已有完整实现)  
**配置**: config.json → `qq_groups` (57个群已配置)  

**降级策略**:
```
NapCat在线? → 实时HTTP推送消息
  ↓ 不在线
qq-chat-exporter部署? → 定时导出JSON备份
  ↓ 不可用
NcatBot Python SDK? → 备选实时监听
  ↓ 不可用
QQ消息管理器导出? → 手动导出兜底
```

---

## 二、微信公众号监控

| 方案 | 工具 | 能力 | 稳定性 | 状态 |
|------|------|------|--------|------|
| **Plan A** | playwright_scraper.py | 搜狗搜索 + 文章正文提取 | ⭐⭐⭐⭐ | ✅ 已实现 |
| **Plan B** | weixin_search_mcp | 搜狗微信搜索API | ⭐⭐⭐ | ✅ 已部署 |
| **Plan C** | agent-browser CLI | 浏览器抓取截图 | ⭐⭐⭐ | ✅ 已安装 |
| **Plan D** | we-mp-rss (wewe-rss) | 微信读书账号 → RSS | ⭐⭐⭐⭐ | ⬜ 需部署 |

**文件**: `scripts/playwright_scraper.py` (新建)  
**配置**: config.json → `wechat_accounts` (4个公众号) + `playwright`  

**降级策略**:
```
playwright可用? → browser抓取(搜狗微信搜索, js_content全文)
  ↓ 不可用
weixin_search_mcp? → 搜狗API关键词搜索
  ↓ 不可用
agent-browser? → 无头浏览器保存截图/内容
  ↓ 不可用
requests降级? → HTTP + User-Agent 备用请求
```

---

## 三、QQ空间好友动态监控 ⭐ 新增

| 方案 | 工具 | 能力 | 稳定性 | 状态 |
|------|------|------|--------|------|
| **Plan A** | qzone-api get_feeds() | 原生API调用feeds3_html_more端点 | ⭐⭐⭐⭐ | ✅ 已安装 |
| **Plan B** | Playwright 浏览器 | 全自动化抓取+截图存档 | ⭐⭐⭐ | ⏳ 安装中 |
| **Plan C** | agent-browser CLI | 轻量无头浏览器内容采集 | ⭐⭐⭐ | ✅ 已安装(v0.27.0) |
| **Plan D** | HTTP requests + cookie | 直接HTTP请求(无UI) | ⭐⭐⭐ | ✅ 已实现 |
| **Plan E** | 本地缓存 + 人工通知 | 加载上次缓存，提示扫码 | ⭐⭐ | ✅ 兜底 |

**文件**: `scripts/qzone_friend_feed.py` (新建, ~580行)  
**配置**: config.json → `qzone_friend_feed` + `qzone`  
**数据存储**: `data/qzone_friend_feed.json` + `data/qzone_feed_cookies.json`  
**截图目录**: `data/screenshots/`

**关键区别**:
- `qzone_scraper.py` → 抓取指定好友的「个人说说」列表
- `qzone_friend_feed.py` → 抓取自己的「好友动态流」聚合页

**Plan A 原理**:
- qzone-api 内部有 `_get_zone()` 方法
- 调用 `taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6` → 个人说说
- 调用 `ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more` → 好友动态Feed ⭐
- 参数: `get_feeds(uin, g_tk, page, count, begintime)` — 已在 api_parms.py 中定义

**Plan B/C 抓取目标**:
- 网页版好友动态: `https://user.qzone.qq.com/proxy/domain/qzs.qzone.qq.com/qzone/v6/friend/friend_dynamic.html`
- 或移动版: `https://mobile.qzone.qq.com/feeds`

---

## 四、所有降级方案汇总

| # | 监控源 | Plan A | Plan B | Plan C | Plan D | Plan E |
|---|--------|--------|--------|--------|--------|--------|
| 1 | QQ群 | NapCat API | qq-chat-exporter | NcatBot | 官方导出 | — |
| 2 | 微信公众号 | playwright_scraper | weixin_search_mcp | agent-browser | requests | — |
| 3 | QQ空间好友动态 | qzone-api get_feeds | Playwright 浏览器 | agent-browser CLI | requests+cookie | 缓存兜底 |
| 4 | QQ空间个人说说 | qzone-api | qzone_scraper Plan B | requests+cookie | — | — |
| 5 | 合工大官网 | website_monitor.py | requests 文本 | 截图存档 | — | — |

**设计原则**:
- 每个源至少 **3 级降级**，QQ空间好友动态 **5 级降级** ✅
- 任一方案失败 **不阻断** 后续流程
- 失败日志包含 `plan_history` 完整链路
- 最终方案 (Plan E) 不做任何破坏性操作，仅提示人工

---

## 五、安装状态

| 组件 | 状态 | 版本 |
|------|------|------|
| Python qzone-api | ✅ 已安装 | — |
| Python playwright | ⏳ 安装中 | — |
| Node agent-browser | ✅ 已安装 | v0.27.0 |
| Node playwright-cli | ✅ 已安装 (deprecated) | v0.262.0 |
| Node @playwright/cli | ⬜ 待安装 | — |
| Python enhanced_detector | ✅ 已实现 | — |
| Python qzone_friend_feed | ✅ 已实现 | ~580行 |

---

## 六、测试命令

```bash
# 测试 QQ空间好友动态 (独立运行)
python scripts/qzone_friend_feed.py --login  # 首次登录
python scripts/qzone_friend_feed.py --count 20  # 获取20条

# 测试 Playwright 微信抓取
python scripts/playwright_scraper.py

# 测试完整的增强检测
python main_controller.py --mode all

# 运行全量测试
python -m pytest tests/test_enhanced_system.py -v
```
