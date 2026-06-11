# 架构决策记录

本文记录系统设计阶段的关键技术决策及其背后的取舍考量，与 [`project-architecture-and-introduction.md`](project-architecture-and-introduction.md) 互补——架构文档描述"系统是什么"，本文解释"为什么这样设计"。

---

## 1. 使用 OpenClaw CLI 而非 HTTP API

**决策**：所有与 OpenClaw 的交互通过 CLI 子进程（`openclaw agent --message ...`），而非 HTTP API。

**理由**：
- CLI 是 OpenClaw 对外暴露的官方接口，行为与用户使用的一致
- HTTP API 是 Gateway 的内部接口，路径和格式容易随版本变化
- 通过 CLI 驱动意味着工具列表、系统提示都是真实运行时生成的，而非静态定义

---

## 2. 双模型职责分离

**决策**：OpenClaw 底层执行模型与外部 LLMClient（用户模拟器）完全独立，互不干扰。

**理由**：
- 执行模型决定"怎么做任务"（工具调用策略、推理深度），可以是任何支持工具调用的模型
- 用户模拟器决定"用户说什么"（query 拆解、完成判断），对任务理解要求高、对工具调用无要求
- 分离后两套模型可以独立替换（比如执行用 Claude、模拟用 Qwen），不互相约束

---

## 3. Runtime Probe 而非静态工具扫描

**决策**：通过短生命周期 probe 捕获 OpenClaw 真实外发给模型的 `tools` + `system_prompt`，而非静态扫描源码或配置文件。

**理由**：
- OpenClaw 在运行时会根据已安装插件、已启用 skills、agent 配置动态组装工具列表
- 静态扫描只能拿到"可能存在的工具"，而非"这个 agent 实际拿到的工具定义"
- Runtime probe 捕获的是模型视角的真实上下文，训练数据的 `tools` 字段因此可信

---

## 4. Deferred Session Finalization（延迟收口）

**决策**：session 的归档和格式转换在 User Loop 完整跑完之后才执行，而非逐轮写入。

**理由**：
- OpenClaw session 文件在 agent 持续交互时会持续追加内容；过早读取会得到不完整的 session
- 延迟到最终状态后一次性归档，保证 session 文件完整、可重放
- 失败的 session 有完整的 pending 记录，重启后可从断点恢复而非重头重跑

---

## 5. 线程池并发而非多进程

**决策**：并发使用 `ThreadPoolExecutor`，而非 `ProcessPoolExecutor` 或 `asyncio`。

**理由**：
- Worker 的主要耗时在等待 OpenClaw CLI 子进程、LLM API 响应——全是 I/O 阻塞，GIL 不是瓶颈
- 线程间可直接共享 `shared_llm`、`shared_converter`、`shared_tools` 等对象，无需 IPC
- 多进程需要序列化共享对象、建立进程间通道，增加复杂度但无 CPU 计算收益

---

## 6. 进度文件用 JSON 而非数据库

**决策**：任务进度记录在 `output/progress.json`，而非 SQLite 或其他数据库。

**理由**：
- 数据量有限（千级 intent），JSON 足够高效
- 文件可直接用文本工具检查和手动修复，调试成本低
- 无额外依赖；多 worker 写入通过 `threading.Lock()` 保护，足以保证一致性

---

## 7. 配置优先级：ENV > CLI > config.yaml

**决策**：所有配置项支持三层覆盖，环境变量优先级最高。

**理由**：
- 容器部署时通过 `-e` 注入运行参数是标准实践，不需要挂载修改后的配置文件
- CLI 参数方便本地调试时快速覆盖单个值
- config.yaml 保存稳定的基线配置，版本控制友好
