import { app, BrowserWindow, nativeImage, shell } from 'electron'
import { join } from 'path'
import { homedir } from 'os'
import { is } from '@electron-toolkit/utils'
import { registerIpcHandlers, cleanupAllProcesses } from './ipc-handlers'

// Finder에서 더블클릭으로 실행한 .app은 launchd 기본 PATH만 가지므로
// 자식 프로세스(ffmpeg/ffprobe/python, 그리고 python이 다시 spawn하는 claude CLI)가
// 바이너리를 찾을 수 있도록 PATH를 보강한다.
//   - /opt/homebrew/bin, /usr/local/bin : ffmpeg/ffprobe/ollama (homebrew)
//   - ~/.local/bin                       : claude CLI (편집 단계 subprocess).
//     이게 없으면 dev는 되는데 빌드 앱에서만 편집이 "Claude CLI를 찾을 수 없습니다"로
//     중간에 실패한다(storyboard.run_narrative_editing_claude).
if (process.platform === 'darwin') {
  const extras = ['/opt/homebrew/bin', '/usr/local/bin', join(homedir(), '.local', 'bin')]
  const current = (process.env.PATH || '').split(':')
  const merged = [...extras.filter((p) => !current.includes(p)), ...current]
  process.env.PATH = merged.join(':')
}

process.on('uncaughtException', (err) => {
  if (err.message.includes('EPIPE')) return
  console.error(err)
})

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    show: false,
    title: 'Video Studio',
    icon: join(__dirname, '../../build/icon.png'),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      webSecurity: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  if (process.platform === 'darwin') {
    const dockIcon = nativeImage.createFromPath(join(__dirname, '../../build/icon.png'))
    if (!dockIcon.isEmpty()) app.dock.setIcon(dockIcon)
  }
  registerIpcHandlers()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('will-quit', () => {
  cleanupAllProcesses()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
