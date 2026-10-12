# 语义检索 demo 说明（路演一页纸）

> hfut_info_monitor · 2026 创新大赛 · 省赛 10/25

## 一句话技术点

**三级引擎：BM25 关键词层 + 同义词扩展 + 本地向量语义层（qwen3-embedding:0.6b），RRF 融合排序，自动降级链。全部跑在本机，完全离线可用、零 API 成本、零新增依赖（全部 Python 标准库）。**

| 层 | 作用 | 引擎标识 |
|---|---|---|
| BM25 关键词层 | 精确字面匹配，标题命中加权 | `bm25` |
| 同义词扩展 | 口语词映射（保研→推免、补助→奖助学金） | `+synonym` |
| 本地向量语义层 | qwen3-embedding:0.6b 语义相似度召回 | `+vector` |
| LLM 改写（可选） | qwen2:7b 查询扩写，**本机未装，自动跳过** | `+ollama` |

**融合公式（RRF，Reciprocal Rank Fusion）**：
`score(d) = Σ_r w_r / (K + rank_r(d))`，K=60，w_bm25=0.5，w_vec=0.5
两路排名互补：关键词层保精确，向量层补语义。

## 路演话术

> **"这套检索完全跑在本地，零 API 成本、离线可用。"**
> 257 条归档通知断网也能搜，评委现场拔网线照样能用。

## 三档降级（评委必问：断了怎么办）

| 场景 | 行为 | 引擎标识 |
|---|---|---|
| 正常 | 三级全开 | `bm25+synonym+vector` |
| Ollama 关闭 / 模型缺失 / 超时 | 熔断后静默退回关键词层，0.1s 返回 | `bm25+synonym` |
| 无任何依赖 | 纯 BM25 仍可用 | `bm25` |

熔断器：某能力首次失败后进程内不再重试，不会每次查询白等超时。

## 启动命令

```bash
# 前置（首次）：Ollama 已启动且已 pull qwen3-embedding:0.6b
ollama serve

# CLI 检索
.venv/Scripts/python.exe scripts/semantic_search.py "出国交流机会" -n 5
.venv/Scripts/python.exe scripts/semantic_search.py "补助" -n 5 --json
.venv/Scripts/python.exe scripts/semantic_search.py "补助" -n 5 --no-vector   # 关向量层看对照

# Web demo（浏览器打开 http://127.0.0.1:8765）
.venv/Scripts/python.exe scripts/search_demo_server.py

# 重跑语义增益对照测试（生成运行证据）
.venv/Scripts/python.exe scripts/semantic_ab_test.py
```

API（供路演/外部系统调用）：
`GET http://127.0.0.1:8765/api/search?q=出国交流机会&n=5`
返回含每条的 `score`（RRF）、`bm25`（关键词分）、`cosine`（语义相似度）。

## 演示话术（3 个示例查询）

| 查询词 | 预期结果 | 话术 |
|---|---|---|
| **出国交流机会** | 纯关键词**零命中**，向量层召回"新加坡社科大学代表团来访""联合培养拔尖人才" | "这是最直观的对比——库里根本没有'出国'两个字，纯关键词检索一条都搜不到；但我们的语义层把它找出来了。关键词层负责精确匹配，向量层负责'你搜的意思'。" |
| **补助** | 命中"自动化81奖助学金"颁奖（cos≈0.60） | "学生口语说'补助'，通知里写的是'奖助学金'。同义词层做映射，向量层再从 257 条里确认哪个语义最接近，Top1 相关度 0.60。" |
| **竞赛报名** | 命中智能建造竞赛等获奖/报名通知 | "257 条通知秒级检索，最相关的排最前，每条带日期、来源和原文链接。同一个接口既是网页 Demo，也是标准 JSON，其他系统可直接接。" |

**语义增益对照实测**：见 `docs/参赛材料/运行证据/semantic_search_语义增益对照_20261012.txt`
（5 组"字面不重合、语义等同"查询，3 组 Top1 变化，1 组由零命中变为成功召回）

## 性能说明（诚实口径）

- 全库向量索引构建一次约 **3-4 分钟**，落盘缓存到 `data/.semantic_vector_cache.json`
- 之后每次查询仅对**查询句**做 1 次本地嵌入，约 **2.7s**（纯 CPU 推理）
- 关键词层单独跑 **0.05s**
- 路演建议：提前构建一次缓存，现场直接秒级

## 已知局限

- 嵌入模型为 **0.6B 轻量版**，cos 值域集中在 0.35-0.65，语义区分度有限（换 qwen3-embedding:4b 可显著提升，仅需换 `EMBED_MODEL` 常量）
- LLM 改写层依赖 qwen2:7b，**本机未安装，已自动跳过**（`+ollama` 不出现属正常）
- 部分公众号条目（wechat_official）原始标题是正文片段，展示层已做标题兜底回退
- bigram 分词对英文长查询较弱（项目场景以中文为主，影响小）
- 检索库仅本地归档 257 条（随归档自动增长，向量缓存按内容指纹自动失效重建）
- `data/.semantic_vector_cache.json` 为本地派生产物（3.4MB），已加 `.` 前缀且建议加入 `.gitignore`；换嵌入模型或数据变更会自动重建

## 路演前检查清单

1. `ollama serve` 已启动，`ollama list` 能看到 `qwen3-embedding:0.6b`
2. 向量缓存已生成（`ls data/.semantic_vector_cache.json`），避免现场 3-4 分钟冷建
3. 想现场演示降级：`HFUT_OLLAMA_PORT=19999 .venv/Scripts/python.exe scripts/search_demo_server.py`（另开窗口对比）