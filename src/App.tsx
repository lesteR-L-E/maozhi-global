import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'

type UiMessage = ChatMessage & { id: string; pending?: boolean; error?: boolean }

const initialMessages: UiMessage[] = [
  {
    id: 'welcome',
    role: 'assistant',
    content: '你好，我是贸智全球助手。告诉我你想分析的产品、市场或客户问题，我们可以先从需求梳理开始。',
  }
]

function App() {
  const [messages, setMessages] = useState<UiMessage[]>(initialMessages)
  const [sessionId, setSessionId] = useState('')
  const [sessions, setSessions] = useState<PersistedSession[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [provider, setProvider] = useState<ProviderConfig>(() => ({
    provider: 'deepseek',
    apiKey: localStorage.getItem('maozhi-api-key') || '',
    model: localStorage.getItem('maozhi-model') || '',
    baseUrl: localStorage.getItem('maozhi-base-url') || undefined
  }))
  const scrollRef = useRef<HTMLDivElement>(null)
  const activeRequest = useRef<{ id: string; cleanup: () => void } | null>(null)

  useEffect(() => {
    void (async () => {
      const stored = await window.maozhi.listSessions()
      setSessions(stored)
      if (stored[0]) {
        setSessionId(stored[0].id)
        setMessages(stored[0].messages as UiMessage[])
      } else {
        await createNewSession()
      }
    })()
  }, [])

  useEffect(() => () => {
    if (activeRequest.current) {
      window.maozhi.cancelChat(activeRequest.current.id)
      activeRequest.current.cleanup()
    }
  }, [])

  useEffect(() => {
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, sending])

  const visibleMessages = useMemo(() => messages.filter((message) => !message.pending), [messages])

  async function persist(nextMessages: UiMessage[], id = sessionId) {
    if (!id) return
    const firstUser = nextMessages.find((message) => message.role === 'user')
    const existing = sessions.find((session) => session.id === id)
    const session: PersistedSession = {
      id,
      title: existing?.title || (firstUser?.content.slice(0, 24) || '新对话'),
      createdAt: existing?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: nextMessages.map(({ role, content }) => ({ role, content }))
    }
    await window.maozhi.saveSession(session)
    setSessions((current) => [session, ...current.filter((item) => item.id !== id)].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)))
  }

  async function createNewSession() {
    const id = crypto.randomUUID()
    const now = new Date().toISOString()
    const session: PersistedSession = { id, title: '新对话', createdAt: now, updatedAt: now, messages: initialMessages.map(({ role, content }) => ({ role, content })) }
    await window.maozhi.createSession(session)
    setSessions((current) => [session, ...current.filter((item) => item.id !== id)])
    setSessionId(id)
    setMessages(initialMessages)
    setInput('')
  }

  function switchSession(session: PersistedSession) {
    if (sending) return
    setSessionId(session.id)
    setMessages(session.messages.map((message, index) => ({ ...message, id: `${session.id}-${index}` })))
    setInput('')
  }

  function saveProvider(next: ProviderConfig) {
    setProvider(next)
    localStorage.setItem('maozhi-api-key', next.apiKey)
    localStorage.setItem('maozhi-model', next.model)
    if (next.baseUrl) localStorage.setItem('maozhi-base-url', next.baseUrl)
    else localStorage.removeItem('maozhi-base-url')
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault()
    const content = input.trim()
    if (!content || sending) return
    const userMessage: UiMessage = { id: crypto.randomUUID(), role: 'user', content }
    const nextMessages = [...messages, userMessage]
    setMessages(nextMessages)
    await persist(nextMessages)
    setInput('')
    setSending(true)
    const requestId = crypto.randomUUID()
    let assistantText = ''
    const assistantId = crypto.randomUUID()
    const cleanup = window.maozhi.streamChat(requestId, nextMessages.map(({ role, content: text }) => ({ role, content: text })), provider, (event) => {
      if (event.type === 'chunk') {
        assistantText += event.content
        setMessages((current) => {
          const existing = current.find((message) => message.id === assistantId)
          if (existing) return current.map((message) => message.id === assistantId ? { ...message, content: assistantText } : message)
          return [...current, { id: assistantId, role: 'assistant', content: assistantText }]
        })
      } else if (event.type === 'error') {
        assistantText = event.error.message
        setMessages((current) => {
          const errorMessage: UiMessage = { id: assistantId, role: 'assistant', content: assistantText, error: true }
          return current.some((message) => message.id === assistantId)
            ? current.map((message) => message.id === assistantId ? errorMessage : message)
            : [...current, errorMessage]
        })
        setSending(false)
        activeRequest.current?.cleanup()
        activeRequest.current = null
      } else if (event.type === 'done') {
        setSending(false)
        setMessages((current) => {
          void persist([...current.filter((message) => message.id !== assistantId), { id: assistantId, role: 'assistant', content: assistantText }])
          return current
        })
        activeRequest.current?.cleanup()
        activeRequest.current = null
      }
    })
    activeRequest.current = { id: requestId, cleanup }
  }

  return (
    <main className="app-shell">
      <section className="map-stage" aria-label="全球市场地图占位区">
        <div className="brand-lockup">
          <div className="brand-mark">贸</div>
          <div><div className="brand-name">贸智全球</div><div className="brand-subtitle">GLOBAL TRADE INTELLIGENCE</div></div>
        </div>
        <div className="map-placeholder">
          <div className="map-grid" />
          <div className="map-ring ring-one" /><div className="map-ring ring-two" /><div className="map-ring ring-three" />
          <div className="map-copy"><span className="eyebrow">MARKET OPPORTUNITY MAP</span><h1>全球市场决策工作台</h1><p>地图与市场机会标注将在下一阶段接入</p></div>
          <span className="map-point point-one" /><span className="map-point point-two" /><span className="map-point point-three" />
        </div>
        <div className="stage-footer"><span className="status-dot" /> 本地演示环境 <span className="footer-divider" /> 数据与地图模块待接入</div>
      </section>

      <aside className="chat-panel">
        <header className="chat-header">
          <div><div className="panel-title">决策助手</div><div className="panel-status"><span className="online-dot" /> 纯LLM对话 · 暂无工具</div></div>
          <div className="header-actions"><button className="icon-button" onClick={() => void createNewSession()} title="新建对话">＋</button><button className="icon-button" onClick={() => setSettingsOpen(true)} title="配置模型">⚙</button></div>
        </header>
        {sessions.length > 1 && <div className="session-strip">{sessions.slice(0, 4).map((session) => <button key={session.id} className={`session-chip ${session.id === sessionId ? 'active' : ''}`} onClick={() => switchSession(session)}>{session.title}</button>)}</div>}
        <div className="conversation" ref={scrollRef}>
          <div className="date-chip">今天</div>
          {visibleMessages.map((message) => <MessageBubble key={message.id} message={message} />)}
          {sending && <div className="message-row assistant-row"><div className="avatar assistant-avatar">贸</div><div className="typing-bubble"><span /><span /><span /></div></div>}
        </div>
        <div className="composer-wrap">
          <div className="quick-prompts"><button onClick={() => setInput('我想了解这个系统现在能做什么')}>了解系统</button><button onClick={() => setInput('我卖家用咖啡机，想进入海外市场')}>试试完整问题</button></div>
          <form className="composer" onSubmit={sendMessage}>
            <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void sendMessage() } }} placeholder="输入你的问题…" rows={1} disabled={sending} />
            <button className="send-button" type="submit" disabled={!input.trim() || sending} aria-label="发送">↑</button>
          </form>
          <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
        </div>
      </aside>
      {settingsOpen && <SettingsDialog provider={provider} onSave={(next) => { saveProvider(next); setSettingsOpen(false) }} onClose={() => setSettingsOpen(false)} />}
    </main>
  )
}

function MessageBubble({ message }: { message: UiMessage }) {
  const isUser = message.role === 'user'
  return <div className={`message-row ${isUser ? 'user-row' : 'assistant-row'}`}><div className={`avatar ${isUser ? 'user-avatar' : 'assistant-avatar'}`}>{isUser ? '我' : '贸'}</div><div className={`bubble ${isUser ? 'user-bubble' : 'assistant-bubble'} ${message.error ? 'error-bubble' : ''}`}>{message.content}</div></div>
}

function SettingsDialog({ provider, onSave, onClose }: { provider: ProviderConfig; onSave: (provider: ProviderConfig) => void; onClose: () => void }) {
  const [draft, setDraft] = useState(provider)
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section className="settings-dialog"><div className="dialog-heading"><div><div className="dialog-title">模型连接</div><div className="dialog-caption">设置非空值优先；留空时读取主进程环境变量</div></div><button className="close-button" onClick={onClose}>×</button></div><label>Provider<select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value as ProviderConfig['provider'], model: '' })}><option value="deepseek">DeepSeek</option><option value="openai-compatible">OpenAI Compatible</option></select></label><label>Model<input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder={draft.provider === 'deepseek' ? '留空使用 DEEPSEEK_MODEL / deepseek-chat' : '留空使用 OPENAI_MODEL / gpt-4o-mini'} /></label><label>API Key<input type="password" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} placeholder="留空读取环境变量" /></label><label>Base URL<input value={draft.baseUrl || ''} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value || undefined })} placeholder={draft.provider === 'deepseek' ? '留空使用 DEEPSEEK_BASE_URL / 官方地址' : '留空使用 OPENAI_BASE_URL / 官方地址'} /></label><button className="save-button" onClick={() => onSave(draft)}>保存配置</button></section></div>
}

export default App
