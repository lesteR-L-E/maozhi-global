import { AgentRuntime, AgentStreamEvent, ChatMessage, ProviderConfig } from './runtime'
import { loadSession, saveSession } from './sessions'
import { executeTool, toolSchemas } from './tools'

const MAX_TOOL_ROUNDS = 6

// 一次用户输入对应一个完整回合：上下文拼接、工具执行、历史落盘全部在主进程完成，
// 渲染进程只提供 sessionId + 用户文本，并接收用于展示的流式事件。
export async function runAgentTurn(options: {
  runtime: AgentRuntime
  sessionId: string
  userText: string
  provider: ProviderConfig
  requestId: string
  emit: (event: AgentStreamEvent) => void
}): Promise<void> {
  const { runtime, sessionId, userText, provider, requestId, emit } = options

  const session = await loadSession(sessionId)
  if (!session) {
    emit({ type: 'error', error: { code: 'SESSION_NOT_FOUND', message: '会话不存在或已损坏，请新建对话。' } })
    return
  }

  const messages: ChatMessage[] = [...session.messages, { role: 'user', content: userText }]
  // 先落盘用户消息，中途退出或出错时输入不丢失
  await saveSession({ ...session, messages })

  const usage = { promptTokens: 0, completionTokens: 0 }

  for (let round = 1; round <= MAX_TOOL_ROUNDS; round++) {
    const result = await runtime.streamOnce({
      messages,
      config: provider,
      tools: toolSchemas(),
      requestId,
      onChunk: (content) => emit({ type: 'chunk', content })
    })

    if (result.status === 'aborted') return
    if (result.status === 'error') {
      emit({ type: 'error', error: result.error })
      return
    }

    usage.promptTokens += result.usage?.promptTokens ?? 0
    usage.completionTokens += result.usage?.completionTokens ?? 0

    if (result.toolCalls.length > 0) {
      // 模型要求调用工具：先记录 assistant 的调用意图，再逐个执行并把结果追加进上下文
      messages.push({ role: 'assistant', content: result.content, tool_calls: result.toolCalls })
      for (const call of result.toolCalls) {
        emit({ type: 'tool_start', callId: call.id, name: call.function.name, argsText: call.function.arguments })
        const toolResult = await executeTool(call.function.name, call.function.arguments)
        emit({ type: 'tool_result', callId: call.id, name: call.function.name, ok: toolResult.ok, content: toolResult.content })
        messages.push({ role: 'tool', content: toolResult.content, tool_call_id: call.id, name: call.function.name })
      }
      await saveSession({ ...session, messages })
      continue
    }

    if (!result.content) {
      emit({ type: 'error', error: { code: 'EMPTY_RESPONSE', message: 'LLM返回了空内容，请重试。' } })
      return
    }

    messages.push({ role: 'assistant', content: result.content })
    await saveSession({ ...session, messages })
    emit({ type: 'done', usage })
    return
  }

  emit({ type: 'error', error: { code: 'MAX_TOOL_ROUNDS', message: `连续工具调用超过 ${MAX_TOOL_ROUNDS} 轮，已停止执行。` } })
}
