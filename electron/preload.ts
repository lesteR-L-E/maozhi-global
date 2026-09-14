import { contextBridge, ipcRenderer } from 'electron'
import type { ChatMessage, ProviderConfig, StreamEvent } from './runtime'

type Session = { id: string; title: string; createdAt: string; updatedAt: string; messages: ChatMessage[] }

contextBridge.exposeInMainWorld('maozhi', {
  streamChat: (requestId: string, messages: ChatMessage[], provider: ProviderConfig, onEvent: (event: StreamEvent) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: StreamEvent & { requestId: string }) => {
      if (payload.requestId === requestId) onEvent(payload)
    }
    ipcRenderer.on('agent:stream-event', listener)
    ipcRenderer.send('agent:stream', { requestId, messages, provider })
    return () => ipcRenderer.removeListener('agent:stream-event', listener)
  },
  cancelChat: (requestId: string) => ipcRenderer.send('agent:cancel', requestId),
  listSessions: (): Promise<Session[]> => ipcRenderer.invoke('session:list'),
  saveSession: (session: Session): Promise<Session> => ipcRenderer.invoke('session:save', session),
  createSession: (session: Session): Promise<Session> => ipcRenderer.invoke('session:create', session)
})
