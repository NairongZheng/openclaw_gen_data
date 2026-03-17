# OpenClaw 数据生成项目

> 通过与 OpenClaw HTTP API 交互生成高质量的 trajectory 数据，用于 LLM SFT 训练

**版本**: v4.0.0（重构版本）  
**状态**: 🚧 开发中（阶段1已完成：基础设施）

---

## 项目概述

本项目用于自动化生成 OpenClaw trajectory 数据，核心流程包括：

1. **Intent 驱动**: 从 `intents.jsonl` 读取用户意图
2. **User Loop**: 使用 LLM 将 intent 拆解成多轮 query，与 OpenClaw 循环交互
3. **并发与断点续传**: 支持多 session 并发，通过进度文件实现断点续传
4. **后处理转换**: 将 OpenClaw trajectory 转换为标准的 middle_format.json

## 核心特性

- ✅ **配置驱动**: 使用 YAML 配置文件，支持环境变量
- ✅ **模块化设计**: 按功能分包（connectors, core, processors, utils）
- ✅ **完善的日志**: 支持文件和控制台输出，敏感信息自动脱敏
- ✅ **数据验证**: 使用 Pydantic 进行严格的类型检查和数据验证
- 🚧 **并发处理**: 多 session 并发生成（开发中）
- 🚧 **断点续传**: 进度跟踪，支持网络/服务中断恢复（开发中）

## 项目结构

```
openclaw_gen_data/
├── README.md                    # 项目文档
├── requirements.txt             # Python依赖
├── setup.py                     # 项目配置
│
├── docs/                        # 文档目录
│   ├── raw_design.txt          # 原始设计思路
│   ├── plan.md                 # 开发计划和架构设计
│   ├── installation.md         # 安装指南（待创建）
│   └── usage.md                # 使用指南（待创建）
│
├── data_examples/               # 示例数据
│   ├── intents.jsonl           # Intent示例
│   └── middle_format_data.json # 中间格式示例
│
├── config/                      # 配置目录
│   └── config.yaml.example     # 配置模板
│
├── scripts/                     # 主运行脚本（待创建）
│   ├── run_generation.py       # Part 1: Intent驱动的生成
│   ├── run_conversion.py       # Part 2: Trajectory转换
│   └── resume_generation.py    # 断点续传脚本
│
├── src/openclaw_gen/            # 核心模块包
│   ├── __init__.py
│   ├── config.py               # 配置管理 ✅
│   ├── logging_config.py       # 日志配置 ✅
│   ├── models.py               # 数据模型定义 ✅
│   │
│   ├── connectors/             # 连接器模块（待实现）
│   │   ├── openclaw_client.py  # OpenClaw HTTP API客户端
│   │   └── llm_client.py       # LLM API客户端
│   │
│   ├── core/                   # 核心逻辑（待实现）
│   │   ├── intent_loader.py    # Intent数据加载器
│   │   ├── user_loop.py        # User Loop
│   │   ├── trajectory_manager.py # Trajectory管理器
│   │   └── progress_tracker.py # 进度跟踪
│   │
│   ├── processors/             # 数据处理（待实现）
│   │   ├── trajectory_parser.py # Trajectory解析器
│   │   ├── middle_format_converter.py # 格式转换器
│   │   └── tools_extractor.py  # 工具提取器
│   │
│   └── utils/                  # 工具函数
│       ├── file_utils.py       # 文件操作 ✅
│       ├── validators.py       # 数据验证 ✅
│       └── concurrency.py      # 并发工具（待实现）
│
├── tests/                      # 测试目录（待创建）
│
└── output/                     # 输出目录（gitignore）
    ├── trajectories/           # 生成的trajectory
    ├── middle_format/          # 转换后的中间格式
    ├── progress/               # 进度文件
    ├── logs/                   # 日志
    └── tools/                  # 工具schema缓存
```

## 快速开始

### 环境要求

- Python 3.8+（推荐使用 conda dev 环境）
- OpenClaw CLI（已安装并配置）
- OpenClaw HTTP API 运行在 127.0.0.1:60012
- LLM API（Azure OpenAI 或兼容 OpenAI SDK 的服务）

### 安装

1. **克隆项目**

```bash
git clone <your-repo-url>
cd openclaw_gen_data
```

2. **激活 conda 环境**

```bash
conda activate dev
```

3. **安装依赖**

```bash
pip install -r requirements.txt
```

4. **以开发模式安装项目**

```bash
pip install -e .
```

### 配置

1. **复制配置模板**

```bash
cp config/config.yaml.example config/config.yaml
```

2. **编辑配置文件**

打开 `config/config.yaml`，配置以下关键项：

```yaml
openclaw:
  base_url: "http://127.0.0.1:60012"  # OpenClaw API 地址

llm:
  base_url: "${LLM_BASE_URL}"         # LLM API 地址（环境变量）
  api_key: "${LLM_API_KEY}"           # LLM API Key（环境变量）
  model: "gpt-4o-2024-11-20"          # LLM 模型名称

paths:
  intents_file: "data_examples/intents.jsonl"  # Intent 数据文件
```

3. **设置环境变量**

```bash
export LLM_BASE_URL="https://your-llm-api-endpoint.com"
export LLM_API_KEY="your-api-key"
```

或者创建 `.env` 文件：

```bash
LLM_BASE_URL=https://your-llm-api-endpoint.com
LLM_API_KEY=your-api-key
```

### 使用

**注意**: 核心功能正在开发中（阶段2-5），完整的使用方式将在后续版本提供。

目前可以测试已完成的模块：

```python
# 测试配置加载
from openclaw_gen.config import Config

Config.initialize("config/config.yaml")
print(Config.get("openclaw.base_url"))

# 测试日志系统
from openclaw_gen.logging_config import setup_logging, get_logger

setup_logging(Config.get_all())
logger = get_logger(__name__)
logger.info("测试日志系统")

# 测试数据模型
from openclaw_gen.models import Intent, Trajectory

# 测试文件工具
from openclaw_gen.utils.file_utils import load_jsonl

intents = load_jsonl("data_examples/intents.jsonl")
print(f"加载了 {len(intents)} 个 intent")
```

## 开发计划

详细的开发计划请参考 [docs/plan.md](docs/plan.md)。

### 当前进度

- [x] **阶段1：基础设施** ✅
  - [x] 目录结构
  - [x] 配置管理系统
  - [x] 日志系统
  - [x] 数据模型定义
  - [x] 基础工具函数
  - [x] requirements.txt 和 setup.py
  - [x] .gitignore 更新

- [ ] **阶段2：连接器模块** 🚧
  - [ ] OpenClawClient
  - [ ] LLMClient
  - [ ] 单元测试

- [ ] **阶段3：核心逻辑** 🚧
  - [ ] IntentLoader
  - [ ] ProgressTracker
  - [ ] UserLoop
  - [ ] TrajectoryManager

- [ ] **阶段4：数据处理** ⏸️
- [ ] **阶段5：主脚本和并发** ⏸️
- [ ] **阶段6：文档和测试** ⏸️
- [ ] **阶段7：优化和发布** ⏸️

## 技术栈

- **核心依赖**:
  - `pydantic`: 数据验证和模型定义
  - `pyyaml`: YAML 配置文件解析
  - `requests`: HTTP 通信
  - `openai`: OpenAI SDK（兼容格式）
  - `tqdm`: 进度显示

- **开发工具**:
  - `pytest`: 测试框架
  - `black`: 代码格式化
  - `isort`: import 排序
  - `mypy`: 类型检查

## 文档

- [开发计划和架构设计](docs/plan.md)
- [原始设计思路](docs/raw_design.txt)
- 安装指南（待创建）
- 使用指南（待创建）
- API文档（待创建）

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可

MIT License

---

**最后更新**: 2026-03-17  
**版本**: v4.0.0 (阶段1完成)  
**作者**: Claude Code
