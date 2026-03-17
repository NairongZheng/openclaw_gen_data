
update 1
1. query 不需要分 first query 跟 后面的 query，就按照我一开始的需求来循环就可以，这一部分不需要参考旧代码
2. 我不需要把 generation 分为两个脚本，因为每次跑，没跑完的都是一定需要 resume 的
3. 现在整个项目的文件夹结构好奇怪，为什么会有 src/openclaw_gen 这种结构呢，我不理解，请你重新调整，包括原来的旧文件，该删除的删除
4. openclaw 这个 client 你理解可能还是有点问题。他是一个本地的服务，而且好像是没有http的也没有其他api好像，这边就是你需要研究设计的地方了。给你几个参考
   1. 你可以先试试 openclaw --help 来看看命令

openclaw 相关：
openclaw 每次运行好像会把session的内容保存在 ~/.openclaw/agents/<agent_name>/sessions
我原本的思路是并发30的话，就一开始init的时候新建30个session，然后跑完了之后把路径下的sessions移动到这个项目的output文件夹下
但是openclaw发送命令的openclaw agent --agent <agent-id> --session-id <session-id> --message "<message_content>"好像没办法发送到对应的session
只能发送到main session，所以这边有点苦恼
或者我写一个 create-agent 的skill，你在一开始init的时候就让main session创建30个agent，然后并发的时候就可以独立会话了
你觉得呢？

update 2
有几个问题：
1. 在系统初始化的时候没有先让 openclaw 创建 agent，导致后面在发消息的时候，找不到相应的 agent id
2. 不希望 agent 创建太多，比如并发30，就创建30个就可以了，然后每次收集完一整条轨迹之后就 reset 然后复用即可
3. 解析文件的时候，parsed_data = self.parser.parse_session_file(session_file)，好像没有 parse_session_file 这个函数

update 3
我发现还有几个问题需要跟你确认：
1. generation 一开始的时候就确定了 agent 是否存在，然后在处理 intent 开始的时候初始化 openclaw client 的时候又确认了一遍，这是有必要的吗？
2. openclaw 的 session-id 我不知道是怎么定义的，好像每个 agent 都会有一个main session，然后每个 session-id 都是 hash 值，然后每次 reset 的时候都会生成新的，所以这边要怎么确定我发送的消息是正确的session，怎么保证最后拿到的数据的session是正确的呢？

update 4
接下来是几个优化：
1. 主循环定义了max_turns，这是必须的吗，是不是应该让 usermodel 自己决定是否停止比较好？或者max_turns是一个保险机制，避免进入死循环？
2. session 清空的逻辑好像有问题，因为session-id是交互一次之后拿到的，但是清空应该是在交互之前吧，这边的逻辑感觉很奇怪
3. 好像没有保存 tools 这个字段，需要把所有可用 tools 都保存到最后的 json 文件里面，参考我给你的文件：data_examples/middle_format_data.json
4. 我用调试的方式看了一下返回，内容是：print(response["result"]["payloads"])
[{'text': '你好！我注意到你提到了"Wei"和"Q3信用风险评估"的工作流，但我需要确认一下：\n\n**我是小白，damonzheng 的助手。** 从你的消息看，似乎你可能是在和另一个助手或系统对话？\n\n如果这是发给我的任务，我需要先澄清几点：\n\n1. **你是谁？** 你是 damonzheng 本人，还是其他协作者？\n2. **这个项目背景** - "Q3信用风险评估"是你们正在进行的实际项目吗？\n3. **我的角色** - 你希望我在这个工作流中扮演什么角色？\n\n如果这是一个测试场景或者演示需求，请告诉我具体想要达成什么目标，我会相应调整回应方式。\n\n如果你确实需要我协助设计服务编排方案，我可以帮忙，但需要你先提供那三个问题的答案（服务清单、依赖关系、部署环境）。', 'mediaUrl': None}]

然后session文件里面的内容是

{"type":"message","id":"ae61dbe4","parentId":"e652c6c1","timestamp":"2026-03-17T11:34:32.525Z","message":{"role":"assistant","content":[{"type":"text","text":"你好！我注意到你提到了\"Wei\"和\"Q3信用风险评估\"的工作流，但我需要确认一下：\n\n**我是小白，damonzheng 的助手。** 从你的消息看，似乎你可能是在和另一个助手或系统对话？\n\n如果这是发给我的任务，我需要先澄清几点：\n\n1. **你是谁？** 你是 damonzheng 本人，还是其他协作者？\n2. **这个项目背景** - \"Q3信用风险评估\"是你们正在进行的实际项目吗？\n3. **我的角色** - 你希望我在这个工作流中扮演什么角色？\n\n如果这是一个测试场景或者演示需求，请告诉我具体想要达成什么目标，我会相应调整回应方式。\n\n如果你确实需要我协助设计服务编排方案，我可以帮忙，但需要你先提供那三个问题的答案（服务清单、依赖关系、部署环境）。"}],"api":"anthropic-messages","provider":"codecmd-yuannang","model":"claude-sonnet-4-5-20250929","usage":{"input":25,"output":334,"cacheRead":0,"cacheWrite":18411,"totalTokens":18770,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":1773747263938}}

好像略有不同，这边的处理方式是不是应该修改，特别是：
if response.get("result", {}).get("payloads"):
                for payload in response["result"]["payloads"]:
                    if payload.get("type") == "text":
                        assistant_text += payload.get("text", "")


# TODO

1. 一开始就清空 agent session
2. 清空的时候是否session 的jsonl文件也会被清空呢？或者会新建？
3. 这边的方案是不是应该改成一开始清空session并且拿到session-id，这样比较符合逻辑，从第一次返回拿id感觉不对