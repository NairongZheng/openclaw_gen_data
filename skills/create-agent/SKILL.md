---
name: create-agent
description: "创建新的 OpenClaw agent 会话。自动复制目录结构、配置文件，并注册到 openclaw.json。"
metadata: { "openclaw": { "emoji": "🤖" } }
---

# Create Agent Skill

创建新的 OpenClaw agent 会话的自动化流程。

## 使用场景

当用户需要创建一个新的 agent 会话时，自动完成以下操作：
1. 复制现有 agent 目录结构
2. 复制必要的配置文件
3. 在 openclaw.json 中注册新 agent

## 操作步骤

### 1. 创建 agent 目录

```bash
cp -r ~/.openclaw/agents/main ~/.openclaw/agents/<new-agent-id>
```

### 2. 复制配置文件

```bash
cp ~/.openclaw/agents/main/agent/models.json ~/.openclaw/agents/<new-agent-id>/agent/models.json
cp ~/.openclaw/agents/main/agent/auth-profiles.json ~/.openclaw/agents/<new-agent-id>/agent/auth-profiles.json
```

### 3. 注册到配置文件

编辑 `~/.openclaw/openclaw.json`，在 `agents.list` 数组中添加：

```json
{
  "id": "<new-agent-id>",
  "name": "<Agent Display Name>",
  "workspace": "/mnt/afs_toolcall/jarvis/.openclaw/workspace",
  "agentDir": "/mnt/afs_toolcall/jarvis/.openclaw/agents/<new-agent-id>/agent"
}
```

### 4. 验证

```bash
openclaw agents list
```

## 注意事项

- agent-id 使用小写字母和连字符
- 确保 agent 目录权限正确
- 配置文件必须是有效的 JSON 格式
