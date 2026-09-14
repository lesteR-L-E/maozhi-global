import 'dotenv/config'
import { app, BrowserWindow, ipcMain, shell } from 'electron'
import path from 'node:path'
import { AgentRuntime, AgentStreamEvent, ProviderConfig } from './runtime'
import { runAgentTurn } from './agent'
import { createSession, listSessions } from './sessions'

let mainWindow: BrowserWindow | null = null
const runtime = new AgentRuntime()

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#f4f7fb',
    title: '贸智全球',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  const devUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173'
  if (!app.isPackaged) {
    void mainWindow.loadURL(devUrl).catch(() => mainWindow?.loadFile(path.join(__dirname, '../dist/index.html')))
  } else void mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url)
    return { action: 'deny' }
  })
}

app.whenReady().then(() => {
  ipcMain.handle('session:list', () => listSessions())
  ipcMain.handle('session:create', () => createSession())
  ipcMain.on('agent:cancel', (_event, requestId: string) => runtime.cancel(requestId))
  ipcMain.on('agent:stream', (event, payload: { requestId: string; sessionId: string; content: string; provider: ProviderConfig }) => {
    if (!payload?.sessionId || typeof payload.content !== 'string' || !payload.content.trim()) return
    const emit = (streamEvent: AgentStreamEvent) => {
      if (!event.sender.isDestroyed()) event.sender.send('agent:stream-event', { requestId: payload.requestId, ...streamEvent })
    }
    void runAgentTurn({
      runtime,
      sessionId: payload.sessionId,
      userText: payload.content.trim(),
      provider: payload.provider,
      requestId: payload.requestId,
      emit
    })
  })

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
