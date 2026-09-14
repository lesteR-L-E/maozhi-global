import { contextBridge, ipcRenderer } from 'electron'
import type { AgentStreamEvent, ProviderConfig } from './runtime'
import type { Session } from './sessions'

contextBridge.exposeInMainWorld('maozhi', {
  streamChat: (requestId: string, sessionId: string, content: string, provider: ProviderConfig, onEvent: (event: AgentStreamEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: AgentStreamEvent & { requestId: string }) => {
      if (payload.requestId === requestId) onEvent(payload)
    }
    ipcRenderer.on('agent:stream-event', listener)
    ipcRenderer.send('agent:stream', { requestId, sessionId, content, provider })
    return () => ipcRenderer.removeListener('agent:stream-event', listener)
  },
  cancelChat: (requestId: string) => ipcRenderer.send('agent:cancel', requestId),
  listSessions: (): Promise<Session[]> => ipcRenderer.invoke('session:list'),
  createSession: (): Promise<Session> => ipcRenderer.invoke('session:create')
})
