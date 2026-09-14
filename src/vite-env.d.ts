/// <reference types="vite/client" />

type ToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } }
type ChatMessage =
  | { role: 'system'; content: string }
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; tool_calls?: ToolCall[] }
  | { role: 'tool'; content: string; tool_call_id: string; name: string }
type ProviderConfig = { provider: 'deepseek' | 'openai-compatible'; apiKey: string; model: string; baseUrl?: string }
type Usage = { promptTokens: number; completionTokens: number }
type AgentStreamEvent =
  | { type: 'chunk'; content: string }
  | { type: 'tool_start'; callId: string; name: string; argsText: string }
  | { type: 'tool_result'; callId: string; name: string; ok: boolean; content: string }
  | { type: 'done'; usage: Usage }
  | { type: 'error'; error: { code: string; message: string } }
type PersistedSession = { id: string; title: string; createdAt: string; updatedAt: string; messages: ChatMessage[] }

interface Window {
  maozhi: {
    streamChat: (requestId: string, sessionId: string, content: string, provider: ProviderConfig, onEvent: (event: AgentStreamEvent) => void) => () => void
    cancelChat: (requestId: string) => void
    listSessions: () => Promise<PersistedSession[]>
    createSession: () => Promise<PersistedSession>
  }
}
