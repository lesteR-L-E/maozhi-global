export type ChatRole = 'system' | 'user' | 'assistant'

export type ChatMessage = {
  role: ChatRole
  content: string
}

export type ProviderConfig = {
  provider: 'deepseek' | 'openai-compatible'
  apiKey: string
  model: string
  baseUrl?: string
}

export type ProviderResponse = {
  ok: boolean
  content?: string
  error?: { code: string; message: string }
  usage?: { promptTokens?: number; completionTokens?: number }
}

export type StreamEvent =
  | { type: 'chunk'; content: string }
  | { type: 'done'; usage?: { promptTokens?: number; completionTokens?: number } }
  | { type: 'error'; error: { code: string; message: string } }

type OpenAIResponse = {
  choices?: Array<{ message?: { content?: string } }>
  usage?: { prompt_tokens?: number; completion_tokens?: number }
  error?: { message?: string; type?: string }
}

const SYSTEM_PROMPT = `你是“贸智全球”的智能决策助手。目前处于第一版骨架阶段，暂未接入贸易、客户或其他业务工具。
你的职责是理解用户问题并进行自然语言交流。不要编造任何贸易数据、客户数据、市场排名、预测、评分或工具调用结果。
如果用户要求分析具体市场、客户或指标，请明确说明当前工具尚未接入，并告诉用户后续会基于结构化数据完成，而不是假装已经完成分析。
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

  async run(messages: ChatMessage[], config: ProviderConfig): Promise<ProviderResponse> {
    config = effectiveConfig(config)
    if (!config.apiKey.trim()) {
      return { ok: false, error: { code: 'MISSING_API_KEY', message: '请先在右上角配置LLM API Key。' } }
    }

    const normalized = messages.filter((message) => message.role !== 'system')
    const body = {
      model: config.model,
      messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...normalized],
      temperature: 0.4,
      stream: false
    }
    const baseUrl = (config.baseUrl || (config.provider === 'deepseek' ? 'https://api.deepseek.com' : 'https://api.openai.com')).replace(/\/$/, '')

    try {
      const response = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.apiKey.trim()}` },
        body: JSON.stringify(body)
      })
      const data = (await response.json()) as OpenAIResponse
      if (!response.ok) {
        return { ok: false, error: { code: data.error?.type || `HTTP_${response.status}`, message: data.error?.message || 'LLM请求失败。' } }
      }
      const content = data.choices?.[0]?.message?.content?.trim()
      if (!content) return { ok: false, error: { code: 'EMPTY_RESPONSE', message: 'LLM返回了空内容，请重试。' } }
      return {
        ok: true,
        content,
        usage: { promptTokens: data.usage?.prompt_tokens, completionTokens: data.usage?.completion_tokens }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法连接LLM服务。'
      return { ok: false, error: { code: 'NETWORK_ERROR', message } }
    }
  }

  async stream(messages: ChatMessage[], config: ProviderConfig, onEvent: (event: StreamEvent) => void, requestId?: string): Promise<void> {
    config = effectiveConfig(config)
    if (!config.apiKey.trim()) {
      onEvent({ type: 'error', error: { code: 'MISSING_API_KEY', message: '请先在右上角配置LLM API Key。' } })
      return
    }

    const normalized = messages.filter((message) => message.role !== 'system')
    const body = {
      model: config.model,
      messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...normalized],
      temperature: 0.4,
      stream: true
    }
    const baseUrl = (config.baseUrl || (config.provider === 'deepseek' ? 'https://api.deepseek.com' : 'https://api.openai.com')).replace(/\/$/, '')

    const controller = new AbortController()
    if (requestId) this.controllers.set(requestId, controller)
    try {
      const response = await fetch(`${baseUrl}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.apiKey.trim()}` },
        body: JSON.stringify(body),
        signal: controller.signal
      })
      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as OpenAIResponse
        onEvent({ type: 'error', error: { code: data.error?.type || `HTTP_${response.status}`, message: data.error?.message || 'LLM请求失败。' } })
        return
      }
      if (!response.body) {
        onEvent({ type: 'error', error: { code: 'NO_STREAM', message: 'LLM服务未返回可读取的流。' } })
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let usage: { promptTokens?: number; completionTokens?: number } | undefined
      const consume = (line: string) => {
        if (!line.startsWith('data:')) return
        const payload = line.slice(5).trim()
        if (!payload || payload === '[DONE]') return
        try {
          const data = JSON.parse(payload) as { choices?: Array<{ delta?: { content?: string } }>; usage?: { prompt_tokens?: number; completion_tokens?: number }; error?: { message?: string } }
          const content = data.choices?.[0]?.delta?.content
          if (content) onEvent({ type: 'chunk', content })
          if (data.usage) usage = { promptTokens: data.usage.prompt_tokens, completionTokens: data.usage.completion_tokens }
          if (data.error) onEvent({ type: 'error', error: { code: 'LLM_STREAM_ERROR', message: data.error.message || 'LLM流式响应失败。' } })
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
      onEvent({ type: 'done', usage })
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') return
      const message = error instanceof Error ? error.message : '无法连接LLM服务。'
      onEvent({ type: 'error', error: { code: 'NETWORK_ERROR', message } })
    } finally {
      if (requestId) this.controllers.delete(requestId)
    }
  }
}
