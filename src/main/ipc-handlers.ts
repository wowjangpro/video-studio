import { ipcMain, BrowserWindow } from 'electron'
import { checkHealth } from './services/health-check'
import { resourceManager } from './services/resource-manager'
import { registerAutocutIpc, cleanupAutocutProcess } from './modules/autocut/ipc'
import { registerSubtitleIpc, cleanupSubtitleProcess } from './modules/subtitle/ipc'
import { registerBgmIpc, cleanupBgmProcess } from './modules/bgm/ipc'

export function getMainWindow(): BrowserWindow | null {
  const windows = BrowserWindow.getAllWindows()
  return windows.length > 0 ? windows[0] : null
}

export function sendToRenderer(channel: string, ...args: unknown[]): void {
  const win = getMainWindow()
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, ...args)
  }
}

export function registerIpcHandlers(): void {
  // 공통: 헬스체크
  ipcMain.handle('check-health', async () => {
    return await checkHealth()
  })

  // 공통: 리소스 상태 조회
  ipcMain.handle('get-resource-status', () => {
    return {
      activeModules: resourceManager.getActiveModules()
    }
  })

  // 공통: 미디어 URL
  ipcMain.handle('get-media-url', async (_event, filePath: string) => {
    return 'file://' + filePath
  })

  // 모듈별 IPC 등록
  registerAutocutIpc()
  registerSubtitleIpc()
  registerBgmIpc()
}

export function cleanupAllProcesses(): void {
  cleanupAutocutProcess()
  cleanupSubtitleProcess()
  cleanupBgmProcess()
}
