import { ipcMain, dialog, shell, BrowserWindow } from 'electron'
import { ChildProcess } from 'child_process'
import { runPythonScript } from '../../services/python'
import { sendToRenderer } from '../../ipc-handlers'

// 지금 돌고 있는 곡 추천 (취소할 수 있는 긴 작업은 이것 하나)
let recommendProcess: ChildProcess | null = null

/**
 * Python 은 detached(자기 프로세스 그룹)로 뜬다. 그룹 전체에 보내야 Python 이 띄운 `claude -p` 까지 멈춘다
 * (Python 에만 보내면 Claude 호출이 끝까지 돌며 비용이 나간다).
 */
function killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals = 'SIGTERM'): void {
  if (proc.pid) {
    try {
      process.kill(-proc.pid, signal)
      return
    } catch { /* 그룹 kill 실패 시 단일 프로세스 kill 시도 */ }
  }
  try { proc.kill(signal) } catch { /* ignore */ }
}

function killRecommend(): void {
  if (recommendProcess) {
    killProcessGroup(recommendProcess)
    recommendProcess = null
  }
}

type Json = Record<string, unknown>

/**
 * Python 스크립트를 한 번 실행하고, doneTypes 중 하나가 나오면 그 메시지로 끝낸다.
 * progress 메시지는 onProgress 로 넘기고, error 메시지·비정상 종료는 { type: 'error' } 로 돌려준다.
 */
function runOnce(
  script: string,
  args: string[],
  doneTypes: string[],
  onProgress?: (data: Json) => void,
  track?: (proc: ChildProcess) => void
): Promise<Json> {
  return new Promise((resolve) => {
    let result: Json | null = null
    let stderrText = ''
    const proc = runPythonScript(
      'bgm',
      script,
      args,
      (data) => {
        if (data.type === 'progress') onProgress?.(data)
        else if (doneTypes.includes(String(data.type)) || data.type === 'error') result = data
      },
      (stderr) => {
        stderrText += stderr + '\n'
      }
    )
    track?.(proc)
    proc.on('close', (code, signal) => {
      if (result) resolve(result)
      else if (signal) resolve({ type: 'error', message: '취소되었습니다' })
      else {
        // stderr 에는 경고도 섞여 나오므로 마지막 몇 줄만 보여 준다
        const tail = stderrText.trim().split('\n').slice(-6).join('\n')
        resolve({ type: 'error', message: tail || `프로세스가 비정상 종료되었습니다 (code: ${code})` })
      }
    })
    proc.on('error', (err) => {
      if (!result) resolve({ type: 'error', message: err.message })
    })
  })
}

// ── 음악 라이브러리 ──

export type LibraryOpenResult =
  | { status: 'ok'; info: Json; tracks: Json[] }
  | { status: 'not_library'; root: string; empty: boolean }
  | { status: 'canceled' }
  | { status: 'error'; message: string }

async function runLibraryCommand(command: 'open' | 'init' | 'set-audio', root: string, ...extra: string[]): Promise<LibraryOpenResult> {
  const data = await runOnce('music_library.py', [command, root, ...extra], ['library', 'not_library'])
  if (data.type === 'library') return { status: 'ok', info: data.info as Json, tracks: data.tracks as Json[] }
  if (data.type === 'not_library') return { status: 'not_library', root: String(data.root), empty: Boolean(data.empty) }
  return { status: 'error', message: String(data.message) }
}

async function openOrCreateLibrary(win: BrowserWindow, root: string): Promise<LibraryOpenResult> {
  const opened = await runLibraryCommand('open', root)
  if (opened.status !== 'not_library') return opened

  const { response } = await dialog.showMessageBox(win, {
    type: 'question',
    buttons: ['새 라이브러리 만들기', '취소'],
    defaultId: 0,
    cancelId: 1,
    message: '이 폴더에는 음악 라이브러리가 없습니다.',
    detail: opened.empty
      ? `새 라이브러리를 만듭니다.\n${root}`
      : `폴더에 다른 파일이 있습니다. 기존 파일은 그대로 두고 라이브러리 파일(library.json, catalog.sqlite, audio/, usage/, sources/)만 추가합니다.\n${root}`
  })
  if (response !== 0) return { status: 'canceled' }

  return runLibraryCommand('init', root)
}

// ── 추천 · 큐 · 내보내기 결과 ──

export type BgmResult<T> = { ok: true; data: T } | { ok: false; message: string }

function toResult<T>(data: Json, pick: (d: Json) => T): BgmResult<T> {
  if (data.type === 'error') return { ok: false, message: String(data.message) }
  return { ok: true, data: pick(data) }
}

export function registerBgmIpc(): void {
  ipcMain.handle('bgm:library-select', async (): Promise<LibraryOpenResult> => {
    const win = BrowserWindow.getAllWindows()[0]
    if (!win) return { status: 'canceled' }

    const result = await dialog.showOpenDialog(win, {
      title: '음악 라이브러리 폴더 선택',
      buttonLabel: '선택',
      properties: ['openDirectory', 'createDirectory']
    })
    if (result.canceled || !result.filePaths[0]) return { status: 'canceled' }

    return openOrCreateLibrary(win, result.filePaths[0])
  })

  ipcMain.handle('bgm:library-open', async (_event, root: string): Promise<LibraryOpenResult> => {
    return runLibraryCommand('open', root)
  })

  // 음원 폴더 바꾸기 (검색 데이터는 라이브러리 폴더에 두고 음원만 NAS 등에 둘 때)
  ipcMain.handle('bgm:library-audio-dir', async (_event, root: string): Promise<LibraryOpenResult> => {
    const win = BrowserWindow.getAllWindows()[0]
    if (!win) return { status: 'canceled' }

    const result = await dialog.showOpenDialog(win, {
      title: '음원 폴더 선택 (mp3 가 들어 있는 audio 폴더 또는 그 위 라이브러리 사본 폴더)',
      buttonLabel: '선택',
      properties: ['openDirectory']
    })
    if (result.canceled || !result.filePaths[0]) return { status: 'canceled' }

    return runLibraryCommand('set-audio', root, result.filePaths[0])
  })

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

  // 구간에 어울리는 곡 추천 (Claude 장면 분석 → 라이브러리 검색). 진행 상황은 bgm:progress 로 보낸다.
  ipcMain.handle(
    'bgm:recommend',
    async (_event, root: string, video: string, start: number, end: number, preference: string, usedTrackIds: string[] = []) => {
      killRecommend()
      let mine: ChildProcess | null = null
      const data = await runOnce(
        'recommend.py',
        // 옵션을 앞에 두고 `--` 뒤에 위치 인수를 넘긴다 (음악 느낌이 '-' 로 시작해도 옵션으로 읽히지 않게)
        ['--used', usedTrackIds.join(','), '--', root, video, String(start), String(end), preference || ''],
        ['recommended'],
        (p) => {
          if (recommendProcess === mine) sendToRenderer('bgm:progress', { stage: p.stage, percent: p.percent, message: p.message })
        },
        (proc) => {
          mine = proc
          recommendProcess = proc
        }
      )
      // 그사이 새 추천이 시작됐으면 그 등록을 지우지 않는다
      if (recommendProcess === mine) recommendProcess = null
      return toResult(data, (d) => ({ criteria: d.criteria, tracks: d.tracks, durationSec: d.durationSec }))
    }
  )

  ipcMain.handle('bgm:cancel', async () => {
    killRecommend()
  })

  ipcMain.handle('bgm:cues', async (_event, root: string, video: string) => {
    const data = await runOnce('bgm_export.py', ['cues', root, video], ['cues'])
    return toResult(data, (d) => d.cues)
  })

  ipcMain.handle('bgm:add-cue', async (_event, root: string, video: string, cue: Json) => {
    const data = await runOnce('bgm_export.py', ['add', root, video, JSON.stringify(cue)], ['cues'])
    return toResult(data, (d) => d.cues)
  })

  ipcMain.handle('bgm:remove-cue', async (_event, root: string, video: string, cueId: string) => {
    const data = await runOnce('bgm_export.py', ['remove', root, video, cueId], ['cues'])
    return toResult(data, (d) => d.cues)
  })

  ipcMain.handle('bgm:export', async (_event, root: string, video: string) => {
    const data = await runOnce('bgm_export.py', ['export', root, video], ['exported'])
    return toResult(data, (d) => ({
      fcpxmlPath: String(d.fcpxmlPath),
      creditsPath: String(d.creditsPath),
      credits: String(d.credits)
    }))
  })

  ipcMain.handle('bgm:reveal', async (_event, path: string) => {
    shell.showItemInFolder(path)
  })
}

export function cleanupBgmProcess(): void {
  killRecommend()
}
