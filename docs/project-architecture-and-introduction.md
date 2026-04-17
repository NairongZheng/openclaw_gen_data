# OpenClaw Gen Data 项目详解

## 1. 项目背景

`openclaw_gen_data` 是一个围绕 OpenClaw 构建的数据生成工程，目标是将“用户意图 → 多轮 Agent 交互 → 原始 session 轨迹 → 训练中间格式数据”这一条链路工程化、自动化、可恢复化。

这个项目的直接动机并不是“单次调用一个 Agent 完成任务”，而是为了**批量构造高质量 trajectory 数据**，服务于后续的大模型训练、回放分析、行为审计和数据集建设。

在很多 Agent 训练场景中，最有价值的数据并不是单轮问答，而是：

- 用户意图如何被拆解成多轮 query
- Agent 如何调用工具、如何处理中间结果
- 为什么在某个时刻停止继续追问
- 一条任务轨迹最终如何转成结构化的训练样本

本项目正是为这个目标设计：

1. 从 JSONL 文件中读取大量 task（既支持 intent，也支持 query）
2. 驱动本地 OpenClaw worker agents 与工具系统完成真实交互
3. 将完整 session 归档为原始轨迹
4. 将归档轨迹转换为适合训练使用的 middle format
5. 在并发、重试、恢复、配置漂移、session 续跑等复杂场景下仍保持稳定

从工程定位上看，它不是一个通用聊天机器人项目，而是一个**面向 Agent 轨迹采集与训练数据构建的批处理系统**。

---

## 2. 项目核心目标

这个项目解决的问题可以概括为四类：

### 2.1 数据生成自动化

给定一批输入任务，系统能够自动完成：

- 任务读取
- Agent 初始化
- query 生成
- OpenClaw 交互
- session 归档
- middle format 转换
- 进度记录与汇总输出

### 2.2 真实运行轨迹保真

本项目尽量避免只生成“模拟数据”，而是强调：

- 真实使用 OpenClaw agent
- 真实触发 OpenClaw 工具系统
- 真实保留工具调用与工具结果
- 真实从 session 中解析消息序列
- 真实捕获 runtime 里最终发送给模型的工具定义

### 2.3 批量并发与断点恢复

实际数据生成常常规模较大，运行时间长，且中途可能遇到：

- OpenClaw 服务不稳定
- 配置文件污染
- worker 中途失败
- 程序中断后需要续跑
- 多个 intent 共用一个 session 时的中间状态丢失

因此项目在设计上强调：

- 多 worker 并发
- 进度文件 resume
- worker runtime snapshot
- session 延迟收口
- OpenClaw runtime 自愈与自动重启

### 2.4 输出可训练的中间格式

原始 session 虽然信息完整，但不适合直接喂给训练流程。因此项目提供专门的转换器，将轨迹转换为结构更清晰的 JSON：

- `messages`
- `tools`
- `skills`
- `final_output`
- `metadata`

最终形成统一中间格式，便于后续 SFT / 分析 / 回放。

---

## 3. 技术栈与依赖形态

项目当前主要基于以下技术栈：

### 3.1 Python 主体工程

核心逻辑基本都在 Python 中实现，包括：

- 配置加载
- 任务读取与标准化
- OpenClaw CLI 封装
- LLM user loop
- session 解析
- middle format 转换
- runtime recovery
- worker snapshot
- agent 初始化与 runtime metadata 刷新

### 3.2 OpenClaw 本地运行时

项目强依赖本地 OpenClaw 环境。OpenClaw 在这里承担的是：

- agent 管理
- workspace 隔离
- session 持久化
- 工具系统运行
- gateway 模型访问

本项目并不重写 OpenClaw，而是通过 CLI 与配置文件对其进行编排。

### 3.3 OpenAI-Compatible LLM API

项目中存在两类模型调用：

1. **OpenClaw 底层模型**：用于 OpenClaw agent 自身执行任务
2. **外部 LLMClient**：用于 user loop 决定“下一条 query 是什么 / 是否完成”

这使得系统天然具备“双模型职责分离”的特点：

- OpenClaw 负责实际 agent 执行
- LLMClient 负责高层 query 调度与完成判定

### 3.4 Node.js 辅助链路

虽然当前主链路已经改为 runtime probe 捕获 tools，但仓库仍保留 `dump_tools.mjs` 作为静态扫描和离线比对工具。

### 3.5 Docker / GitHub Actions

项目提供：

- 容器化运行入口
- Dockerfile
- CI 镜像构建 workflow
- 多架构镜像构建与按需发布

这意味着项目不仅能本地跑，也具备部署与可移植性。

---

## 4. 项目整体架构

从逻辑上，这个项目可以拆成 6 个层级：

### 4.1 输入层

负责接收和标准化任务输入。

核心文件：

- `src/intent_loader.py`

支持两类任务：

- `intent`：以 `natural_language_intent` 为核心
- `direct_query`：以 `query` 或 `question` 为核心

标准化后统一得到：

- `id`
- `task_type`
- `natural_language_intent`
- `query`（如果适用）
- `metadata`

### 4.2 调度层

负责 orchestrate 全局运行流程。

核心文件：

- `scripts/run_generation.py`
- `src/generation_support.py`

职责包括：

- 读取配置
- 初始化 worker agents
- 加载共享 runtime metadata
- 构建任务队列
- 启动多线程 worker
- 汇总结果
- 触发 runtime recovery

### 4.3 决策层（User Loop）

负责决定每一轮给 OpenClaw 发送什么 query。

核心文件：

- `src/llm_client.py`
- `prompts/user_model_system_prompt.txt`

逻辑是：

- 输入原始用户 intent
- 输入 persona
- 输入 conversation history
- 由外部 LLM 生成：
  - 任务是否已经完成
  - 如果未完成，下一条最佳 query 是什么

这层相当于“模拟用户推进任务”的逻辑核心。

### 4.4 执行层（OpenClaw Runtime）

负责真正与 OpenClaw agent 交互。

核心文件：

- `src/openclaw_wrapper.py`
- `scripts/init_agents.py`

职责包括：

- agent 创建与配置
- workspace/state 路径管理
- 全局 provider 配置
- 全局 skills 配置
- session 重置、归档、恢复
- worker workspace 模板克隆
- runtime tools probe

### 4.5 恢复层

负责在异常情况下保住运行进度和环境稳定性。

核心文件：

- `src/worker_snapshot.py`
- `src/agent_runtime.py`
- `src/runtime_recovery.py`
- `src/fs_utils.py`

能力包括：

- worker 级 runtime snapshot
- pending session 恢复
- OpenClaw 配置 baseline 回滚
- gateway 重启
- 只读文件权限修复与安全删除

### 4.6 转换层

负责将原始 session 转换为训练可用的中间格式。

核心文件：

- `src/session_parser.py`
- `src/converter.py`

这里完成：

- JSONL session 解析
- assistant / user / toolResult 提取
- tool_calls 与 tool_results 对齐
- reasoning 内容提取
- system prompt 注入
- tools / skills / metadata 汇总

---

## 5. 运行链路详解

下面按一次完整任务生成的生命周期来解释。

### 5.1 初始化阶段

入口：`scripts/init_agents.py`

初始化的动作包括：

1. 根据配置确保 worker agents 存在
2. 若需要，删除旧 agents 并重建
3. 为每个 agent 分配独立 workspace
4. 配置 worker 工具白名单
5. 配置全局 provider 与全局 skills
6. 生成共享 workspace 快照
7. 可选刷新共享 runtime metadata（`--refresh-tools`）

这里有一个很重要的工程点：

- worker 并不是每次手工单独配置，而是统一管理
- 新建 agent 可以复用模板 workspace
- 初始化结果会影响后续整个数据生成流程的稳定性

### 5.2 runtime metadata 刷新阶段

当执行：

```bash
python scripts/init_agents.py --refresh-tools
```

当前主链路不是再依赖纯静态扫描，而是：

1. 启动一个短生命周期本地 proxy
2. 创建一个临时 probe agent
3. 发起一次最小真实请求
4. 捕获 OpenClaw 最终外发到模型的共享 runtime metadata（`tools` + `system_prompt`）
5. 将结果写入 `output/worker_snapshots/runtime_metadata/runtime_metadata.json`
6. 同时将 `runtime_probe_*` 调试快照写入 `output/worker_snapshots/runtime_metadata/probe/`

对应文件：

- `scripts/init_agents.py`
- `src/runtime_tools_proxy.py`
- `tools/tool-inspector/README.md`

这是项目最近非常重要的一次演进，因为它解决了“静态提取工具定义”和“真实运行时工具定义”不一致的问题。

### 5.3 generation 主循环

入口：`scripts/run_generation.py`

主流程大体如下：

1. 读取配置
2. 准备输出目录
3. 确保 worker agents 存在
4. 备份 OpenClaw runtime baseline
5. 读取 tasks
6. 构造任务队列
7. 启动多个 worker 并发执行
8. 汇总结果与 summary
9. 正常退出时清理 agents

### 5.4 单个 worker 的处理方式

一个 worker 由 `worker_loop()` 驱动，其行为是：

- 单个 worker 内串行消费任务
- 多个 worker 之间并发
- 每个 worker 绑定一个固定 agent
- 每个 worker 可连续处理多个 intent，并按 `intents_per_session` 决定何时收口 session

这样设计的意义是：

- 并发简单稳定
- session 状态与 workspace 状态更容易按 worker 管理
- snapshot 恢复也更容易实现

### 5.5 单个 task 的处理方式

核心函数：`process_intent()`

当任务是 `intent` 时：

1. 初始化 conversation history
2. 调用 `LLMClient.generate_next_query()`
3. 如果 LLM 判定完成，则停止
4. 如果未完成，拿到下一条 query
5. 将 query 发给 OpenClaw
6. 从 OpenClaw 响应中抽取 assistant 文本
7. 更新 history
8. 重复直到完成或达到最大轮次

当任务是 `direct_query` 时：

- 不走 user loop
- 直接把 query 发给 OpenClaw
- 成功后归档 session

### 5.6 session 收口与延迟物化

一个非常有意思的设计是：**session 可以延迟收口**。

如果 `intents_per_session > 1`，则：

- 前几个 intent 的结果先挂在 `pending_session_results`
- 同时保存 worker runtime snapshot
- 最后一个 intent 完成时，才真正归档 session、转换 middle format

优点：

- 保持一个 session 中包含多个连续 intent 的真实轨迹
- 使“单 session 多任务”的训练数据成为可能
- 减少每条任务都 reset workspace/session 的开销

### 5.7 转换为 middle format

session 收口后，由 `DataConverter` 完成转换。

输出结构包含：

- `status`
- `session_id`
- `source_intent_ids`
- `messages`
- `tools`
- `skills`
- `final_output`
- `metadata`

其中最关键的是 `messages`：

- user 消息来自 session 原始 message
- assistant 消息保留文本、tool_calls、reasoning_content
- tool 消息保留工具名、tool_call_id、content、success

这使 middle format 接近 OpenAI 风格消息结构，同时又保留项目特定元数据。

---

## 6. 核心模块说明

## 6.1 `src/intent_loader.py`

作用：统一输入任务格式。

亮点：

- 同时支持 `intent` 和 `direct_query`
- 自动生成稳定 ID
- 自动把 `question/answer` 数据归一化
- 对非法 JSONL 记录进行跳过与告警

这是数据入口的一层“容错适配层”。

## 6.2 `src/llm_client.py`

作用：实现 user loop 的高层 query 生成器。

特点：

- 支持不同模型体系的 thinking 参数差异
- 使用统一 JSON response format
- 内置指数退避重试
- system prompt 模板独立存放在 `prompts/`

难点在于：这个 LLM 不是最终执行任务的模型，而是“控制 OpenClaw 任务推进”的调度器。

## 6.3 `src/openclaw_wrapper.py`

作用：封装 OpenClaw CLI 和 agent/session 相关操作。

主要能力：

- 读取与保存 `~/.openclaw/openclaw.json`
- 配置全局 provider / skills
- 配置 agent workspace / tools / skills / model
- 列出和删除 worker agents
- reset / archive / restore session
- 解析 OpenClaw 的混合 stdout/stderr JSON 输出

这是整个项目最核心的“系统边界层”。

## 6.4 `src/session_parser.py`

作用：把 OpenClaw 原始 session JSONL 解析成结构化消息。

核心能力：

- 过滤出 `type=message` 的记录
- 提取 text 内容并清理 OpenClaw CLI 时间戳前缀
- 提取 tool calls
- 提取 tool results
- 提取最后一条完整 agent 响应

最近一个值得注意的细节是：

- 如果工具参数是字符串但不能成功 `json.loads`，项目现在会**原样保留字符串**，而不是伪造包一层 `{"raw": ...}`
- 这保证了 tool call arguments 的“无损保留”

## 6.5 `src/converter.py`

作用：将 session 转换为训练中间格式。

技术点包括：

- OpenAI-style message 构造
- tool_calls 保留结构化参数
- reasoning 内容提取
- system prompt 动态构建
- skills section 渲染
- tools schema 提取与 fallback

这部分本质上承担的是“数据产品化”的任务。

## 6.6 `src/generation_support.py`

作用：为 `run_generation.py` 提供共享逻辑。

内容包括：

- 进度记录器 `ProgressTracker`
- thinking mode 解析
- append query 逻辑
- tools cache 读取
- session metadata 提取
- middle format 路径、session 路径生成
- 最终结果汇总

它把大量杂项逻辑从主脚本里抽离出来，提高了可维护性。

## 6.7 `src/worker_snapshot.py`

作用：worker 级续跑恢复。

做法是把一个 worker 当前未收口的状态固化下来：

- 当前 workspace 快照
- 当前 session 快照
- pending results
- 当前 session 中已处理的 intent 数

这样即使中断，也能恢复到“最近一个一致状态”。

## 6.8 `src/runtime_recovery.py`

作用：处理 OpenClaw 运行时配置污染与 gateway 崩溃等问题。

功能包括：

- 检测配置文件是否损坏
- 计算配置漂移比例
- 从 baseline 回滚配置
- 调用 `openclaw doctor --fix`
- 重启 gateway

这是一个非常工程化的能力：它不只是记录失败，而是尝试**自动自愈**。

## 6.9 `src/runtime_tools_proxy.py`

作用：在初始化阶段捕获真实 runtime `tools`。

特点：

- 实现一个 OpenAI-compatible proxy
- 可以在 `capture_only` 模式下捕获后立即返回最小响应
- 不依赖上游完整请求结束
- 会保存 `tool_names`、`tool_count`、`message_count` 与完整 `tools`

这是项目里一个很典型的“为准确性牺牲一些复杂度，但最终控制住复杂度”的设计。

## 6.10 `src/fs_utils.py`

作用：处理 reset / cleanup 时的只读文件问题。

能力包括：

- 递归修复 owner writable 权限
- 安全删除只读文件
- 安全删除只读目录树
- 复制快照后恢复可写位

这是一个非常典型的“实际运行中才会暴露”的工程问题修复。

---

## 7. 项目的关键技术难点

这个项目真正有价值的地方，恰恰在于它解决了很多“看起来不大，实际非常麻烦”的工程问题。

### 7.1 intent 与 direct_query 的统一抽象

输入数据未必天然一致：

- 有些是用户意图
- 有些是直接搜索问题
- 有些带 answer，有些不带
- 有些没有稳定 ID

项目通过 `normalize_task_record()` 把它们统一成同一个 task 抽象，这保证了后续流程不需要对输入来源分支爆炸。

### 7.2 双模型职责分离

很多系统只用一个模型完成所有事，但这个项目不是。

这里实际上有两层智能：

1. `LLMClient`：判断下一步 query
2. OpenClaw agent：真正完成工具调用与执行

这种设计的难点在于：

- history 怎么组织
- 完成判断怎么定义
- query 生成和 agent 执行如何衔接
- reasoning 模式如何在不同模型间兼容

### 7.3 多 worker + 单 worker 串行 session 的平衡

如果完全并发地乱跑 session，恢复和归档会非常复杂；
如果完全串行，又会太慢。

本项目采用：

- worker 间并发
- worker 内串行
- 每个 worker 绑定一个 agent

这是一个很好的工程折中。

### 7.4 session 延迟收口

如果一个 session 里连续跑多个 intent，那么中间 intent 的“输出”并不应该立即最终物化。

于是项目引入：

- `pending_session_results`
- worker runtime snapshot
- finalize 时统一归档和转换

这个机制显著提高了轨迹真实性，但实现复杂度也明显上升。

### 7.5 OpenClaw runtime 配置污染恢复

这是项目里很有特色的部分。

真实运行时，OpenClaw 配置可能因为异常、手工修改、环境问题而损坏。项目没有简单地“报错退出”，而是：

- 识别配置损坏信号
- 结合 baseline 和 drift ratio 判断是否触发恢复
- 回滚配置
- 重启 gateway
- 清理 agents
- 自动重跑 generation

这说明项目已经不仅是脚本，而是具备了一定“运行时自治能力”。

### 7.6 工具定义真实来源不一致

工具定义是本项目里一个关键难点。

最开始可能会想当然地认为：

- 静态扫描出来的工具定义 = 实际发给模型的工具定义

但实际并不总是这样。因为运行时还涉及：

- 插件注册
- allowlist 裁剪
- provider 行为
- 最终外发 payload

于是项目最后采用 runtime probe 捕获来逼近“真实工具定义”，这是一种很务实的解决方案。

### 7.7 权限与只读文件问题

在 reset / restart / snapshot 恢复过程中，生成物有时会变成只读，导致删除失败。

这类问题通常不是业务逻辑问题，而是典型的运行环境问题：

- 文件复制保留 mode
- 容器或工具生成只读文件
- 后续 reset 无法删除

项目通过 `fs_utils.py` 做了统一兜底，显著增强了鲁棒性。

---

## 8. 技术亮点

### 8.1 真实轨迹优先，而不是模拟轨迹优先

项目不是伪造一个“看起来像 Agent 轨迹”的数据集，而是尽量基于真实 OpenClaw runtime 来构造数据。

这使得数据在工具调用、消息格式、session 状态上更可信。

### 8.2 对 OpenClaw 采取“编排”而不是“侵入式改造”

项目没有大幅修改 OpenClaw 本体，而是通过：

- CLI
- 配置文件
- workspace
- runtime proxy
- session 文件

完成对其的编排。这种做法可维护性更强，也更适合随着上游版本演进而跟进。

### 8.3 运行时恢复能力很强

相比许多“失败即退出”的脚本，本项目具备：

- 进度文件 resume
- worker snapshot
- runtime recovery
- gateway 重启
- 自动重试

这让它更接近一个生产级数据生成系统。

### 8.4 runtime metadata 设计兼顾准确性与调试性

项目同时保留：

- runtime probe 捕获的真实运行时 metadata（`tools` + `system_prompt`）
- 静态导出的离线工具扫描能力
- 人工维护的 `all_tools.json`

这其实是很好的“三层运行时认知体系”：

1. 离线静态理解
2. 运行时真实捕获
3. 人工可控的标准 catalog

### 8.5 数据转换设计贴近训练使用场景

middle format 不是简单把 session 复制一份，而是做了面向训练的结构化整理，包括：

- system message 注入
- reasoning 抽取
- tools 结构化
- skills 注入
- final_output 汇总

这让输出能直接进入后续训练或数据分析流程。

---

## 9. 输出物说明

项目运行后主要会生成以下输出：

### 9.1 原始 session

目录：`output/sessions`

特点：

- 保留 OpenClaw 原始 JSONL 轨迹
- 可用于问题排查、行为审计、回放分析

### 9.2 middle format

目录：`output/middle_format`

特点：

- 结构更统一
- 更适合训练与后处理
- 每个 intent 对应一个最终 JSON

### 9.3 runtime metadata 与 probe 快照

目录：`output/worker_snapshots/runtime_metadata`

包括：

- `runtime_metadata.json`
- `probe/runtime_probe_*_latest.json`
- `probe/runtime_probe_*.jsonl` 历史

### 9.4 进度与汇总

包括：

- `output/progress.json`
- `output/summary.json`

用于记录：

- 各 intent 最终状态
- success / failed 数量
- 全局自动恢复次数
- 尝试历史

### 9.5 runtime recovery 基线

目录：`output/runtime_recovery`

包括：

- `openclaw.json.baseline`

用于运行时恢复和配置回滚。

---

## 10. 当前适用场景

这个项目尤其适合以下场景：

1. **Agent 训练数据集构建**
2. **工具调用行为分析**
3. **OpenClaw 运行轨迹归档**
4. **多任务 session 采样**
5. **搜索 / 检索 / 自动化代理任务的数据生成**
6. **中间格式标准化输出**

---

## 11. 当前局限与后续可演进方向

### 11.1 当前局限

1. runtime probe 当前保存的是 `message_count`，并未完整落盘 `messages/system prompt`
2. `run_generation.py` 仍较长，主流程可以继续拆分模块
3. tools cache 当前主要按 agent 维度组织，长期可能需要更明确的版本化策略
4. 复杂运行时下，OpenClaw 外部行为仍可能随版本演进发生变化
5. 自动恢复虽然很强，但依然属于“启发式自愈”，并非绝对可靠

### 11.2 可继续演进的方向

1. 保存 probe 请求中的完整 messages 或单独保存 system prompt
2. 将 worker_loop 进一步拆分为更细的 state machine
3. 引入更完整的运行指标与 tracing
4. 对 tools cache 做版本戳、哈希或 schema diff 管理
5. 增加更多回归测试，覆盖 session finalize / runtime recovery / resume 链路
6. 优化文档与运维面板，降低上手成本

---

## 12. 总结

`openclaw_gen_data` 的本质，不是一个简单的“调 OpenClaw 的脚本集合”，而是一个围绕 Agent trajectory 数据构建的完整工程系统。

它的价值体现在三个层面：

### 12.1 工程价值

它把原本脆弱、手工、难恢复的 Agent 数据生成过程，变成了可重复、可并发、可恢复的流水线。

### 12.2 数据价值

它生成的不是纯模拟文本，而是尽量保真的 OpenClaw 真实轨迹，并能转换成结构清晰的训练中间格式。

### 12.3 架构价值

它很好地平衡了：

- 真实运行时保真
- 工具定义准确性
- OpenClaw 兼容性
- 工程鲁棒性
- 数据转换可用性

如果把这个项目放在更大的 Agent 数据基础设施视角下看，它已经具备了一个“小型生产级数据生成平台”的雏形：

- 有输入标准化
- 有执行调度
- 有运行时恢复
- 有中间产物
- 有最终输出
- 有工具系统对账
- 有容器化与 CI 支持

这也是这个项目最值得关注的地方：它解决的不是单点功能，而是一整条 Agent 轨迹生产链路。
