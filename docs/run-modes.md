# 运行模式说明

本文专门说明当前项目支持的三种主运行模式，以及输入文件应该如何提供。

## 1. 普通 intent 模式

适用于传统 user intent 数据。

输入文件放在 `paths.intents_file`，每行至少包含：

```jsonl
{"id": "intent_1", "natural_language_intent": "帮我总结最近的 AI 新闻"}
```

运行时会进入现有 user loop：

1. LLM 生成下一条 query
2. OpenClaw 执行一轮交互
3. 重复直到任务完成或达到 `generation.max_turns`

示例：

```bash
INTENTS_FILE=data/intents.jsonl \
python scripts/run_generation.py --concurrent 4
```

## 2. 纯 query 模式

适用于搜索问答、query-only 数据，不需要先跑 intent。

这时仍然使用 `paths.intents_file`，但文件内容改成 direct query 数据。支持两种常见格式：

```jsonl
{"id": "query_1", "query": "2025 年值得关注的 AI Agent 产品有哪些？"}
{"id": "query_2", "question": "2025 年最强多模态模型有哪些？", "answer": "可选参考答案"}
```

运行行为：

- `query` / `question` 会被归一化成 `direct_query`
- 不再走 LLM user loop
- 直接把 query 发给 OpenClaw
- 仍然会归档 session，并转换 middle format

示例：

```bash
INTENTS_FILE=data/merged_data_sample_20.jsonl \
APPEND_QUERY_ENABLED=false \
python scripts/run_generation.py --concurrent 4
```

说明：

- 纯 query 模式下，`generation.intents_per_session` 仍然有效
- 它控制“多少条 query 归到同一个 session 后再收口”
- 一般建议把 `APPEND_QUERY_ENABLED=false`，避免 query 任务结束后又额外追加一条 query

## 3. intent + 收口追加 query 模式

适用于“先跑一批普通 intent，在 session 正常收口前补一条搜索 query”的场景。

这时需要两个输入文件：

- `paths.intents_file`：主 intent 文件
- `generation.append_query_file`：query 池文件

配置示例：

```yaml
generation:
  intents_per_session: 3
  append_query_enabled: true
  append_query_file: "data/merged_data_sample_20.jsonl"

paths:
  intents_file: "data/intents.jsonl"
```

对应命令：

```bash
INTENTS_FILE=data/intents.jsonl \
INTENTS_PER_SESSION=3 \
APPEND_QUERY_ENABLED=true \
APPEND_QUERY_FILE=data/merged_data_sample_20.jsonl \
python scripts/run_generation.py --concurrent 4
```

行为说明：

- 只有当 `append_query_enabled=true` 且 `append_query_file` 非空时才启用
- 当前 session 只要是正常 finalize，就会在 finalize 前固定追加 1 条 query
- 追加 query 来自 `append_query_file`，会先筛出其中的 `direct_query` 记录
- 追加 query 写入同一个 session 轨迹与 batch metadata
- 追加 query 不会新增一个独立 progress item

## 输入记录的自动归一化规则

主流程会把输入统一规范成 task：

- 有 `natural_language_intent`：视为 `intent`
- 有 `query` 或 `question`，且没有 `natural_language_intent`：视为 `direct_query`
- 如果没有显式 `id`，会自动生成稳定 id
- 如果有 `answer` 字段，会写入 `metadata.reference_answer`

## 该怎么选

- 只有 intent 数据：用普通 intent 模式
- 只有搜索 query 数据：用纯 query 模式
- 想在每个 session 收口时补一条 query：用 intent + 收口追加 query 模式
