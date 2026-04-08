# tool-inspector

从 OpenClaw 运行时动态提取所有工具的完整定义（name + description + parameters JSON Schema），输出为 JSON 文件。

这个目录目前同时包含两套思路：

- **旧方式（保留）**：使用 `dump_tools.mjs` 做静态扫描/运行时插件注册拦截，导出一份工具定义。
- **新推荐方式**：在 `init_agents.py --refresh-tools` 阶段，创建一个短生命周期的 probe agent，并通过一次真实请求捕获 OpenClaw 最终发给模型的 `tools`，再写入工具缓存。

如果你的目标是做 **agent 轨迹采集 / 训练数据回放 / 工具定义对账**，推荐优先使用 **新推荐方式**，因为它更接近真实运行时请求。

## 推荐方式：初始化阶段 probe 捕获真实 tools

当前仓库的主链路已经不再依赖静态扫描结果作为唯一真值，而是在初始化时做一次真实请求校准：

1. `scripts/init_agents.py --refresh-tools`
2. 临时启动一个短生命周期本地 proxy
3. 创建一个 `probe agent`
4. 让该 agent 发起一次最小请求
5. 捕获 OpenClaw **最终外发请求**中的 `tools`
6. 将捕获结果写回工具缓存（如 `output/tools/openclaw_all_tools.json`）
7. 清理 probe agent，并关闭 proxy

这个方案的特点：

- **优点**：拿到的是 OpenClaw 最终发给模型的 `tools`，比静态扫描更接近真实运行时
- **风险控制**：proxy 只在初始化阶段短暂存在，不影响正式生成链路的稳定性
- **适用场景**：工具 schema 经常变化、插件注册与静态提取结果不一致、需要尽量贴近真实请求时

相关实现位于：

- `scripts/init_agents.py`
- `src/runtime_tools_proxy.py`

## 旧方式：`dump_tools.mjs` 静态导出

## 前置条件

- Node.js 18+
- OpenClaw 已安装（自动检测安装路径，支持 npm/Homebrew/nvm 等多种安装方式）

## 用法

```bash
# 导出所有 agent 的工具
node dump_tools.mjs --all-agents --output '/your/path/{agent}/tools.json'

# 只导出指定 agent
node dump_tools.mjs --agent main --output /tmp/main_tools.json

# 查看帮助
node dump_tools.mjs --help
```

换电脑后只需运行上面第一条命令，无需其他配置。

## 输出格式

```json
{
  "metadata": {
    "exportTime": "2026-03-19T10:00:00.000Z",
    "agentId": "main",
    "totalCount": 43,
    "bySource": {
      "builtin": 22,
      "plugin:feishu": 13,
      "plugin:git-tools": 5,
      "plugin:memory-core": 2,
      "plugin:notify": 1
    }
  },
  "tools": [
    {
      "name": "exec",
      "description": "Execute shell commands...",
      "parameters": { "type": "object", "required": ["command"], "properties": { "command": { "type": "string" } } },
      "source": "builtin"
    }
  ]
}
```

## 工作原理

### 工具提取
- **内置工具**：自动扫描 OpenClaw dist bundle，找所有 `name/description/parameters` 组合，递归解析 TypeBox schema 依赖，无硬编码
- **插件工具**：运行时 load 插件，拦截 `registerTool` 调用
- **pi-coding-agent 工具**（read/write/edit）：直接从 npm 包导入
- **唯一 fallback**：`web_search` schema 是运行时动态生成，使用静态构造版本

### OpenClaw 路径自动检测

脚本会按优先级依次尝试以下方法查找 OpenClaw 安装路径（使用第一个找到的）：

1. **`OPENCLAW_ROOT` 环境变量** - 手动指定，优先级最高
2. **`which openclaw`** - PATH 中的可执行文件（**当前 shell 实际执行的版本**，使用系统命令，支持 Windows `where`）
3. **`import.meta.resolve`** - Node.js 模块解析（项目本地依赖）
4. **`npm root -g`** - 全局 npm 包目录
5. **`process.execPath`** - 当前 node 运行时的相邻路径（确保版本匹配）
6. **`NVM_BIN`** - nvm 当前激活版本（如果使用 nvm）

**设计原则**：优先使用 `which openclaw` 确保找到的是你在终端输入 `openclaw` 时实际运行的版本。

**多环境支持**：如果系统中有多个 OpenClaw 安装，可通过环境变量指定：
```bash
export OPENCLAW_ROOT=/path/to/your/openclaw
node dump_tools.mjs --all-agents
```

## 注意

- Skills（tmux、apple-reminders 等）是 Markdown 文档，不注册工具，不会出现在列表里
- OpenClaw 升级后脚本自动适应，无需手动更新
- `dump_tools.mjs` 现在更适合作为**离线检查 / 调试 / 对比工具**，而不是生成链路里的唯一真值来源
- 如果你需要一份人工维护、长期稳定的标准工具定义，建议配合仓库中的 `all_tools.json` 一起使用
