import { ipcMain, dialog, BrowserWindow } from 'electron'
import { ChildProcess } from 'child_process'
import { runPythonScript } from '../../services/python'
import { sendToRenderer } from '../../ipc-handlers'
import { resourceManager } from '../../services/resource-manager'

let activeProcess: ChildProcess | null = null

function killActiveProcess(): void {
  if (activeProcess) {
    activeProcess.kill('SIGTERM')
    activeProcess = null
  }
}

export function registerBgmIpc(): void {
  ipcMain.handle('bgm:select-file', async () => {
    const win = BrowserWindow.getAllWindows()[0]
    if (!win) return null

    const result = await dialog.showOpenDialog(win, {
      title: '영상 파일 선택',
      filters: [
        {
          name: '비디오 파일',
          extensions: ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v']
        },
        { name: '모든 파일', extensions: ['*'] }
      ],
      properties: ['openFile']
    })

    return result.canceled ? null : result.filePaths[0]
  })

  ipcMain.handle(
    'bgm:analyze-video',
    async (_event, filePath: string, rangeStart: number, rangeEnd: number, preference: string) => {
      killActiveProcess()

      const conflict = resourceManager.acquire('bgm', ['ollama'])
      if (conflict.conflict) {
        sendToRenderer('bgm:error', conflict.message)
        return
      }

      activeProcess = runPythonScript(
        'bgm',
        'generate.py',
        ['analyze', filePath, String(rangeStart), String(rangeEnd), preference || ''],
        (data) => {
          const type = data.type as string
          if (type === 'progress') {
            sendToRenderer('bgm:progress', {
              stage: data.stage,
              percent: data.percent,
              message: data.message
            })
          } else if (type === 'analyzed') {
            sendToRenderer('bgm:analyze-complete', data.scene_description, data.music_prompt || '')
            resourceManager.release('bgm')
            activeProcess = null
          } else if (type === 'error') {
            sendToRenderer('bgm:error', data.message)
            resourceManager.release('bgm')
            activeProcess = null
          }
        },
        (stderr) => {
          if (stderr.includes('Traceback') || stderr.includes('Error')) {
            sendToRenderer('bgm:error', stderr)
          }
        }
      )

      activeProcess.on('exit', (code) => {
        const wasActive = activeProcess !== null
        if (code !== 0 && wasActive) {
          sendToRenderer('bgm:error', `프로세스가 비정상 종료되었습니다 (code: ${code})`)
        }
        activeProcess = null
        if (wasActive) {
          resourceManager.release('bgm')
        }
      })
    }
  )

  ipcMain.handle(
    'bgm:generate-bgm',
    async (
      _event,
      filePath: string,
      rangeStart: number,
      rangeEnd: number,
      prompt: string,
      count: number
    ) => {
      killActiveProcess()

      const conflict = resourceManager.acquire('bgm', ['ollama'])
      if (conflict.conflict) {
        sendToRenderer('bgm:error', conflict.message)
        return
      }

      activeProcess = runPythonScript(
        'bgm',
        'generate.py',
        ['generate', filePath, String(rangeStart), String(rangeEnd), prompt, String(count || 1)],
        (data) => {
          const type = data.type as string
          if (type === 'progress') {
            sendToRenderer('bgm:progress', {
              stage: data.stage,
              percent: data.percent,
              message: data.message
            })
          } else if (type === 'complete') {
            sendToRenderer('bgm:generate-complete', data.bgm_paths)
            resourceManager.release('bgm')
            activeProcess = null
          } else if (type === 'error') {
            sendToRenderer('bgm:error', data.message)
            resourceManager.release('bgm')
            activeProcess = null
          }
        },
        (stderr) => {
          if (stderr.includes('Traceback') || stderr.includes('Error')) {
            sendToRenderer('bgm:error', stderr)
          }
        }
      )

      activeProcess.on('exit', (code) => {
        const wasActive = activeProcess !== null
        if (code !== 0 && wasActive) {
          sendToRenderer('bgm:error', `프로세스가 비정상 종료되었습니다 (code: ${code})`)
        }
        activeProcess = null
        if (wasActive) {
          resourceManager.release('bgm')
        }
      })
    }
  )

  ipcMain.handle('bgm:cancel-generate', async () => {
    killActiveProcess()
    resourceManager.release('bgm')
  })
}

export function cleanupBgmProcess(): void {
  killActiveProcess()
  resourceManager.release('bgm')
}
