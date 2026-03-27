# tool-inspector

从 OpenClaw 运行时动态提取所有工具的完整定义（name + description + parameters JSON Schema），输出为 JSON 文件。

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
