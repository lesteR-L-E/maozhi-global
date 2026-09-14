export type ToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } }

export type ChatMessage =
  | { role: 'system'; content: string }
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; tool_calls?: ToolCall[] }
  | { role: 'tool'; content: string; tool_call_id: string; name: string }

export type ProviderConfig = {
  provider: 'deepseek' | 'openai-compatible'
  apiKey: string
  model: string
  baseUrl?: string
}

export type Usage = { promptTokens: number; completionTokens: number }

export type ToolSchema = {
  type: 'function'
  function: { name: string; description: string; parameters: Record<string, unknown> }
}

export type StreamOnceResult =
  | { status: 'ok'; content: string; toolCalls: ToolCall[]; usage?: { promptTokens?: number; completionTokens?: number } }
  | { status: 'aborted' }
  | { status: 'error'; error: { code: string; message: string } }

export type AgentStreamEvent =
  | { type: 'chunk'; content: string }
  | { type: 'tool_start'; callId: string; name: string; argsText: string }
  | { type: 'tool_result'; callId: string; name: string; ok: boolean; content: string }
  | { type: 'done'; usage: Usage }
  | { type: 'error'; error: { code: string; message: string } }

type ToolCallFragment = { index?: number; id?: string; function?: { name?: string; arguments?: string } }

type StreamChunk = {
  choices?: Array<{
    delta?: { content?: string | null; tool_calls?: ToolCallFragment[] }
    finish_reason?: string | null
  }>
  usage?: { prompt_tokens?: number; completion_tokens?: number }
  error?: { message?: string }
}

type HttpErrorBody = { error?: { message?: string; type?: string } }

const SYSTEM_PROMPT = `你是“贸智全球”的智能决策助手，运行在本地 Agent 运行时中。
你可以调用工具完成任务：当前可用工具为 greet_user（为用户生成一段欢迎语）。当用户发送问候（如“你好”“您好”“hi”）时，必须调用 greet_user，再基于工具返回的内容组织自然、口语化的回复；其他常规问题直接回答。
当前尚未接入贸易、客户、市场等真实数据工具。不要编造任何贸易数据、客户数据、市场排名、预测、评分或工具调用结果；如果用户要求分析具体市场、客户或指标，请明确说明相关工具尚未接入，后续会基于结构化数据完成。
回答使用中文，简洁、专业、可执行；如果用户的问题缺少必要信息，可以提出澄清问题。`

function effectiveConfig(config: ProviderConfig): ProviderConfig {
  const isDeepSeek = config.provider === 'deepseek'
  const env = isDeepSeek
    ? {
        apiKey: process.env.DEEPSEEK_API_KEY || process.env.LLM_API_KEY || '',
        baseUrl: process.env.DEEPSEEK_BASE_URL || process.env.LLM_BASE_URL,
        model: process.env.DEEPSEEK_MODEL || process.env.LLM_MODEL || 'deepseek-chat'
      }
    : {
        apiKey: process.env.OPENAI_API_KEY || process.env.LLM_API_KEY || '',
        baseUrl: process.env.OPENAI_BASE_URL || process.env.LLM_BASE_URL,
        model: process.env.OPENAI_MODEL || process.env.LLM_MODEL || 'gpt-4o-mini'
      }
  return {
    ...config,
    apiKey: config.apiKey.trim() || env.apiKey.trim(),
    baseUrl: config.baseUrl?.trim() || env.baseUrl?.trim(),
    model: config.model.trim() || env.model
  }
}

export class AgentRuntime {
  private controllers = new Map<string, AbortController>()

  cancel(requestId: string) {
    this.controllers.get(requestId)?.abort()
    this.controllers.delete(requestId)
  }

  // 单次 LLM 调用：流式返回文本增量，结束后给出本次调用产生的完整 tool_calls
  async streamOnce(options: {
    messages: ChatMessage[]
    config: ProviderConfig
    tools: ToolSchema[]
    onChunk: (content: string) => void
    requestId?: string
  }): Promise<StreamOnceResult> {
    const config = effectiveConfig(options.config)
    if (!config.apiKey.trim()) {
      return { status: 'error', error: { code: 'MISSING_API_KEY', message: '请先在右上角配置LLM API Key。' } }
    }

    const history = options.messages.filter((message) => message.role !== 'system')
    const body = {
      model: config.model,
      messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...history],
      temperature: 0.4,
      stream: true,
      ...(options.tools.length ? { tools: options.tools, tool_choice: 'auto' } : {})
    }
    const baseUrl = (config.baseUrl || (config.provider === 'deepseek' ? 'https://api.deepseek.com' : 'https://api.openai.com')).replace(/\/$/, '')

    const controller = new AbortController()
    if (options.requestId) this.controllers.set(options.requestId, controller)
    try {
      const response = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.apiKey.trim()}` },
        body: JSON.stringify(body),
        signal: controller.signal
      })
      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as HttpErrorBody
        return { status: 'error', error: { code: data.error?.type || `HTTP_${response.status}`, message: data.error?.message || 'LLM请求失败。' } }
      }
      if (!response.body) {
        return { status: 'error', error: { code: 'NO_STREAM', message: 'LLM服务未返回可读取的流。' } }
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let content = ''
      let streamError = ''
      let usage: { promptTokens?: number; completionTokens?: number } | undefined
      // 流式 tool_calls 按index分片到达：id/name在首个分片，arguments逐段追加
      const toolCalls = new Map<number, ToolCall>()
      const mergeToolCall = (fragment: ToolCallFragment) => {
        const index = fragment.index ?? 0
        const existing = toolCalls.get(index) ?? { id: '', type: 'function' as const, function: { name: '', arguments: '' } }
        if (fragment.id) existing.id = fragment.id
        if (fragment.function?.name) existing.function.name = existing.function.name || fragment.function.name
        if (fragment.function?.arguments) existing.function.arguments += fragment.function.arguments
        toolCalls.set(index, existing)
      }
      const consume = (line: string) => {
        if (!line.startsWith('data:')) return
        const payload = line.slice(5).trim()
        if (!payload || payload === '[DONE]') return
        try {
          const data = JSON.parse(payload) as StreamChunk
          const delta = data.choices?.[0]?.delta
          if (delta?.content) {
            content += delta.content
            options.onChunk(delta.content)
          }
          delta?.tool_calls?.forEach(mergeToolCall)
          if (data.usage) usage = { promptTokens: data.usage.prompt_tokens, completionTokens: data.usage.completion_tokens }
          if (data.error) streamError = data.error.message || 'LLM流式响应失败。'
        } catch {
          // Providers may split a JSON payload across chunks; incomplete lines remain buffered.
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        const lines = buffer.split(/\r?\n/)
        buffer = lines.pop() || ''
        lines.forEach(consume)
        if (done) break
      }
      if (streamError) return { status: 'error', error: { code: 'LLM_STREAM_ERROR', message: streamError } }
      const calls = [...toolCalls.entries()].sort(([a], [b]) => a - b).map(([, call]) => call)
      return { status: 'ok', content: content.trim(), toolCalls: calls, usage }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return { status: 'aborted' }
      return { status: 'error', error: { code: 'NETWORK_ERROR', message: error instanceof Error ? error.message : '无法连接LLM服务。' } }
    } finally {
      if (options.requestId) this.controllers.delete(options.requestId)
    }
  }
}
