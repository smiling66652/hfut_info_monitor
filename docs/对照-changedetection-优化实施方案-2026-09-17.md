# 对照 changedetection.io 的优化实施方案

> 生成时间：2026-09-17
> 分析对象：`dgtlmoon/changedetection.io`（Apache-2.0，33,445★，已在 `D:\_ref\changedetection.io` 克隆待查）
> 对照对象：`D:\项目整理\01-代码项目\hfut_info_monitor`（25,975 行 / 57 py 文件）

---

## 一、先说结论：走「借鉴为主 + 单点二次开发」，不要整体 fork

| 路线 | 判断 | 理由 |
|---|---|---|
| **A. 整体 fork 改造** | ❌ 不推荐 | 它的场景是通用/电商（价格、补货、可用性），你是高校通知。fork 后会背上它的技术债与迭代节奏，且它的代码量（9,738 行 Python + 大量前端）改起来比自己写更慢 |
| **B. 借鉴架构 + 单点二次开发** | ✅ **推荐** | 抄它的 7 个设计（下面详解），并在你的 `processors` 层加一个高校专用处理器。工作量可控，护城河仍在你自己手里 |
| **C. 只用它做能力基准线** | ✅ 可作为底线 | 不写代码时，用它当"我该有什么功能"的对照表 |

**一句话**：它解决了"网页变了怎么比对"，你要解决的是"高校通知意味着什么"。前者抄，后者自己造。

---

## 二、它的 7 个设计，值得直接抄（附代码位置）

### 1. 原始内容 MD5 短路 —— 省掉 90% 的无效处理

`changedetectionio/processors/base.py:411`

```python
def get_raw_document_checksum(self):
    checksum = None
    if self.fetcher.content:
        checksum = hashlib.md5(self.fetcher.content.encode('utf-8')).hexdigest()
    return checksum
```

配套 `read_last_raw_content_checksum()` / `update_last_raw_content_checksum()`（第 48–100 行），把 checksum 写进 `data_dir/last-checksum.txt`。**抓完先算 MD5，和上次一样就直接返回，不进解析流程。**

**你的现状**：`grep -c "difflib|hashlib|md5|checksum" hfut_scanner.py` → **只有 1 处**，等于没有。每次扫描都全量解析 18 个栏目。
**改法**：在 `fetch_with_fallback()` 返回后、进入 `extract_items_*` 前加一道 checksum 闸门。改动量约 20 行。

---

### 2. 处理器插件化 —— 这是你二次开发的正确切入点

`processors/base.py:15` 定义抽象基类 `difference_detection_processor`，核心是抽象方法：

```python
@abstractmethod
def run_changedetection(self, watch, force_reprocess=False):
    ...
```

内置三个实现：
- `processors/text_json_diff/` —— 通用文本/JSON 比对（默认）
- `processors/restock_diff/` —— 电商补货 + 价格区间检测
- `processors/image_ssim_diff/` —— 图像结构相似度（SSIM）对比

**这告诉你一件事**：它是把"变化意味着什么"做成可插拔的。**你完全可以加第四个：`processors/notice_diff/`** —— 高校通知专用处理器，判断"这条通知是新增、被修改、还是撤回"。这是你的护城河，不用碰它的任何既有代码。

---

### 3. Apprise：一行依赖解决 214 个通知渠道

`requirements.txt:43` → `apprise==1.13.1`

实测（本次已装包验证）注册了 **214 个 URL scheme**，包括：

| 渠道 | scheme | 国内可用 |
|---|---|---|
| 钉钉 | `dingtalk://` | ✅ |
| 企业微信机器人 | `wecombot://` | ✅ |
| 微信 | `wechat://` | ✅ |
| WxPusher | `wxpusher://` | ✅ |
| 飞书 | `lark://` `feishu://` | ✅ |
| PushDeer | `pushdeer://` | ✅ |
| PushPlus | `pushplus://` | ✅ |
| Bark (iOS) | `bark://` | ✅ |
| Chanify | `chanify://` | ✅ |
| Spug 推送 | `spugpush://` | ✅ |
| ntfy / Discord / Slack / Telegram | 同名 scheme | ✅ |

**你的现状**：`hfut_email.py` 手写 SMTP（`send_email()` 在第 318 行），仅邮件一个渠道。
**收益**：加一行依赖，用户填一个 URL 字符串就能用上全部渠道。**这是全方案里性价比最高的一条。**

---

### 4. LLM 成本控制三件套 —— 你在商业化时一定会撞上

`changedetectionio/llm/evaluator.py` 里有三个你在别处很少见到的设计：

```python
def get_global_token_budget_month(datastore=None):   # 第417行 全局月度 token 预算
def accumulate_global_tokens(datastore, tokens):     # 第460行 累计消耗
def _estimate_cost_usd(model, input_tokens, output_tokens):  # 第440行 成本估算
def _cached_system(text, model=''):                  # 第114行 prompt 缓存
```

还有 `llm/bm25_trim.py`：

```python
def trim_to_relevant(text, query, max_chars=MAX_CONTEXT_CHARS):
    """用 BM25 把长文本裁到最相关的片段再喂模型"""
```

**为什么重要**：diff 结果动辄几千行，直接喂 LLM 会烧钱。它先用 BM25 裁到最相关部分。**这是"LLM 功能能不能低价跑起来"的关键。**
**你的现状**：用本机 Ollama（零 API 成本），但一旦上云服务多用户，成本会失控。

---

### 5. 忽略噪音的 diff —— 你最缺的能力

`diff/__init__.py` 的三个关键函数：

```python
def render_inline_word_diff(before_line, after_line, ignore_junk=False, ...)  # 第79行
def render_nested_line_diff(before_line, after_line, ignore_junk=False, ...)  # 第214行
def customSequenceMatcher(...)                                                 # 第309行
def render_diff(...)                                                           # 第424行
```

`ignore_junk` 参数专门用来忽略"访问计数器变了、时间戳变了"这类无意义变更。

**你的现状**：项目里**完全没有 diff 能力**。只能判断"列表页出现了新的标题"，无法判断"已抓到的通知正文被改了什么"。

---

### 6. 只监控指定区域 —— 降噪的物理手段

它支持 `include_filters`（CSS/XPath 精确到元素）+ `subtractive_selectors`（排除区域）。
**你的现状**：`CHANNELS` 里每个栏目是一个列表页 URL，提取整页所有链接再过滤（`extract_items_generic`）。

> 顺带一提：你的 `extract_items_generic()` 用 URL 形态（`/YYYY/MMDD/`）而非 CSS 选择器来识别详情页，**这个思路在"多套 CMS 并存"的高校场景下比 CSS 选择器更抗变化**——这是你的优势，别丢掉。

---

### 7. 路径穿越防护 —— 做多用户时必踩的坑

`processors/base.py:297` 的 `_resolve_watch_config_path()`：

```python
# 拒绝含路径分隔符的文件名，realpath 双向校验
if filename != os.path.basename(filename) or '/' in filename or '\\' in filename:
    logger.error(f"Refusing watch config filename with a path component: {filename!r}")
```

它的注释里写明：这个值来自 `watch['processor']`，**是不可信输入**，历史上曾被 `../../../tmp/pwned` 逃逸过。

**你的现状**：单用户，还不需要。但一旦让用户自定义监控源，用户输入的域名/路径会直接参与文件路径拼接。

---

## 三、对照优化清单（按优先级 + 风险）

### P0 —— 立刻做，低风险高收益

| # | 优化项 | 改动位置 | 工作量 | 风险 |
|---|---|---|---|---|
| 1 | 引入 apprise 替代单一 SMTP | 新建 `hfut_site/hfut_notify.py`，`hfut_email.py` 保留兼容 | ~80 行新代码 | 低（新增模块，不动旧逻辑） |
| 2 | 加内容 MD5 短路 | `hfut_scanner.py` 的 `fetch_with_fallback()` 之后 | ~20 行 | 低 |
| 3 | 栏目配置改外部 JSON | `hfut_scanner.py:220` 的 `CHANNELS` 表（结构为 `(名称, URL, 域名, 栏目类型, CMS类型, 是否需浏览器)`） | ~40 行 | 中（要保证向后兼容） |

### P1 —— 差异化核心，决定产品价值

| # | 优化项 | 说明 | 工作量 |
|---|---|---|---|
| 4 | **「已有通知被修改」检测** | 对已抓条目存正文快照 + 算哈希，变了就告警。这是通用工具做不到的 | ~150 行 |
| 5 | DDL 提取 + 倒计时 | 从通知正文/标题抽截止日期，生成倒计时提醒 | ~100 行 |
| 6 | diff 降噪（ignore_junk 思路） | 借鉴 `customSequenceMatcher` 的思路做正文段落级比对 | ~120 行 |

### P2 —— 规模化才需要

| # | 优化项 |
|---|---|
| 7 | 用户体系 + 多租户 |
| 8 | Web 配置界面 |
| 9 | 配额与计费 |
| 10 | 多端（uni-app X） |

---

## 四、「熟人生意」这个想法——其实是最务实的一条路

你提到"卖服务，或者朋友之间使用，熟人生意"。**这不是退而求其次，这是当前阶段的最优解。**

### 为什么说它最优

| 维度 | 做 SaaS 产品 | 做熟人服务 |
|---|---|---|
| 微信支付资质 | 必须办个体户/企业主体 | **不需要**，群里转账即可 |
| 小程序长期订阅消息 | 个人主体拿不到 | **不需要**，直接用服务号/邮箱/PushDeer |
| ICP 备案 | 必须 | 用境外或已有域名可先跑 |
| 多租户系统 | 必须做 | **不需要**，你手工帮他配源 |
| 服务器成本 | 要扛并发 | 一台旧电脑/轻量云就够 |
| 获客 | 要投内容、做增长 | **熟人直接信任** |
| 冷启动速度 | 3–6 个月 | **1 周内就能收到第一笔钱** |

### 现实的产品形态

**形态：微信群 + 一个极简配置页 + 你的人工兜底**

- 用户群：同班/同专业/社团的朋友，起步 5–20 人
- 交付：他告诉你"我想关注保研和竞赛"，**你手动帮他配好源**（这是 SaaS 做不到的服务溢价）
- 推送：拉一个微信群，或者每人配一个 PushDeer/企业微信机器人 webhook
- 收款：按学期收，¥50–100/学期（折合 ¥10–20/月，卡在大学生心理线内）

### 必须守住的边界（否则熟人生意会变成免费劳动力）

1. **明码标价，先小人后君子**。开群第一句话写清楚：服务什么、不服务什么、什么时候推、坏了多久修。
2. **不要承诺"绝不漏"**。学校改版、网站维护都会导致漏抓。写清楚"尽力而为"。
3. **限定源的数量**。比如"每人最多 5 个栏目"，否则你会被无穷无尽的"帮我也加一个"拖死。
4. **收钱要干脆**。不收钱的服务，用户不会珍惜，你也不会有动力维护。¥9.9/月 比免费更能让关系健康。
5. **别碰 QQ 群/空间采集这类个人信息密集的功能**。熟人场景下，一旦有人问"你怎么知道我群里说了什么"，关系就崩了。

### 它的天花板与价值

- **收入天花板**：熟人圈子有限，最多做到月入几百到一两千，成不了事业。
- **真实价值**：① 验证"信息不漏"到底有没有人为它付钱（这是最贵的答案）；② 攒真实用户反馈，决定要不要做产品；③ 简历上"独立开发并运营一个信息服务，服务 N 名真实付费用户"比"写了一个爬虫"有力得多。

---

## 五、建议的执行顺序

> **执行状态（2026-09-17 更新）**：第 1 步已落地。
> - ✅ 新增 `hfut_site/hfut_notify.py`（多渠道推送模块，已实测：apprise 1.13.1 / 214 个 scheme）
> - ✅ 新增 `hfut_site/hfut_notify_config.example.json`（配置模板）
> - ✅ `.gitignore` 已加入 `hfut_site/hfut_notify_config.json`（防止密钥入库，已验证生效）
> - ✅ `requirements.txt` 已登记 `apprise>=1.13.1`
> - ⬜ **待你确认**：在项目 venv 执行 `pip install apprise` 后即可启用（未擅自改动你的运行环境）

```
第 1 步（本周）：加 apprise，让自己能推到微信        ← ✅ 已完成
   ↓  意义：先让自己用爽，顺手就有了给别人用的能力
第 2 步（下周）：加 MD5 短路 + 栏目配置外置
   ↓  意义：把系统从"我的机器"变成"能给别人配"
第 3 步（两周内）：加「通知被修改」检测 + DDL 倒计时
   ↓  意义：这是你唯一说得出口"别人做不到"的功能
第 4 步：找 3–5 个最熟的朋友，免费试用 2 周
   ↓  意义：验证需求真伪。没需求就停，别硬做
第 5 步：有 3 个人以上主动问"还有吗" → 开始收 ¥9.9/月
```

**关键判断点在第 4 步**：如果免费试用两周后，有人主动来问"今天怎么没推"，这事就成立；如果没人问，说明"信息不漏"是伪需求，应及时止损。

---

## 六、附：apprise 推送模块参考实现

可直接放进 `hfut_site/hfut_notify.py`，与现有 `hfut_email.py` 并存：

```python
#!/usr/bin/env python3
"""hfut_notify.py - 多渠道推送（基于 apprise）

替换/补充 hfut_email.py 的单 SMTP 方案。
支持钉钉、企业微信、飞书、PushDeer、Bark、邮件等 200+ 渠道。

配置示例（hfut_notify_config.json）：
{
  "urls": [
    "dingtalk://<APIKEY>/<手机号>",
    "wecombot://<bot_key>",
    "pushdeer://<pushkey>",
    "mailto://<QQ邮箱>:<授权码>@smtp.qq.com"
  ]
}
"""

import json
import os

try:
    import apprise
except ImportError:
    raise SystemExit("请先安装：pip install apprise")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "hfut_notify_config.json")


def load_notify_urls(path=CONFIG_PATH):
    """读取推送渠道 URL 列表"""
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f).get("urls", [])


def push(title: str, body: str, url: list = None, body_format: str = "markdown"):
    """推送到所有已配置渠道

    Args:
        title: 标题
        body: 正文，支持 markdown
        url: 覆盖配置中的渠道列表
        body_format: text / markdown / html

    Returns:
        bool: 是否至少有一个渠道成功
    """
    urls = url if url is not None else load_notify_urls()
    if not urls:
        print("[notify] 未配置任何推送渠道，跳过")
        return False

    a = apprise.Apprise()
    for u in urls:
        if not a.add(u):
            print(f"[notify] ⚠ 渠道无效，已跳过：{u}")

    if not len(a):
        print("[notify] 没有任何有效渠道")
        return False

    ok = a.notify(title=title, body=body, body_format=body_format)
    print(f"[notify] {'✅ 推送成功' if ok else '❌ 推送失败'}（{len(a)} 个渠道）")
    return bool(ok)


if __name__ == "__main__":
    # 自测
    push("巡检系统测试", "这是一条来自 hfut_notify 的测试消息。\n\n- 渠道正常\n- Markdown 生效")
```

配套 `requirements.txt` 追加一行：

```
apprise==1.13.1
```

**验证方式**（不改动任何现有代码即可测试）：

```bash
# 用项目 venv
.venv\Scripts\python.exe -c "import apprise; print(apprise.__version__)"
```

---

## 七、风险与注意事项

| 项 | 说明 |
|---|---|
| **不要 fork 后抛弃原项目** | 你的 `hfut_info_monitor` 是唯一在跑的生产系统，先以"新增模块"方式演进，稳定后再重构 |
| **`D:\_ref\changedetection.io` 是本次分析用的克隆副本** | 仅作参考，不参与你的项目构建。确认无用后可删除（约 1139 文件） |
| **apprise 的 scheme 是明文凭据** | `dingtalk://`、`mailto://` 里含密钥，配置文件必须进 `.gitignore`，且加密存储 |
| **推送渠道的自定义 URL** | 若未来让用户填自己的 webhook，需要做 SSRF 防护（参考它的 `is_safe_url.py`，你项目里也有类似需求） |
| **`hfut_site/hfut_email_config.json` 仍是明文 SMTP 授权码** | 本次未改动。引入 apprise 时一并迁移到加密配置 |
