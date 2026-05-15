# Context Handoff Skill

## Description

This skill enables the agent to participate in context handoff operations
orchestrated by the Handoff Orchestrator. The agent can:

- Receive context packages when resuming another agent's task
- Report readiness to accept handoffs via capability negotiation
- Understand handoff semantics in system prompts

## When to Use

- When the system prompt contains a `## 交接任务说明` section
- When instructed by the orchestrator to prepare for handoff
- When the user explicitly requests task delegation to another agent

## Capabilities

Declare these capabilities in your agent configuration to participate:

```json
{
  "skills": [
    {
      "id": "context-handoff",
      "name": "Context Handoff",
      "description": "Can receive and resume tasks from other agents via context packages"
    }
  ]
}
```

## Instructions

### Receiving a Handoff

When your system prompt contains a handoff section:

1. Read the `## 交接任务说明` section carefully.
2. Note the `已完成步骤`, `当前步骤`, and `预期下一步动作`.
3. Continue from the current step. Do not repeat completed work unless
the previous results are questionable.
4. If you identify gaps or ambiguities in the handoff context, ask for
clarification rather than making assumptions.

### Preparing for Handoff (Agent-Side)

If you are the source agent and instructed to prepare:

1. Ensure your current state is stable (finish any pending tool calls).
2. Summarize your progress in the structured format required by the orchestrator.
3. Do **not** attempt to package or transmit context yourself — the orchestrator
   handles all serialization and transfer.

## Limitations

- You cannot initiate a handoff without orchestrator approval.
- All sensitive data is stripped by the orchestrator before transmission.
- You may only see a truncated conversation history (recent messages + summary).
- If the handoff context is insufficient, escalate to the user rather than hallucinate.

## Example

```
## 交接任务说明

你正在接续一个由 **agent-researcher** 处理的任务。
交接原因：token_limit
优先级：high

### 原始任务
Research and summarize the latest developments in quantum computing.

### 当前任务进度

- **已完成步骤**: Searched arXiv for recent papers, identified 3 key papers
- **当前步骤**: Reading paper #2 to extract experimental results
- **关键中间结果**: Paper #1 shows 127-qubit stable operation; Paper #2 pending
- **遇到的问题/阻塞点**: 无
- **预期下一步动作**: Complete reading Paper #2, then synthesize findings

---
请基于以上信息继续任务。如有不确定之处，请明确说明。
```
