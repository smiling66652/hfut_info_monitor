# 语义检索 demo 说明（路演一页纸）

> hfut_info_monitor · 2026 创新大赛 · 省赛 10/25

## 一句话技术点

**手写 BM25 排序 + 内置同义词扩展 + 可选本地 Ollama qwen2:7b 查询扩写，三级引擎自动降级链，零 API 成本、零新增依赖（全部 Python 标准库）。**

- 中文分词用 bigram 切分，不依赖 jieba
- 口语词内置同义词层：搜"保研"能命中"推免"（引擎显示 `bm25+synonym`）
- Ollama 在线 → 查询再叠加本地大模型扩写（引擎显示 `bm25+synonym+ollama`）
- Ollama 离线 → 5 秒超时静默降级，不报错（引擎显示 `bm25` / `bm25+synonym`）
- 数据：本地归档 257 条真实通知（新闻要闻 108 / 通知公告 82 / 公众号 41 / 宣城校区 28）

## 启动命令

```bash
# CLI 检索
.venv/Scripts/python.exe scripts/semantic_search.py "保研" -n 5
.venv/Scripts/python.exe scripts/semantic_search.py "奖学金" -n 5 --json

# Web demo（浏览器打开 http://127.0.0.1:8765）
.venv/Scripts/python.exe scripts/search_demo_server.py
```

API（供路演/外部调用）：`GET http://127.0.0.1:8765/api/search?q=保研&n=5`

## 演示话术（3 个示例查询）

| 查询词 | 预期命中 | 话术 |
|---|---|---|
| 保研 | 命中"推免/研究生培养"相关通知/新闻 | "学生搜'保研'两个字，系统内置同义词层直接映射到'推免'，BM25 找到研究生培养相关通知；再叠加本地大模型还能自动扩写更多相关词——本地 LLM，不出网、不花钱。" |
| 竞赛报名 | 命中学科竞赛、竞赛报名通知 | "257 条归档通知秒级检索，标题命中加权，最相关的排最前面，每条带日期、来源和原文链接。" |
| 奖学金 | 命中各类奖助学金评选通知/新闻 | "同一套检索逻辑既是命令行工具也是网页 Demo，还是标准 JSON 接口——可以直接被别的系统复用，这就是平台化能力。" |

## 已知局限

- 检索库仅本地归档 257 条（随归档自动增长）
- 部分公众号条目（wechat_official）原始标题本身是日期片段，检索命中后展示标题观感一般（数据源问题，非检索问题）
- bigram 分词对英文长查询较弱（项目场景以中文为主，影响小）
- Ollama 改写依赖本机 qwen2:7b，未部署时自动降级（同义词层仍生效）；路演前建议 `ollama serve` 并 `ollama pull qwen2:7b`
