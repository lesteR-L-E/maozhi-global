import { FormEvent, useEffect, useRef, useState } from "react";

type ChatBubble = {
  id: string;
  kind: "chat";
  role: "user" | "assistant";
  content: string;
  error?: boolean;
};
type ToolRecord = {
  id: string;
  kind: "tool";
  name: string;
  status: "running" | "done" | "failed";
  result?: string;
};
type UiMessage = ChatBubble | ToolRecord;

// 渲染层只做展示：历史由主进程持有，这里把会话文件里的消息映射成气泡/工具卡片
function toUiMessages(messages: ChatMessage[], sessionId: string): UiMessage[] {
  return messages.flatMap((message, index) => {
    const id = `${sessionId}-${index}`;
    if (message.role === "user") {
      return [{ id, kind: "chat", role: "user", content: message.content }] as UiMessage[];
    }
    if (message.role === "assistant") {
      if (!message.content) return []; // 纯工具调用轮次没有可展示的文本
      return [{ id, kind: "chat", role: "assistant", content: message.content }] as UiMessage[];
    }
    if (message.role === "tool") {
      return [{ id, kind: "tool", name: message.name, status: "done", result: message.content }] as UiMessage[];
    }
    return [];
  });
}

function App() {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<PersistedSession[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [provider, setProvider] = useState<ProviderConfig>(() => ({
    provider: "deepseek",
    apiKey: localStorage.getItem("maozhi-api-key") || "",
    model: localStorage.getItem("maozhi-model") || "",
    baseUrl: localStorage.getItem("maozhi-base-url") || undefined,
  }));
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeRequest = useRef<{ id: string; cleanup: () => void } | null>(null);
  const historyAnchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void (async () => {
      const stored = await window.maozhi.listSessions();
      setSessions(stored);
      if (stored[0]) {
        setSessionId(stored[0].id);
        setMessages(toUiMessages(stored[0].messages, stored[0].id));
      } else {
        await createNewSession();
      }
    })();
  }, []);

  useEffect(
    () => () => {
      if (activeRequest.current) {
        window.maozhi.cancelChat(activeRequest.current.id);
        activeRequest.current.cleanup();
      }
    },
    [],
  );

  useEffect(() => {
    if (!historyOpen) return;
    const handleOutsideClick = (event: MouseEvent) => {
      if (!historyAnchorRef.current?.contains(event.target as Node)) {
        setHistoryOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [historyOpen]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, sending]);

  async function refreshSessions() {
    setSessions(await window.maozhi.listSessions());
  }

  async function createNewSession() {
    if (sending) return;
    const session = await window.maozhi.createSession();
    setSessions((current) => [session, ...current]);
    setSessionId(session.id);
    setMessages(toUiMessages(session.messages, session.id));
    setInput("");
    setHistoryOpen(false);
  }

  function openHistory() {
    if (sending) return;
    setHistoryOpen(true);
    void refreshSessions();
  }

  function switchSession(session: PersistedSession) {
    if (sending) return;
    setSessionId(session.id);
    setMessages(toUiMessages(session.messages, session.id));
    setInput("");
    setHistoryOpen(false);
  }

  function saveProvider(next: ProviderConfig) {
    setProvider(next);
    localStorage.setItem("maozhi-api-key", next.apiKey);
    localStorage.setItem("maozhi-model", next.model);
    if (next.baseUrl) localStorage.setItem("maozhi-base-url", next.baseUrl);
    else localStorage.removeItem("maozhi-base-url");
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const content = input.trim();
    if (!content || sending || !sessionId) return;
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), kind: "chat", role: "user", content },
    ]);
    setInput("");
    setSending(true);
    const requestId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    let assistantText = "";
    const upsertAssistant = (error?: boolean) =>
      setMessages((current) => {
        const bubble: ChatBubble = {
          id: assistantId,
          kind: "chat",
          role: "assistant",
          content: assistantText,
          error,
        };
        return current.some((item) => item.id === assistantId)
          ? current.map((item) => (item.id === assistantId ? bubble : item))
          : [...current, bubble];
      });
    const finish = () => {
      setSending(false);
      activeRequest.current?.cleanup();
      activeRequest.current = null;
    };
    const cleanup = window.maozhi.streamChat(
      requestId,
      sessionId,
      content,
      provider,
      (streamEvent) => {
        if (streamEvent.type === "chunk") {
          assistantText += streamEvent.content;
          upsertAssistant();
        } else if (streamEvent.type === "tool_start") {
          setMessages((current) => [
            ...current,
            {
              id: streamEvent.callId,
              kind: "tool",
              name: streamEvent.name,
              status: "running",
            },
          ]);
        } else if (streamEvent.type === "tool_result") {
          setMessages((current) =>
            current.map((item) =>
              item.id === streamEvent.callId && item.kind === "tool"
                ? {
                    ...item,
                    status: streamEvent.ok ? "done" : "failed",
                    result: streamEvent.content,
                  }
                : item,
            ),
          );
        } else if (streamEvent.type === "error") {
          assistantText = streamEvent.error.message;
          upsertAssistant(true);
          finish();
        } else if (streamEvent.type === "done") {
          void refreshSessions();
          finish();
        }
      },
    );
    activeRequest.current = { id: requestId, cleanup };
  }

  return (
    <main className="app-shell">
      <section className="map-stage" aria-label="全球市场地图占位区">
        <div className="brand-lockup">
          <div className="brand-mark">贸</div>
          <div>
            <div className="brand-name">贸智全球</div>
            <div className="brand-subtitle">GLOBAL TRADE INTELLIGENCE</div>
          </div>
        </div>
        <div className="map-placeholder">
          <div className="map-grid" />
          <div className="map-ring ring-one" />
          <div className="map-ring ring-two" />
          <div className="map-ring ring-three" />
          <div className="map-copy">
            <span className="eyebrow">MARKET OPPORTUNITY MAP</span>
            <h1>全球市场决策工作台</h1>
            <p>地图与市场机会标注将在下一阶段接入</p>
          </div>
          <span className="map-point point-one" />
          <span className="map-point point-two" />
          <span className="map-point point-three" />
        </div>
        <div className="stage-footer">
          <span className="status-dot" /> 本地演示环境{" "}
          <span className="footer-divider" /> 数据与地图模块待接入
        </div>
      </section>

      <aside className="chat-panel">
        <header className="chat-header">
          <div>
            <div className="panel-title">决策助手</div>
            <div className="panel-status">
              <span className="online-dot" /> Agent 模式 · 已接入演示工具
              greet_user
            </div>
          </div>
          <div className="header-actions" ref={historyAnchorRef}>
            <button
              className="icon-button"
              onClick={() => void createNewSession()}
              title="新建对话"
            >
              <span className="action-glyph new-session-glyph">＋</span>
            </button>
            <button
              className="icon-button"
              onClick={openHistory}
              title="历史对话"
              aria-label="历史对话"
            >
              <span className="action-glyph history-icon" aria-hidden="true">◷</span>
            </button>
            <button
              className="icon-button"
              onClick={() => setSettingsOpen(true)}
              title="配置模型"
            >
              <span className="action-glyph">⚙</span>
            </button>
            {historyOpen && (
              <HistoryDialog
                sessions={sessions}
                activeSessionId={sessionId}
                onSelect={switchSession}
                onClose={() => setHistoryOpen(false)}
              />
            )}
          </div>
        </header>
        <div className="conversation" ref={scrollRef}>
          <div className="date-chip">今天</div>
          {messages.map((message) =>
            message.kind === "tool" ? (
              <ToolBadge key={message.id} message={message} />
            ) : (
              <MessageBubble key={message.id} message={message} />
            ),
          )}
          {sending && (
            <div className="message-row assistant-row">
              <div className="avatar assistant-avatar">贸</div>
              <div className="typing-bubble">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>
        <div className="composer-wrap">
          <div className="quick-prompts">
            <button onClick={() => setInput("你好")}>打个招呼（触发工具）</button>
            <button onClick={() => setInput("我想了解这个系统现在能做什么")}>
              了解系统
            </button>
            <button onClick={() => setInput("我卖家用咖啡机，想进入海外市场")}>
              试试完整问题
            </button>
          </div>
          <form className="composer" onSubmit={sendMessage}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder="输入你的问题…"
              rows={1}
              disabled={sending}
            />
            <button
              className="send-button"
              type="submit"
              disabled={!input.trim() || sending}
              aria-label="发送"
            >
              ↑
            </button>
          </form>
          <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
        </div>
      </aside>
      {settingsOpen && (
        <SettingsDialog
          provider={provider}
          onSave={(next) => {
            saveProvider(next);
            setSettingsOpen(false);
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </main>
  );
}

function HistoryDialog({
  sessions,
  activeSessionId,
  onSelect,
  onClose,
}: {
  sessions: PersistedSession[];
  activeSessionId: string;
  onSelect: (session: PersistedSession) => void;
  onClose: () => void;
}) {
  return (
    <section className="history-dialog" role="dialog" aria-label="历史对话" onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-heading">
          <div><div className="dialog-title">历史对话</div><div className="dialog-caption">本地保存的会话记录</div></div>
          <button className="close-button" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="history-list">
          {sessions.length === 0 && <div className="history-empty">还没有历史对话</div>}
          {sessions.map((session) => (
            <button key={session.id} className={`history-item ${session.id === activeSessionId ? "active" : ""}`} onClick={() => onSelect(session)}>
              <span className="history-item-icon">⌁</span>
              <span className="history-item-copy"><strong>{session.title || "新对话"}</strong><small>{formatSessionDate(session.updatedAt)} · {session.messages.filter((message) => message.role !== "system").length} 条消息</small></span>
              {session.id === activeSessionId && <span className="history-current">当前</span>}
            </button>
          ))}
        </div>
    </section>
  );
}

function formatSessionDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知时间";
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function MessageBubble({ message }: { message: ChatBubble }) {
  const isUser = message.role === "user";
  return (
    <div className={`message-row ${isUser ? "user-row" : "assistant-row"}`}>
      <div className={`avatar ${isUser ? "user-avatar" : "assistant-avatar"}`}>
        {isUser ? "我" : "贸"}
      </div>
      <div
        className={`bubble ${isUser ? "user-bubble" : "assistant-bubble"} ${message.error ? "error-bubble" : ""}`}
      >
        {message.content}
      </div>
    </div>
  );
}

function ToolBadge({ message }: { message: ToolRecord }) {
  return (
    <div className="tool-row">
      <span className={`tool-status ${message.status}`} />
      <span className="tool-name">{message.name}</span>
      <span className="tool-state">
        {message.status === "running"
          ? "调用中…"
          : message.status === "failed"
            ? "失败"
            : "完成"}
      </span>
      {message.result && <span className="tool-result">{message.result}</span>}
    </div>
  );
}

function SettingsDialog({
  provider,
  onSave,
  onClose,
}: {
  provider: ProviderConfig;
  onSave: (provider: ProviderConfig) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(provider);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="settings-dialog">
        <div className="dialog-heading">
          <div>
            <div className="dialog-title">模型连接</div>
            <div className="dialog-caption">
              设置非空值优先；留空时读取主进程环境变量
            </div>
          </div>
          <button className="close-button" onClick={onClose}>
            ×
          </button>
        </div>
        <label>
          Provider
          <select
            value={draft.provider}
            onChange={(event) =>
              setDraft({
                ...draft,
                provider: event.target.value as ProviderConfig["provider"],
                model: "",
              })
            }
          >
            <option value="deepseek">DeepSeek</option>
            <option value="openai-compatible">OpenAI Compatible</option>
          </select>
        </label>
        <label>
          Model
          <input
            value={draft.model}
            onChange={(event) => setDraft({ ...draft, model: event.target.value })}
            placeholder={
              draft.provider === "deepseek"
                ? "留空使用 DEEPSEEK_MODEL / deepseek-chat"
                : "留空使用 OPENAI_MODEL / gpt-4o-mini"
            }
          />
        </label>
        <label>
          API Key
          <input
            type="password"
            value={draft.apiKey}
            onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
            placeholder="留空读取环境变量"
          />
        </label>
        <label>
          Base URL
          <input
            value={draft.baseUrl || ""}
            onChange={(event) =>
              setDraft({ ...draft, baseUrl: event.target.value || undefined })
            }
            placeholder={
              draft.provider === "deepseek"
                ? "留空使用 DEEPSEEK_BASE_URL / 官方地址"
                : "留空使用 OPENAI_BASE_URL / 官方地址"
            }
          />
        </label>
        <button className="save-button" onClick={() => onSave(draft)}>
          保存配置
        </button>
      </section>
    </div>
  );
}

export default App;
