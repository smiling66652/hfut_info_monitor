# AI 任务分工策略：本地模型 vs 云端 API

> 合工大信息监控系统 v4.0 | 更新日期: 2026-06-08

## 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                     信息处理流水线                             │
│                                                              │
│  采集 → 过滤 → L1规则 → L2本地AI → [可选]L3云端 → 摘要/推送 │
└──────────────────────────────────────────────────────────────┘
```

**核心原则**: 本地算力优先，云端按需兜底。每层只在自己能力不足时才向上请求。

---

## L1 规则引擎（零 AI 成本）

| 职责 | 实现模块 | 说明 |
|------|---------|------|
| 关键词匹配评分 | `smart_digest.analyze_with_keywords()` | 保研+5 / 通知+3 / 活动+1 |
| 信息分类过滤 | `EnhancedDetector` (filter.py) | 竞赛/二手/有用/垃圾 判定 |
| URL 去重、格式校验 | `archive.py` + 各采集器 | 纯字符串/正则操作 |
| 推送渠道路由 | `push.py` | 根据分类决定走邮件/QQ/Notion |

**特点**:
- 零延迟，零成本，100% 可靠
- 处理全量数据的第一道关卡
- 关键词得分 < 4 的内容直接归档，不进入 AI 层

---

## L2 本地 Ollama（主力 AI）

| 属性 | 配置 |
|------|------|
| **默认模型** | `qwen:1.7b`（可通过 `.env` 中 `OLLAMA_MODEL` 覆盖） |
| **备用模型** | `qwen2.5:0.5b`（更低资源占用） |
| **API 端点** | `http://localhost:11434` |
| **管理器** | `ollama_manager.OllamaManager` 单例 |

### 分配给 L2 的任务

| 任务 | 方法 | 频率 | 说明 |
|------|------|------|------|
| **内容重要性评分** | `smart_digest.analyze_with_local_ai()` | 每条信息 | 输出 0-10 分 + 分类 + 理由 |
| **每日摘要生成** | `ollama_manager.generate_summary()` | 每天 1 次 | 汇总当日所有信息为摘要文本 |
| **文本分类/聚类** | `smart_digest` 内部调用 | 每条信息 | 将内容归类到预定义分类 |

### 评分逻辑

```
关键词得分(kw) × 0.4 + 本地AI得分(ai) × 0.6 = 综合分

综合分 ≥ 7  → 触发 L3 云端深度分析（预留）
综合分 < 7  → 直接输出结果，推送/归档
```

### 回退策略

```
L2 Ollama 正常 → 使用本地模型推理（免费、隐私）
     ↓ 失败
OllamaManager.generate_summary() 自动回退到 DeepSeek Cloud API
     ↓ 也失败
输出规则引擎的原始结果，标记 "AI分析不可用"
```

> **注意**: `generate_summary()` 已经内置了 DeepSeek cloud fallback（见 `ollama_manager.py`），无需额外配置。

---

## L3 云端 DeepSeek API（增强/兜底）

| 属性 | 配置 |
|------|------|
| **提供商** | DeepSeek（通过 OllamaManager 内置 fallback） |
| **触发条件** | ① Ollama 不可用时自动降级 ② 综合评分≥7 时深度分析（TODO） |

### 分配给 L3 的任务

| 任务 | 状态 | 说明 |
|------|------|------|
| **摘要生成降级** | ✅ 已实现 | `ollama_manager.generate_summary()` 内置 |
| **高分内容深度分析** | 🚧 TODO | `smart_digest.analyze_with_cloud_ai()` 待实现 |
| **复杂推理/语义理解** | 🚧 TODO | 多轮对话、长文理解等 |

### L3 设计规范（待实现）

```python
# smart_digest.py - analyze_with_cloud_ai() 应实现：
def analyze_with_cloud_api(self, text: str, user_profile: dict) -> dict:
    """
    仅在以下条件满足时调用：
    1. L2 综合得分 >= 7（高价值信息才值得花钱）
    2. 或 L2 Ollama 不可用且需要摘要生成

    返回结构：
    {
        'score': 8,              # 深度评分
        'category': '夏令营',     # 精细分类
        'reason': '...',          # 详细理由
        'layer': 'cloud_ai',
        'actionable_items': [...], # 可执行建议（可选）
        'deadline_aware': True,    # 是否含截止日期（可选）
    }
    """
```

---

## 服务启停与模式对应表

`environment_bootstrap.py` 根据执行模式智能判断需要哪些服务：

| 执行模式 | 需要 Ollama | 需要 NapCat | 说明 |
|----------|:-----------:|:-----------:|------|
| `all` | ✅ | ✅ | 完整流程 |
| `collect` | ❌ | ❌ | 纯采集，不需要 AI |
| `analyze` | ✅ | ❌ | 分析需要本地 AI |
| `digest` | ✅ | ❌ | 摘要生成需要 AI |
| `sync` | ❌ | ❌ | 同步到知识库 |
| `status` | ❌ | ❌ | 只检查状态 |
| `services-start` | ✅ | ✅ | 手动启动所有服务 |
| `services-stop` | ❌ | ❌ | 手动停止所有服务 |

---

## 性能参考

| 模型 | 显存占用 | 推理速度(约) | 适用场景 |
|------|---------|-------------|---------|
| qwen2.5:0.5b | ~0.4 GB | < 1s/条 | 低配笔记本、快速筛选 |
| qwen:1.7b | ~1.2 GB | 2-3s/条 | 日常使用（推荐） |
| DeepSeek Cloud | 0（远程） | 1-2s/条 | 降级兜底、深度分析 |

---

## 快速命令参考

```bash
# 完整运行（自动启动→执行→关闭）
python scripts/environment_bootstrap.py

# 仅检查环境
python scripts/environment_bootstrap.py --check-only

# 只采集（不需要任何 AI 服务）
python scripts/environment_bootstrap.py --mode collect

# 只生成摘要（只需 Ollama）
python scripts/environment_bootstrap.py --mode digest

# 完整运行但跳过 NapCat（不推 QQ）
python scripts/environment_bootstrap.py --skip-napcat

# 跳过 Ollama（纯规则引擎模式）
python scripts/environment_bootstrap.py --skip-ollama
```
