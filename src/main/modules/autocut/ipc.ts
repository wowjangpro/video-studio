import { ipcMain, dialog } from 'electron'
import { ChildProcess } from 'child_process'
import { readdirSync, readFileSync, writeFileSync, statSync, existsSync, unlinkSync, mkdirSync } from 'fs'
import { join, extname, dirname, basename } from 'path'
import { runPythonScript } from '../../services/python'
import { sendToRenderer } from '../../ipc-handlers'
import { getVideoDuration, generateThumbnailTo, extractWaveformPeaks, generateThumbnail } from '../../services/ffmpeg'
import { resourceManager } from '../../services/resource-manager'

const VIDEO_EXTS = ['.mp4', '.mov', '.mkv', '.avi', '.m4v', '.webm', '.ts']

let analysisProcess: ChildProcess | null = null

/** 프로세스 그룹 전체에 시그널 전송 (claude 서브프로세스 포함) */
function killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals = 'SIGTERM'): void {
  if (proc.pid) {
    try {
      process.kill(-proc.pid, signal)
      return
    } catch { /* 그룹 kill 실패 시 단일 프로세스 kill 시도 */ }
  }
  try { proc.kill(signal) } catch { /* ignore */ }
}

/** 앱 종료 시 분석 프로세스 정리 */
export function cleanupAutocutProcess(): void {
  if (analysisProcess) {
    killProcessGroup(analysisProcess)
    analysisProcess = null
  }
}

interface FileInfo {
  path: string
  name: string
  duration: number
  cumulativeOffset: number
  thumbnailUrl: string | null
}

const AUTOCUT_DIR = 'autocut'
const CACHE_FILENAME = 'cache.json'
const THUMB_DIR = 'thumbs'

interface CacheEntry {
  mtime: number
  size: number
  duration: number
  thumbFile: string
}

interface ScanCache {
  version: number
  files: Record<string, CacheEntry>
}

function getAutocutDir(folderPath: string): string {
  const dir = join(folderPath, AUTOCUT_DIR)
  mkdirSync(dir, { recursive: true })
  return dir
}

function loadScanCache(folderPath: string): ScanCache {
  const cachePath = join(getAutocutDir(folderPath), CACHE_FILENAME)
  if (!existsSync(cachePath)) return { version: 1, files: {} }
  try {
    const data = JSON.parse(readFileSync(cachePath, 'utf-8'))
    if (data.version === 1) return data
  } catch { /* corrupt cache, start fresh */ }
  return { version: 1, files: {} }
}

function saveScanCache(folderPath: string, cache: ScanCache): void {
  writeFileSync(join(getAutocutDir(folderPath), CACHE_FILENAME), JSON.stringify(cache), 'utf-8')
}

async function scanVideoFiles(folderPath: string): Promise<FileInfo[]> {
  const entries = readdirSync(folderPath)
    .filter((name) => {
      const ext = extname(name).toLowerCase()
      return VIDEO_EXTS.includes(ext) && !name.startsWith('.')
    })
    .sort()

  console.log(`[autocut:scan] ${folderPath} → ${entries.length}개 영상 파일 발견`)
  const startTime = Date.now()

  const cache = loadScanCache(folderPath)
  const autocutDir = getAutocutDir(folderPath)
  const thumbDir = join(autocutDir, THUMB_DIR)
  let cacheHits = 0

  const results = await Promise.all(
    entries.map(async (name) => {
      const filePath = join(folderPath, name)
      const stat = statSync(filePath)
      const cached = cache.files[name]

      // 캐시 히트: mtime + size 일치 + 썸네일 파일 존재
      if (
        cached &&
        cached.mtime === stat.mtimeMs &&
        cached.size === stat.size &&
        cached.thumbFile &&
        existsSync(join(thumbDir, cached.thumbFile))
      ) {
        cacheHits++
        return {
          path: filePath,
          name,
          duration: cached.duration,
          thumbnailUrl: 'file://' + join(thumbDir, cached.thumbFile)
        }
      }

      // 캐시 미스: ffprobe + ffmpeg 실행
      mkdirSync(thumbDir, { recursive: true })
      const thumbFile = name.replace(/\.[^.]+$/, '.jpg')
      const thumbPath = join(thumbDir, thumbFile)
      const [duration, thumbnailUrl] = await Promise.all([
        getVideoDuration(filePath),
        generateThumbnailTo(filePath, thumbPath)
      ])

      cache.files[name] = {
        mtime: stat.mtimeMs,
        size: stat.size,
        duration,
        thumbFile: thumbnailUrl ? thumbFile : ''
      }

      return { path: filePath, name, duration, thumbnailUrl }
    })
  )

  saveScanCache(folderPath, cache)

  // 누적 오프셋 계산 (순서 보장)
  const files: FileInfo[] = []
  let cumulative = 0
  for (const r of results) {
    files.push({
      path: r.path,
      name: r.name,
      duration: r.duration,
      cumulativeOffset: cumulative,
      thumbnailUrl: r.thumbnailUrl
    })
    cumulative += r.duration
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1)
  console.log(`[autocut:scan] 완료: ${files.length}개 파일 (캐시 ${cacheHits}/${entries.length}), 총 ${cumulative.toFixed(1)}초, 스캔 ${elapsed}s 소요`)

  return files
}

const PROGRESS_FILENAME = 'progress.jsonl'
const ANALYSIS_CACHE_FILENAME = 'analysis.json'

interface ResumeInfo {
  canResume: boolean
  completedFiles: number
  totalFiles: number
  savedOptions: { score_threshold?: number; window_duration: number }
  hasAnalysisCache?: boolean
}

function checkResumeInfo(folderPath: string, files: FileInfo[]): ResumeInfo | null {
  const autocutDir = getAutocutDir(folderPath)
  const progressPath = join(autocutDir, PROGRESS_FILENAME)
  const analysisCachePath = join(autocutDir, ANALYSIS_CACHE_FILENAME)
  const hasAnalysisCache = existsSync(analysisCachePath)

  if (hasAnalysisCache) {
    console.log(`[autocut:resume] 분석 캐시 발견: ${analysisCachePath}`)
  }

  if (!existsSync(progressPath)) {
    if (hasAnalysisCache) {
      return {
        canResume: false,
        completedFiles: files.length,
        totalFiles: files.length,
        savedOptions: { window_duration: 10 },
        hasAnalysisCache: true
      }
    }
    return null
  }

  try {
    const content = readFileSync(progressPath, 'utf-8')
    const lines = content.split('\n').filter((l) => l.trim())
    if (lines.length === 0) {
      if (hasAnalysisCache) {
        return {
          canResume: false,
          completedFiles: files.length,
          totalFiles: files.length,
          savedOptions: { window_duration: 10 },
          hasAnalysisCache: true
        }
      }
      return null
    }

    // 첫 줄: meta
    const meta = JSON.parse(lines[0])
    if (meta.type !== 'meta') {
      if (hasAnalysisCache) {
        return {
          canResume: false,
          completedFiles: files.length,
          totalFiles: files.length,
          savedOptions: { window_duration: 10 },
          hasAnalysisCache: true
        }
      }
      return null
    }

    const savedFiles = meta.files as { name: string; duration: number }[]
    const savedOptions = meta.options as { score_threshold?: number; window_duration: number }

    // 파일 목록 검증
    let progressValid = true
    if (savedFiles.length !== files.length) {
      progressValid = false
    } else {
      for (let i = 0; i < savedFiles.length; i++) {
        if (savedFiles[i].name !== files[i].name || Math.abs(savedFiles[i].duration - files[i].duration) > 0.5) {
          progressValid = false
          break
        }
      }
    }
    if (!progressValid) {
      if (hasAnalysisCache) {
        return {
          canResume: false,
          completedFiles: files.length,
          totalFiles: files.length,
          savedOptions,
          hasAnalysisCache: true
        }
      }
      return null
    }

    // 완료된 파일 수 카운트
    let completedFiles = 0
    for (const line of lines) {
      try {
        const data = JSON.parse(line)
        if (data.type === 'file_complete') completedFiles++
      } catch { /* skip */ }
    }

    console.log(`[autocut:resume] 진행 파일 감지: ${completedFiles}/${files.length} 파일 완료`)
    return {
      canResume: true,
      completedFiles,
      totalFiles: files.length,
      savedOptions,
      hasAnalysisCache
    }
  } catch (e) {
    console.log(`[autocut:resume] 진행 파일 파싱 실패: ${e}`)
    return null
  }
}

export function registerAutocutIpc(): void {
  ipcMain.handle('autocut:select-folder', async () => {
    console.log('[autocut:ipc] select-folder 호출')
    const { BrowserWindow } = await import('electron')
    const win = BrowserWindow.getAllWindows()[0]
    if (!win) return null
    const result = await dialog.showOpenDialog(win, {
      title: '캠핑 영상 폴더 선택',
      properties: ['openDirectory']
    })
    if (result.canceled || result.filePaths.length === 0) {
      console.log('[autocut:ipc] select-folder: 취소됨')
      return null
    }
    const folderPath = result.filePaths[0]
    console.log(`[autocut:ipc] select-folder: ${folderPath}`)
    const files = await scanVideoFiles(folderPath)
    const resumeInfo = checkResumeInfo(folderPath, files)
    return { folderPath, files, resumeInfo }
  })

  ipcMain.handle('autocut:scan-folder', async (_event, folderPath: string) => {
    console.log(`[autocut:ipc] scan-folder: ${folderPath}`)
    try {
      const stat = statSync(folderPath)
      if (!stat.isDirectory()) {
        console.log('[autocut:ipc] scan-folder: 디렉토리가 아님')
        return null
      }
    } catch {
      console.log('[autocut:ipc] scan-folder: 경로 접근 실패')
      return null
    }
    const files = await scanVideoFiles(folderPath)
    const resumeInfo = checkResumeInfo(folderPath, files)
    return { folderPath, files, resumeInfo }
  })

  ipcMain.handle('autocut:get-thumbnail', async (_event, filePath: string) => {
    console.log(`[autocut:ipc] get-thumbnail: ${filePath.split('/').pop()}`)
    return await generateThumbnail(filePath)
  })

  ipcMain.handle('autocut:get-waveform', async (_event, filePath: string) => {
    const dir = dirname(filePath)
    const name = basename(filePath)
    const thumbDir = join(getAutocutDir(dir), THUMB_DIR)
    const peaksPath = join(thumbDir, name.replace(/\.[^.]+$/, '.peaks.json'))

    if (existsSync(peaksPath)) {
      const videoMtime = statSync(filePath).mtimeMs
      const peaksMtime = statSync(peaksPath).mtimeMs
      if (peaksMtime >= videoMtime) {
        console.log(`[autocut:ipc] get-waveform (캐시): ${name}`)
        return JSON.parse(readFileSync(peaksPath, 'utf-8'))
      }
    }

    console.log(`[autocut:ipc] get-waveform (추출): ${name}`)
    mkdirSync(thumbDir, { recursive: true })
    const peaks = await extractWaveformPeaks(filePath)
    writeFileSync(peaksPath, JSON.stringify(peaks))
    return peaks
  })

  ipcMain.handle(
    'autocut:start-analysis',
    async (_event, folderPath: string, options: Record<string, unknown>) => {
      console.log(`[autocut:ipc] start-analysis: ${folderPath}`)
      console.log(`[autocut:ipc] options:`, JSON.stringify(options))

      if (analysisProcess) {
        console.log('[autocut:ipc] 기존 분석 프로세스 종료')
        killProcessGroup(analysisProcess)
        analysisProcess = null
      }

      // 리소스 점유 요청
      const usesClaude = options.ai_engine === 'claude'
      const resourceResult = resourceManager.acquire('autocut', ['ollama'], { usesClaude })
      if (resourceResult.conflict) {
        sendToRenderer('autocut:error', resourceResult.message || '리소스 충돌')
        return
      }

      const optionsJson = JSON.stringify(options)
      let errorSent = false

      analysisProcess = runPythonScript(
        'autocut',
        'analyze.py',
        [folderPath, optionsJson],
        (data) => {
          const type = data.type as string
          switch (type) {
            case 'progress':
              sendToRenderer('autocut:progress', {
                stage: data.stage,
                percent: data.percent,
                message: data.message
              })
              // LLM 오류로 Python이 자체 일시정지(SIGSTOP)한 경우 → UI도 일시정지 표시
              if (data.llm_error) {
                console.log('[autocut] LLM 오류 감지 — Python 자체 일시정지됨')
                sendToRenderer('autocut:paused')
              }
              break
            case 'window_result':
              console.log(`[autocut] window_result: ${data.decision} [${data.label}] score=${data.score} (${data.globalStart}~${data.globalEnd})`)
              sendToRenderer('autocut:window-result', data)
              break
            case 'file_complete':
              console.log(`[autocut] file_complete: #${data.fileIndex} keep=${data.keepCount} drop=${data.dropCount}`)
              sendToRenderer('autocut:file-complete', {
                fileIndex: data.fileIndex,
                filePath: data.filePath,
                keepCount: data.keepCount,
                dropCount: data.dropCount
              })
              break
            case 'complete':
              console.log(`[autocut] complete: ${data.totalKeep}개 KEEP 세그먼트`)
              sendToRenderer('autocut:complete', {
                keepSegments: data.keepSegments,
                srtPath: data.srtPath,
                totalKeep: data.totalKeep,
                totalDuration: data.totalDuration
              })
              analysisProcess = null
              resourceManager.release('autocut')
              break
            case 'error':
              console.error(`[autocut] error: ${data.message}`)
              if (!errorSent) {
                sendToRenderer('autocut:error', data.message)
                errorSent = true
              }
              analysisProcess = null
              resourceManager.release('autocut')
              break
          }
        },
        (stderr) => {
          // Python 모듈 디버그 로그는 콘솔에만 출력
          if (/^\[(?:analyze|claude|stage[12]|storyboard|stt|scene_detector|vad|merger)\]/.test(stderr)) {
            console.log(`[autocut:python:debug] ${stderr}`)
            return
          }
          const lower = stderr.toLowerCase()
          if (
            !errorSent &&
            (lower.includes('traceback') ||
              (lower.includes('exception') && !lower.includes('timeout')) ||
              (lower.includes('error') && !lower.includes('userwarning') && !lower.includes('retry')))
          ) {
            console.error(`[autocut:stderr] ${stderr.slice(0, 500)}`)
            sendToRenderer('autocut:error', stderr)
            errorSent = true
          } else {
            console.log(`[autocut:python:stderr] ${stderr.slice(0, 300)}`)
          }
        }
      )

      analysisProcess.on('close', (code) => {
        console.log(`[autocut] process closed, code=${code}`)
        const wasActive = analysisProcess !== null
        if (code !== 0 && !errorSent && wasActive) {
          sendToRenderer('autocut:error', `분석 프로세스가 종료되었습니다 (코드: ${code})`)
          errorSent = true
        }
        analysisProcess = null
        if (wasActive) {
          resourceManager.release('autocut')
        }
      })
    }
  )

  ipcMain.handle('autocut:pause-analysis', async () => {
    console.log('[autocut:ipc] pause-analysis')
    if (analysisProcess && analysisProcess.pid) {
      killProcessGroup(analysisProcess, 'SIGSTOP')
      sendToRenderer('autocut:paused')
    }
  })

  ipcMain.handle('autocut:resume-analysis', async () => {
    console.log('[autocut:ipc] resume-analysis')
    if (analysisProcess && analysisProcess.pid) {
      killProcessGroup(analysisProcess, 'SIGCONT')
      sendToRenderer('autocut:resumed')
    }
  })

  ipcMain.handle('autocut:cancel-analysis', async () => {
    console.log('[autocut:ipc] cancel-analysis')
    if (analysisProcess) {
      const proc = analysisProcess
      analysisProcess = null
      // 일시정지 상태일 수 있으므로 먼저 SIGCONT → 500ms 후 SIGKILL
      killProcessGroup(proc, 'SIGCONT')
      setTimeout(() => killProcessGroup(proc, 'SIGKILL'), 500)
      sendToRenderer('autocut:cancelled')
      resourceManager.release('autocut')
    }
  })

  ipcMain.handle('autocut:delete-progress', async (_event, folderPath: string) => {
    const autocutDir = getAutocutDir(folderPath)
    const progressPath = join(autocutDir, PROGRESS_FILENAME)
    const analysisCachePath = join(autocutDir, ANALYSIS_CACHE_FILENAME)
    console.log(`[autocut:ipc] delete-progress: ${progressPath}`)
    if (existsSync(progressPath)) {
      unlinkSync(progressPath)
      console.log('[autocut:ipc] delete-progress: 진행 파일 삭제')
    }
    if (existsSync(analysisCachePath)) {
      unlinkSync(analysisCachePath)
      console.log('[autocut:ipc] delete-progress: 분석 캐시 삭제')
    }
  })

  ipcMain.handle('autocut:load-srt', async (_event, srtFilePath?: string) => {
    let srtPath: string

    if (srtFilePath) {
      console.log(`[autocut:ipc] load-srt (직접): ${srtFilePath}`)
      srtPath = srtFilePath
    } else {
      console.log('[autocut:ipc] load-srt 호출')
      const { BrowserWindow } = await import('electron')
      const win = BrowserWindow.getAllWindows()[0]
      if (!win) return null
      const result = await dialog.showOpenDialog(win, {
        title: 'SRT 파일 불러오기',
        filters: [{ name: 'SRT', extensions: ['srt'] }],
        properties: ['openFile']
      })
      if (result.canceled || result.filePaths.length === 0) {
        console.log('[autocut:ipc] load-srt: 취소됨')
        return null
      }
      srtPath = result.filePaths[0]
    }

    if (!existsSync(srtPath)) {
      console.log(`[autocut:ipc] load-srt: 파일 없음 ${srtPath}`)
      return null
    }
    console.log(`[autocut:ipc] load-srt: ${srtPath}`)
    const content = readFileSync(srtPath, 'utf-8')
    const segments = parseSrtFile(content)
    console.log(`[autocut:ipc] load-srt: ${segments.length}개 세그먼트 파싱 완료`)
    return { srtPath, segments }
  })

  ipcMain.handle(
    'autocut:save-srt',
    async (_event, segments: { globalStart: number; globalEnd: number; label: string; score: number | string }[], filePath?: string) => {
      console.log(`[autocut:ipc] save-srt: ${segments.length}개 세그먼트`)
      if (!filePath) {
        const { BrowserWindow } = await import('electron')
        const win = BrowserWindow.getAllWindows()[0]
        if (!win) return null
        const result = await dialog.showSaveDialog(win, {
          title: 'SRT 파일 저장',
          defaultPath: 'autocut_guide.srt',
          filters: [{ name: 'SRT', extensions: ['srt'] }]
        })
        if (result.canceled || !result.filePath) return null
        filePath = result.filePath
      }

      const lines: string[] = []
      for (let i = 0; i < segments.length; i++) {
        const seg = segments[i]
        const start = formatSrtTime(seg.globalStart)
        const end = formatSrtTime(seg.globalEnd)
        lines.push(`${i + 1}`)
        lines.push(`${start} --> ${end}`)
        lines.push(`[${seg.label}] score:${seg.score}`)
        lines.push('')
      }

      writeFileSync(filePath, lines.join('\n'), 'utf-8')
      console.log(`[autocut:ipc] save-srt: 저장 완료 → ${filePath}`)
      return filePath
    }
  )
}

function parseSrtTime(str: string): number {
  const [hms, msStr] = str.split(',')
  const [h, m, s] = hms.split(':').map(Number)
  return h * 3600 + m * 60 + s + (parseInt(msStr, 10) || 0) / 1000
}

function parseSrtFile(content: string): { globalStart: number; globalEnd: number; label: string; score: number | string }[] {
  const segments: { globalStart: number; globalEnd: number; label: string; score: number | string }[] = []
  const blocks = content.trim().split(/\n\s*\n/)

  for (const block of blocks) {
    const lines = block.trim().split('\n')
    if (lines.length < 3) continue

    const timeMatch = lines[1].match(/(\d+:\d{2}:\d{2},\d{3})\s*-->\s*(\d+:\d{2}:\d{2},\d{3})/)
    if (!timeMatch) continue

    const globalStart = parseSrtTime(timeMatch[1])
    const globalEnd = parseSrtTime(timeMatch[2])

    const text = lines.slice(2).join(' ')
    const labelMatch = text.match(/\[([^\]]*)\]/)
    const scoreMatch = text.match(/score:(\S+)/)
    const label = labelMatch ? labelMatch[1] : ''
    let score: number | string = scoreMatch ? scoreMatch[1] : 0
    if (score !== 'VAD' && !isNaN(Number(score))) {
      score = Number(score)
    }

    segments.push({ globalStart, globalEnd, label, score })
  }

  return segments
}

function formatSrtTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 1000)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`
}
