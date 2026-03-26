# Data Examples

这个目录用于保存可直接参考的示例数据，方便检查生成效果、调试转换逻辑，以及手工挑选高质量 case。

## 当前文件说明

- `intents.jsonl`
  - 原始示例 intent 数据。
- `safety_compliance_audit_session.jsonl`
  - 本轮新挑选的高质量 session 示例。
  - 来源：`output/sessions/intent_intent_5723a36d__gendata-worker-1__2562cd0e-d465-4ab3-9fe0-afc9dc548b73.jsonl`
- `safety_compliance_audit_middle_format.json`
  - 与上面 session 对应的 middle format 示例。
  - 来源：`output/middle_format/intent_intent_5723a36d.json`

## 为什么选择这个 case

选中的 case 是一个 `safety compliance audit` 场景，主要优势：

1. **完成度高**
   - `total_steps = 88`
   - 有明确的最终交付内容，而不是中途停止或仅完成局部步骤。

2. **过程真实**
   - 包含 web search / web fetch / memory search / exec 等多类工具调用。
   - 中间出现了真实失败场景，并展示了后续恢复与兜底处理。

3. **训练价值更高**
   - assistant 的 `reasoning_content` 较完整。
   - 任务链条长，既有调研，也有环境准备、文档生成、问题处理和最终总结。

4. **更适合作为展示样例**
   - 最终输出结构化程度高，能比较完整地反映生成流程质量。
