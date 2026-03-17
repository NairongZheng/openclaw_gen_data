# OpenClaw 数据生成项目开发计划

## 项目概述

**项目名称**: openclaw_gen_data
**版本**: v5.0.0（简化重构版本）
**目标**: 通过 OpenClaw CLI 工具生成高质量的 trajectory 数据，用于 LLM SFT 训练

## ✅ v5.0 重构完成状态

**完成时间**: 2026-03-17
**重构目标**: 简化架构，使用 OpenClaw CLI 而非 HTTP API，统一 query 生成逻辑

### 核心变更

1. ✅ **OpenClaw CLI 封装** - 使用命令行工具而非 HTTP API
2. ✅ **统一循环逻辑** - 不区分 first query，LLM 根据状态统一判断
3. ✅ **扁平化结构** - 简化 src/ 目录，移除复杂嵌套
4. ✅ **多 Agent 并发** - 支持 agent-1 到 agent-30 并发执行
5. ✅ **一体化脚本** - 生成和转换在同一个脚本中完成

## ✅ v5.1 Bug 修复（2026-03-17）

### 修复内容

1. ✅ **Agent 命名优化** - 改用 `gendata-worker-{N}` 避免与用户 agent 冲突
2. ✅ **自动初始化 Agent** - 运行时自动检查和创建，无需手动初始化
3. ✅ **Session 复用机制** - 每次处理前清空 session，复用 agent
4. ✅ **修复 SessionParser API** - 使用正确的 `parse_jsonl_file()` 方法
5. ✅ **重构数据提取逻辑** - 基于新的 SessionParser API 重写 converter

## ✅ v6.0 Session 机制优化（2026-03-17）

### 优化内容

1. ✅ **移除冗余 Agent 检测** - 删除 `openclaw_wrapper.py` 中的 `_ensure_agent_exists()`
2. ✅ **修复 Session ID 机制** - 从 openclaw 响应中获取真实的 session ID
3. ✅ **简化 API 接口** - `send_message()` 不再需要传入 session_id 参数
4. ✅ **自动 Session 管理** - wrapper 内部自动跟踪和管理 session ID

### 技术细节

**测试发现**：
- openclaw CLI 忽略自定义 `--session-id` 参数，使用自己生成的 UUID
- Session ID 从响应的 `result.meta.agentMeta.sessionId` 中获取
- 删除 session 文件后，使用相同 ID 会创建全新 session

**修改文件**：
- `src/openclaw_wrapper.py` - 重构 session 管理逻辑
- `scripts/run_generation.py` - 更新 API 调用方式

## ✅ v7.0 Middle Format 完整实现（2026-03-17）

### 优化内容

1. ✅ **修复 clear_session 时机** - 从处理前移到处理后，确保 session_id 存在时才清空
2. ✅ **实现 OpenAI 完整格式** - messages 包含 tool_calls、tool results、reasoning_content
3. ✅ **添加 tools 字段提取** - 从 session 中提取使用的工具定义
4. ✅ **更新 middle_format 结构** - 符合示例规范，包含所有必需字段

### 关键改进

**clear_session 时机修复**：
- 旧逻辑：处理 intent 前调用（此时 session_id 为 None，无效）
- 新逻辑：转换完成后调用（此时已有真实 session_id）

**OpenAI 格式消息**：
- user 消息：`{role, content}`
- assistant 消息：`{role, content, tool_calls?, reasoning_content?}`
- tool 消息：`{role, name, tool_call_id, content}`

**tools 字段**：
- 从实际工具调用中提取工具名称
- 从参数推断工具 schema
- 格式：`{type: "function", function: {name, description, parameters}}`

**middle_format 结构**：
```json
{
  "status": "completed",
  "session_id": "uuid",
  "intent": "用户意图",
  "total_steps": 工具调用总数,
  "enable_thinking": true,
  "messages": [...],
  "tools": [...],
  "final_output": "最终输出",
  "intent_id": "id",
  "metadata": {}
}
```

### 修改文件

- `scripts/run_generation.py` - 调整 clear_session 调用位置
- `src/converter.py` - 完全重写，实现 OpenAI 格式和 tools 提取

## v5.0 简化架构

### 系统交互流程
```
Intent (JSONL) → LLM (统一生成) → OpenClaw CLI → Session File → Middle Format (JSON)
                      ↓                    ↓              ↓              ↓
                 判断完成/生成query    工具调用记录    解析转换      格式验证
```

### 核心组件（简化版）

1. **OpenClawWrapper** (`src/openclaw_wrapper.py`)
   - 封装 OpenClaw CLI 命令调用
   - 管理 session 文件路径
   - 处理命令执行和错误

2. **LLMClient** (`src/llm_client.py`)
   - 统一的 query 生成逻辑（不区分首次/后续）
   - 根据对话历史判断任务是否完成
   - 生成符合 persona 的自然 query

3. **DataConverter** (`src/converter.py`)
   - 解析 OpenClaw session 文件
   - 转换为 middle format
   - 提取对话、轨迹、最终状态

4. **IntentLoader** (`src/intent_loader.py`)
   - 加载 JSONL 格式的 intent 数据

5. **配置管理** (`src/config.py`)
   - 加载 YAML 配置
   - 环境变量替换

## 目录结构（v5.0 简化版）

```
openclaw_gen_data/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml            # ✅ 配置文件
├── data_examples/
│   ├── intents.jsonl          # Intent 示例
│   └── middle_format_data.json
├── src/
│   ├── __init__.py
│   ├── config.py              # ✅ 配置加载
│   ├── openclaw_wrapper.py    # ✅ OpenClaw CLI 封装
│   ├── llm_client.py          # ✅ LLM 客户端
│   ├── intent_loader.py       # ✅ Intent 加载
│   ├── session_parser.py      # ✅ Session 解析
│   ├── converter.py           # ✅ 格式转换
│   └── utils.py               # ✅ 工具函数
├── scripts/
│   ├── init_agents.py         # ✅ Agent 初始化
│   └── run_generation.py      # ✅ 主生成脚本
├── output/
│   ├── sessions/              # Session 文件
│   ├── middle_format/         # 转换后数据
│   ├── logs/                  # 日志
│   └── summary.json           # 执行摘要
└── docs/
    ├── plan.md                # 本文件
    └── raw_design.txt         # 原始设计
```

## 使用方法

### 1. 环境配置

```bash
# 设置环境变量
export LLM_API_URL="https://your-llm-api.com/v1"
export LLM_API_KEY="your-api-key"

# 安装依赖
pip install -r requirements.txt
```

### 2. 初始化 Agents（可选）

```bash
# 创建 30 个并发 agent
python scripts/init_agents.py --num-agents 30
```

### 3. 运行数据生成

```bash
# 测试单个 intent
python scripts/run_generation.py --limit 1

# 测试 10 个 intent，5 个并发
python scripts/run_generation.py --limit 10 --concurrent 5

# 全量生成，30 个并发
python scripts/run_generation.py --concurrent 30
```

### 4. 查看结果

```bash
# 查看生成的数据
ls output/middle_format/

# 查看执行摘要
cat output/summary.json

# 查看日志
tail -f output/logs/generation.log
```
│
├── scripts/                       # 主运行脚本
│   ├── run_generation.py         # Part 1: Intent驱动的生成
│   ├── run_conversion.py         # Part 2: Trajectory转换
│   └── resume_generation.py      # 断点续传脚本
│
├── src/openclaw_gen/              # 核心模块包（新架构）
│   ├── __init__.py
│   ├── config.py                 # 配置管理
│   ├── logging_config.py         # 日志配置
│   │
│   ├── connectors/               # 连接器模块
│   │   ├── __init__.py
│   │   ├── openclaw_client.py   # OpenClaw HTTP API客户端
│   │   └── llm_client.py        # LLM API客户端（User Loop）
│   │
│   ├── core/                     # 核心逻辑
│   │   ├── __init__.py
│   │   ├── intent_loader.py     # Intent数据加载器
│   │   ├── user_loop.py         # User Loop（LLM驱动的query拆解）
│   │   ├── trajectory_manager.py # Trajectory生成管理器
│   │   └── progress_tracker.py  # 进度跟踪和断点续传
│   │
│   ├── processors/               # 数据处理
│   │   ├── __init__.py
│   │   ├── trajectory_parser.py # Trajectory解析器
│   │   ├── middle_format_converter.py # 中间格式转换器
│   │   └── tools_extractor.py   # 工具定义提取器
│   │
│   └── utils/                    # 工具函数
│       ├── __init__.py
│       ├── file_utils.py        # 文件操作
│       ├── concurrency.py       # 并发工具
│       └── validators.py        # 数据验证
│
├── tests/                        # 测试目录
│   ├── __init__.py
│   ├── test_openclaw_client.py
│   ├── test_user_loop.py
│   ├── test_trajectory_manager.py
│   ├── test_converter.py
│   └── fixtures/                # 测试数据
│
└── output/                       # 输出目录（.gitignore）
    ├── trajectories/            # 生成的trajectory
    ├── middle_format/           # 转换后的中间格式
    ├── progress/                # 进度文件
    ├── logs/                    # 日志
    └── tools/                   # 工具schema缓存
```

## 数据流程

### Part 1: Trajectory 生成流程

```
1. 读取 intents.jsonl
   ├─ natural_language_intent (用户意图)
   ├─ persona (角色信息)
   ├─ domains (领域信息)
   └─ tasks (任务列表)
        ↓
2. 进度跟踪器检查
   ├─ 已完成的intent → 跳过
   └─ 未完成/失败的intent → 继续
        ↓
3. 为每个 intent 创建 session
   └─ 并发处理（可配置并发数）
        ↓
4. User Loop（LLM驱动）
   ├─ 分析 intent 和当前对话历史
   ├─ 生成下一个 query（拆解任务）
   └─ 判断是否完成
        ↓
5. 发送到 OpenClaw HTTP API
   └─ POST /v1/chat/completions
        ↓
6. 接收 OpenClaw 响应
   ├─ assistant 消息
   ├─ tool_calls（如果有）
   └─ tool results
        ↓
7. 保存 trajectory
   ├─ 完整对话历史（OpenAI格式）
   ├─ 工具调用记录
   └─ 元数据（intent_id, persona等）
        ↓
8. 更新进度文件
   └─ 标记完成状态
```

### Part 2: 格式转换流程

```
1. 扫描 trajectories/ 目录
        ↓
2. 解析每个 trajectory 文件
   ├─ 提取 messages
   ├─ 提取 tool_calls 和 tool results
   └─ 识别使用的工具
        ↓
3. 提取工具定义
   ├─ 从 OpenClaw API 获取完整工具列表
   └─ 匹配使用的工具
        ↓
4. 转换为 middle_format
   ├─ status: completed/failed/in_progress
   ├─ session_id
   ├─ intent（从原始数据）
   ├─ total_steps
   ├─ messages（OpenAI格式）
   ├─ tools（完整定义）
   ├─ enable_thinking
   └─ final_output
        ↓
5. 验证输出格式
        ↓
6. 保存 middle_format JSON
```

## 实施阶段

### 阶段1：基础设施 ✅

**任务**：
- [x] 创建新的目录结构
- [x] 配置管理系统（config.py + config.yaml.example）
- [x] 日志系统（logging_config.py）
- [x] 数据模型定义（使用 Pydantic）
- [x] 基础工具函数（file_utils.py, validators.py）
- [x] 更新 requirements.txt, setup.py
- [x] 更新 .gitignore

**关键文件**：
- `src/openclaw_gen/config.py`
- `src/openclaw_gen/logging_config.py`
- `src/openclaw_gen/utils/file_utils.py`
- `src/openclaw_gen/utils/validators.py`
- `config/config.yaml.example`
- `requirements.txt`
- `setup.py`

### 阶段2：连接器模块 ✅

**任务**：
- [x] 实现 OpenClawClient
  - HTTP API 通信
  - Session 管理
  - 错误处理和重试
- [x] 实现 LLMClient
  - OpenAI SDK 兼容调用
  - Prompt 设计和测试
- [ ] 编写单元测试（使用 mock）

**关键文件**：
- `src/openclaw_gen/connectors/openclaw_client.py` ✅
- `src/openclaw_gen/connectors/llm_client.py` ✅
- `tests/test_openclaw_client.py`
- `tests/test_llm_client.py`

### 阶段3：核心逻辑 ✅

**任务**：
- [x] 实现 IntentLoader（读取 intents.jsonl）
- [x] 实现 ProgressTracker（进度跟踪和断点续传）
- [x] 实现 UserLoop（循环交互逻辑）
- [x] 实现 TrajectoryManager（保存trajectory）
- [ ] 编写单元测试和集成测试

**关键文件**：
- `src/openclaw_gen/core/intent_loader.py` ✅
- `src/openclaw_gen/core/progress_tracker.py` ✅
- `src/openclaw_gen/core/user_loop.py` ✅
- `src/openclaw_gen/core/trajectory_manager.py` ✅

### 阶段4：数据处理 ✅

**任务**：
- [x] 实现 ToolsExtractor（提取工具定义）
- [x] 实现 TrajectoryParser（解析trajectory）
- [x] 实现 MiddleFormatConverter（转换为中间格式）
- [ ] 数据验证器（验证middle_format格式）
- [ ] 编写测试

**关键文件**：
- `src/openclaw_gen/processors/tools_extractor.py` ✅
- `src/openclaw_gen/processors/trajectory_parser.py` ✅
- `src/openclaw_gen/processors/middle_format_converter.py` ✅

### 阶段5：主脚本和并发 ✅

**任务**：
- [x] 实现 run_generation.py（主生成脚本）
  - 批量处理 intents
  - 并发控制
  - 进度显示
  - 错误处理
- [x] 实现 run_conversion.py（转换脚本）
- [x] 实现 resume_generation.py（断点续传脚本）
- [ ] 并发工具（concurrency.py）

**关键文件**：
- `scripts/run_generation.py` ✅
- `scripts/run_conversion.py` ✅
- `scripts/resume_generation.py` ✅

### 阶段6：文档和测试 ⏸️

**任务**：
- [ ] 编写 README.md
- [ ] 编写 docs/installation.md
- [ ] 编写 docs/usage.md
- [ ] 编写 docs/api.md
- [ ] 完善所有代码注释（符合CLAUDE.md要求）
- [ ] 端到端测试

### 阶段7：优化和发布 ⏸️

**任务**：
- [ ] 性能优化
- [ ] 错误处理优化
- [ ] 代码审查和重构
- [ ] 最终测试
- [ ] 版本标记（v4.0.0）

## 重构决策

### 为什么要重构？

旧实现存在的问题：

1. **硬编码路径**：代码中有多处硬编码的个人路径（`/Users/luosiyuan/...`），可移植性差
2. **配置管理混乱**：使用 `config_example.py` 而不是标准的 YAML 配置
3. **缺少文档**：没有 README.md、requirements.txt、安装指南等
4. **代码注释不完善**：缺少内联注释，不符合团队规范
5. **缺少测试**：没有单元测试和集成测试
6. **日志系统不规范**：使用 `print()` 而不是 `logging` 模块
7. **模块结构不清晰**：文件直接放在 `src/` 下，没有按功能分包

### 重构的核心改进

1. **配置驱动**：使用 YAML 配置文件，支持环境变量
2. **模块化设计**：按功能分包（connectors, core, processors, utils）
3. **完善文档**：README、安装指南、使用指南、API文档
4. **详细注释**：每个函数都有 docstring，关键逻辑有内联注释
5. **测试覆盖**：单元测试、集成测试、端到端测试
6. **标准日志**：使用 `logging` 模块，支持日志级别和格式配置
7. **可打包安装**：提供 `setup.py`，可通过 `pip install -e .` 安装

## 技术选型

### 核心依赖

- **requests**: HTTP 通信（OpenClaw API、LLM API）
- **openai**: OpenAI SDK（兼容格式）
- **pydantic**: 数据验证和模型定义
- **pyyaml**: YAML 配置文件解析
- **tqdm**: 进度显示
- **concurrent.futures**: 并发处理（Python 标准库）

### 开发工具

- **pytest**: 测试框架
- **black**: 代码格式化
- **isort**: import 排序
- **mypy**: 类型检查（可选）

## 关键设计决策

### 1. OpenClaw API 交互方式

**决策**：使用 HTTP API (127.0.0.1:60012)，兼容 OpenAI Chat API 格式

**理由**：
- OpenClaw 提供了与 OpenAI 兼容的 HTTP API
- 比直接操作 CLI 更稳定、更易测试
- 支持更灵活的 Session 管理

### 2. User Loop 实现方式

**决策**：使用 LLM 驱动的循环，而不是规则驱动

**理由**：
- Intent 可能非常复杂，难以用规则拆解
- LLM 可以理解上下文，判断任务是否完成
- 更灵活，易于处理各种场景

### 3. 进度跟踪机制

**决策**：使用 JSON 文件记录进度，而不是数据库

**理由**：
- 数据量不大（几百到几千个 intent）
- JSON 文件更简单、更易调试
- 无需额外的数据库依赖

### 4. 并发实现方式

**决策**：使用 `ThreadPoolExecutor`，而不是 `ProcessPoolExecutor` 或 `asyncio`

**理由**：
- 主要瓶颈是网络 I/O，而不是 CPU
- 线程池更简单，资源占用更少
- 无需处理复杂的进程间通信

### 5. 配置管理方式

**决策**：使用 YAML 配置文件 + 环境变量

**理由**：
- YAML 更易读，支持注释
- 敏感信息（API Key）通过环境变量管理
- 符合业界标准实践

## 风险和注意事项

### 风险

1. **OpenClaw API 可能不稳定**
   - 缓解：充分的错误处理和重试机制
   - 进度文件确保数据不丢失

2. **LLM 生成的 query 可能不合理**
   - 缓解：精心设计 Prompt
   - 添加验证逻辑（如query长度、格式检查）
   - 记录所有生成的query，便于调试

3. **并发可能导致 API 限流**
   - 缓解：可配置并发数
   - 添加速率限制（rate limiting）

4. **Trajectory 文件可能很大**
   - 缓解：定期清理或归档旧文件
   - 监控磁盘空间

### 注意事项

1. **环境变量管理**：
   - 敏感信息（API Key）通过环境变量
   - 提供 .env.example 模板

2. **日志管理**：
   - 日志轮转（避免单文件过大）
   - 敏感信息脱敏

3. **数据备份**：
   - 定期备份 progress.json
   - 重要的 trajectory 文件备份

4. **监控和告警**：
   - 记录成功率、失败率
   - 异常情况及时通知

## 代码规范

遵循 CLAUDE.md 的要求：

1. **函数注释**：每个函数都必须有详细的 docstring
2. **内联注释**：关键逻辑必须有解释性注释
3. **模块化**：单个文件不超过 500 行（目标 300-400 行）
4. **文件管理**：
   - 文档统一放在 `docs/` 目录
   - 配置放在 `config/` 目录
   - 输出放在 `output/` 目录（.gitignore）
5. **不创建冗余文档**：基于现有文档调整，而不是每次新建
6. **Python 环境**：使用 conda dev 环境运行

## 开发环境

### Python 版本

- Python 3.8+（推荐 3.9 或 3.10）

### 环境配置

```bash
# 激活 conda dev 环境
conda activate dev

# 安装依赖
pip install -r requirements.txt

# 以开发模式安装项目
pip install -e .
```

### 外部依赖

1. **OpenClaw CLI**：需要预先安装并配置
2. **OpenClaw HTTP API**：需要运行在 127.0.0.1:60012
3. **LLM API**：Azure OpenAI 或其他兼容 OpenAI SDK 的服务

## 测试策略

### 单元测试

- 每个模块都有对应的测试文件
- 使用 mock 模拟外部依赖（API 调用）
- 覆盖率目标：80%+

### 集成测试

- 测试模块间的交互
- 使用真实的配置文件和数据
- 验证数据流完整性

### 端到端测试

- 使用少量真实 intent 数据
- 完整运行 Part 1 和 Part 2
- 验证最终输出格式

## 迁移策略

1. **在新分支进行**：
   ```bash
   git checkout -b refactor-v4
   ```

2. **保留旧代码**（如需参考）：
   ```bash
   mkdir -p archive/v3
   git mv scripts/main_*.py archive/v3/
   git mv src/*.py archive/v3/
   ```

3. **逐步实施**：
   - 按照阶段1-7依次实施
   - 每个阶段完成后进行测试
   - 通过测试后再进入下一阶段

4. **验证**：
   - 单元测试全部通过
   - 集成测试通过
   - 端到端测试通过

5. **合并到主分支**：
   ```bash
   git checkout main
   git merge refactor-v4
   ```

## 版本历史

- **v3.0.0** (Initial commit f1a16f9): 初始实现，存在硬编码路径等问题
- **v4.0.0** (重构版本): 完全重构，模块化设计，完善文档和测试

## 参考资料

- [OpenClaw 官方文档](https://docs.openclaw.ai/)
- [OpenAI API 文档](https://platform.openai.com/docs/api-reference)
- [Pydantic 文档](https://docs.pydantic.dev/)
- [Python logging 最佳实践](https://docs.python.org/3/howto/logging.html)

---

**最后更新**: 2026-03-17
**作者**: Claude Code
**版本**: v4.0.0 (重构版)
