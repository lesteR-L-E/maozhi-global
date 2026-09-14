/// <reference types="vite/client" />

type ChatRole = 'system' | 'user' | 'assistant'
type ChatMessage = { role: ChatRole; content: string }
type ProviderConfig = { provider: 'deepseek' | 'openai-compatible'; apiKey: string; model: string; baseUrl?: string }
type ProviderResponse = { ok: boolean; content?: string; error?: { code: string; message: string } }
type StreamEvent = { type: 'chunk'; content: string } | { type: 'done'; usage?: { promptTokens?: number; completionTokens?: number } } | { type: 'error'; error: { code: string; message: string } }
type PersistedSession = { id: string; title: string; createdAt: string; updatedAt: string; messages: ChatMessage[] }

interface Window {
  maozhi: {
    streamChat: (requestId: string, messages: ChatMessage[], provider: ProviderConfig, onEvent: (event: StreamEvent) => void) => () => void
    cancelChat: (requestId: string) => void
    listSessions: () => Promise<PersistedSession[]>
    saveSession: (session: PersistedSession) => Promise<PersistedSession>
    createSession: (session: PersistedSession) => Promise<PersistedSession>
  }
}
