import { ipcMain, dialog, shell, clipboard, BrowserWindow } from 'electron'
import { ChildProcess } from 'child_process'
import { join } from 'path'
import { runPythonOnce, killProcessGroup, type PythonJson } from '../../services/python'
import { sendToRenderer } from '../../ipc-handlers'

const SCRIPT = 'thumbnail.py'

// 지금 돌고 있는 썸네일 생성 (취소할 수 있는 긴 작업은 이것 하나)
let generateProcess: ChildProcess | null = null

function killGenerate(): void {
  if (generateProcess) {
    killProcessGroup(generateProcess)
    generateProcess = null
  }
}

export type ThumbResult<T> = { ok: true; data: T } | { ok: false; message: string }

function toResult<T>(data: PythonJson, pick: (d: PythonJson) => T): ThumbResult<T> {
  if (data.type === 'error') return { ok: false, message: String(data.message) }
  return { ok: true, data: pick(data) }
}

function run(args: string[], doneTypes: string[]): Promise<PythonJson> {
  return runPythonOnce('thumbnail', SCRIPT, args, doneTypes)
}

export function registerThumbnailIpc(): void {
  ipcMain.handle('thumbnail:select-folder', async () => {
    const win = BrowserWindow.getAllWindows()[0]
    if (!win) return null
    const result = await dialog.showOpenDialog(win, {
      title: '원본 촬영 폴더 선택',
      buttonLabel: '선택',
      properties: ['openDirectory']
    })
    return result.canceled ? null : result.filePaths[0] ?? null
  })

  // 폴더 정보 + 이전에 만든 결과
  ipcMain.handle('thumbnail:load', async (_event, folder: string) => {
    const data = await run(['load', folder], ['loaded'])
    return toResult(data, (d) => ({
      folder: String(d.folder),
      clipCount: Number(d.clipCount),
      totalSec: d.totalSec == null ? null : Number(d.totalSec),
      hasAnalysis: Boolean(d.hasAnalysis),
      hasStory: Boolean(d.hasStory),
      synopsis: String(d.synopsis ?? ''),
      result: d.result ?? null
    }))
  })

  // 썸네일 5장 + 제목·설명 생성. 진행 상황은 thumbnail:progress 로 보낸다.
  ipcMain.handle('thumbnail:generate', async (_event, folder: string, synopsis: string) => {
    killGenerate()
    let mine: ChildProcess | null = null
    const data = await runPythonOnce(
      'thumbnail',
      SCRIPT,
      // 시놉시스는 `--synopsis=` 한 덩어리로, 폴더는 `--` 뒤에 넘긴다 ('-' 로 시작해도 옵션으로 읽히지 않게)
      ['generate', `--synopsis=${synopsis}`, '--', folder],
      ['generated'],
      (p) => {
        if (generateProcess === mine) sendToRenderer('thumbnail:progress', { stage: p.stage, percent: p.percent, message: p.message })
      },
      (proc) => {
        mine = proc
        generateProcess = proc
      }
    )
    // 그사이 새 생성이 시작됐으면 그 등록을 지우지 않는다
    if (generateProcess === mine) generateProcess = null
    return toResult(data, (d) => d.result)
  })

  ipcMain.handle('thumbnail:cancel', async () => {
    killGenerate()
  })

  // 문구·제목·설명 수정 후 다시 합성
  ipcMain.handle('thumbnail:compose', async (_event, folder: string, id: number, patch: PythonJson) => {
    const data = await run(['compose', folder, String(id), JSON.stringify(patch)], ['composed'])
    return toResult(data, (d) => d.result)
  })

  // 하나를 고르면 selected.jpg · selected.txt 로 저장
  ipcMain.handle('thumbnail:select', async (_event, folder: string, id: number) => {
    const data = await run(['select', folder, String(id)], ['selected'])
    return toResult(data, (d) => d.result)
  })

  ipcMain.handle('thumbnail:reveal', async (_event, folder: string, name: string) => {
    shell.showItemInFolder(join(folder, 'thumbnail', name))
  })

  ipcMain.handle('thumbnail:copy-text', async (_event, text: string) => {
    clipboard.writeText(text)
  })
}

export function cleanupThumbnailProcess(): void {
  killGenerate()
}
