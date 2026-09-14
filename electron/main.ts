import 'dotenv/config'
import { app, BrowserWindow, ipcMain, shell } from 'electron'
import path from 'node:path'
import fs from 'node:fs/promises'
import { AgentRuntime, ChatMessage, ProviderConfig, StreamEvent } from './runtime'

let mainWindow: BrowserWindow | null = null
const runtime = new AgentRuntime()
const sessionsDir = () => path.join(app.getPath('userData'), 'sessions')

type Session = { id: string; title: string; createdAt: string; updatedAt: string; messages: ChatMessage[] }

async function ensureSessionsDir() {
  await fs.mkdir(sessionsDir(), { recursive: true })
}

async function readSessions(): Promise<Session[]> {
  await ensureSessionsDir()
  const names = (await fs.readdir(sessionsDir())).filter((name) => name.endsWith('.json'))
  const sessions: Session[] = []
  for (const name of names) {
    try { sessions.push(JSON.parse(await fs.readFile(path.join(sessionsDir(), name), 'utf8')) as Session) } catch { /* ignore corrupt session */ }
  }
  return sessions.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

async function writeSession(session: Session) {
  await ensureSessionsDir()
  await fs.writeFile(path.join(sessionsDir(), `${session.id}.json`), JSON.stringify(session, null, 2), 'utf8')
  return session
}

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
  ipcMain.handle('session:list', () => readSessions())
  ipcMain.handle('session:save', (_event, session: Session) => writeSession(session))
  ipcMain.handle('session:create', async (_event, session: Session) => writeSession(session))
  ipcMain.on('agent:cancel', (_event, requestId: string) => runtime.cancel(requestId))
  ipcMain.on('agent:stream', async (event, payload: { requestId: string; messages: ChatMessage[]; provider: ProviderConfig }) => {
    await runtime.stream(payload.messages, payload.provider, (streamEvent: StreamEvent) => {
      if (!event.sender.isDestroyed()) event.sender.send('agent:stream-event', { requestId: payload.requestId, ...streamEvent })
    }, payload.requestId)
  })

  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
