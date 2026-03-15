import { ipcMain, dialog } from 'electron'
import { join, dirname, basename, extname } from 'path'
import { spawn, ChildProcess } from 'child_process'
import { mkdirSync } from 'fs'
import { tmpdir } from 'os'
import { randomUUID } from 'crypto'
import { unlink } from 'fs/promises'
import { runPythonScript } from '../../services/python'
import { sendToRenderer } from '../../ipc-handlers'
import { getMainWindow } from '../../ipc-handlers'
import { resourceManager } from '../../services/resource-manager'
import { saveSrtFile, loadSrtFile, type SrtSegment } from '../../services/srt.service'

const SUBTITLE_DIR = 'subtitle'

function getSubtitleDir(filePath: string): string {
  const dir = join(dirname(filePath), SUBTITLE_DIR)
  mkdirSync(dir, { recursive: true })
  return dir
}

let transcribeProcess: ChildProcess | null = null
let ffmpegProcess: ChildProcess | null = null
let spellcheckProcess: ChildProcess | null = null
let translateProcess: ChildProcess | null = null
let translateTextProcess: ChildProcess | null = null
let youtubeProcess: ChildProcess | null = null

function extractAudio(
  videoPath: string,
  onProgress: (percent: number) => void
): Promise<{ audioPath: string }> {
  const audioPath = join(tmpdir(), `video-studio-subtitle-${randomUUID()}.wav`)
  let duration = 0

  return new Promise((resolve, reject) => {
    ffmpegProcess = spawn('ffmpeg', [
      '-i', videoPath,
      '-vn', '-acodec', 'pcm_s16le',
      '-ar', '16000', '-ac', '1',
      '-y', audioPath
    ])

    let stderrData = ''

    ffmpegProcess.stderr?.on('data', (data: Buffer) => {
      const text = data.toString()
      stderrData += text

      const durationMatch = text.match(/Duration:\s*(\d{2}):(\d{2}):(\d{2})\.(\d{2})/)
      if (durationMatch) {
        const [, h, m, s, cs] = durationMatch
        duration = parseInt(h) * 3600 + parseInt(m) * 60 + parseInt(s) + parseInt(cs) / 100
      }

      const timeMatch = text.match(/time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})/)
      if (timeMatch && duration > 0) {
        const [, h, m, s, cs] = timeMatch
        const current = parseInt(h) * 3600 + parseInt(m) * 60 + parseInt(s) + parseInt(cs) / 100
        onProgress(Math.min(100, Math.round((current / duration) * 100)))
      }
    })

    ffmpegProcess.on('close', (code) => {
      ffmpegProcess = null
      if (code === 0) {
        resolve({ audioPath })
      } else {
        unlink(audioPath).catch(() => {})
        reject(new Error(`FFmpeg 오류 (code: ${code})\n${stderrData.slice(-500)}`))
      }
    })

    ffmpegProcess.on('error', (err) => {
      ffmpegProcess = null
      unlink(audioPath).catch(() => {})
      reject(new Error(`FFmpeg 실행 실패: ${err.message}`))
    })
  })
}

export function registerSubtitleIpc(): void {
  ipcMain.handle('subtitle:select-file', async () => {
    const win = getMainWindow()
    if (!win) return null

    const result = await dialog.showOpenDialog(win, {
      title: '영상 파일 선택',
      filters: [
        {
          name: '비디오 파일',
          extensions: ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'ts', 'm4v']
        },
        { name: '모든 파일', extensions: ['*'] }
      ],
      properties: ['openFile']
    })

    return result.canceled ? null : result.filePaths[0]
  })

  ipcMain.handle('subtitle:start-process', async (_event, filePath: string, modelSize?: string, description?: string) => {
    try {
      // 1단계: 오디오 추출
      sendToRenderer('subtitle:progress', {
        stage: 'extracting',
        percent: 0,
        message: '오디오를 추출하고 있습니다...'
      })

      const { audioPath } = await extractAudio(filePath, (percent) => {
        sendToRenderer('subtitle:progress', {
          stage: 'extracting',
          percent,
          message: `오디오 추출 중... ${percent}%`
        })
      })

      // 2단계: 음성인식 (runPythonScript 사용)
      sendToRenderer('subtitle:progress', {
        stage: 'transcribing',
        percent: 0,
        message: '모델을 로딩하고 있습니다...'
      })

      const segments: Array<{
        id: number
        start: number
        end: number
        text: string
      }> = []

      await new Promise<void>((resolve, reject) => {
        const args = [
          audioPath,
          '--model-size', modelSize || 'large-v3',
          '--device', 'cpu',
          '--compute-type', 'int8'
        ]
        if (description) {
          args.push('--description', description)
        }

        transcribeProcess = runPythonScript('subtitle', 'transcribe.py', args, (data) => {
          if (data.status) {
            const status = data.status as string
            if (status === 'loading_model') {
              sendToRenderer('subtitle:progress', {
                stage: 'transcribing',
                percent: 0,
                message: `Whisper 모델 로딩 중 (${(data.duration as string) || modelSize || 'medium'})...`
              })
            } else if (status === 'detecting_speech') {
              sendToRenderer('subtitle:progress', {
                stage: 'transcribing',
                percent: -1,
                message: '음성 구간 감지 중...'
              })
            } else if (status === 'speech_detected') {
              sendToRenderer('subtitle:progress', {
                stage: 'transcribing',
                percent: 0,
                message: `음성 구간 ${data.groups}개 감지 (총 ${data.speech_duration}초)`
              })
            } else if (status === 'transcribing_chunk') {
              const chunk = (data.chunk as number) || 0
              const totalChunks = (data.total_chunks as number) || 1
              const percent = Math.round((chunk / totalChunks) * 100)
              sendToRenderer('subtitle:progress', {
                stage: 'transcribing',
                percent,
                message: `음성인식 중... (${chunk}/${totalChunks} 청크)`
              })
              sendToRenderer('subtitle:chunk-progress', {
                chunk,
                totalChunks,
                chunkStart: (data.chunk_start as number) || 0,
                chunkEnd: (data.chunk_end as number) || 0
              })
            }
          } else if (data.type === 'segment') {
            const segment = {
              id: data.id as number,
              start: data.start as number,
              end: data.end as number,
              text: data.text as string
            }
            segments.push(segment)
            sendToRenderer('subtitle:segment-added', {
              id: segment.id,
              start: segment.start,
              end: segment.end,
              text: segment.text,
              correctedText: segment.text,
              isEdited: false
            })
            sendToRenderer('subtitle:progress', {
              stage: 'transcribing',
              percent: -1,
              message: `음성인식 중... (${segments.length}개 세그먼트 완료)`
            })
          }
        })

        transcribeProcess.on('close', (code) => {
          transcribeProcess = null
          if (code === 0) {
            resolve()
          } else {
            reject(new Error(`Whisper 프로세스 오류 (code: ${code})`))
          }
        })

        transcribeProcess.on('error', (err) => {
          transcribeProcess = null
          reject(new Error(`Whisper 실행 실패: ${err.message}`))
        })
      })

      // 임시 오디오 파일 삭제
      try {
        await unlink(audioPath)
      } catch (e) {
        console.warn(`[subtitle] 임시 오디오 파일 삭제 실패: ${audioPath}`, e)
      }

      // 3단계: 맞춤법 교정
      let finalSegments: Array<{
        id: number
        start: number
        end: number
        text: string
        correctedText: string
        isEdited: boolean
      }>

      try {
        sendToRenderer('subtitle:progress', {
          stage: 'correcting',
          percent: 0,
          message: '맞춤법 교정 중... (네이버 맞춤법 검사기)'
        })

        const corrected = await new Promise<Array<{ id: number; text: string }>>((resolve, reject) => {
          spellcheckProcess = runPythonScript('subtitle', 'spellcheck.py', [], (data) => {
            const status = data.status as string
            if (status === 'progress') {
              const processed = data.processed as number
              const total = data.total as number
              const percent = Math.round((processed / total) * 100)
              sendToRenderer('subtitle:progress', {
                stage: 'correcting',
                percent,
                message: `맞춤법 교정 중... (${processed}/${total})`
              })
            } else if (status === 'done') {
              resolve((data.segments as Array<{ id: number; text: string }>) || [])
            } else if (status === 'error') {
              reject(new Error(data.message as string))
            }
          })

          const input = JSON.stringify({ segments: segments.map((s) => ({ id: s.id, text: s.text })) })
          spellcheckProcess.stdin?.write(input)
          spellcheckProcess.stdin?.end()

          spellcheckProcess.on('close', (code) => {
            spellcheckProcess = null
            if (code !== 0) {
              reject(new Error(`맞춤법 검사 프로세스 오류 (코드: ${code})`))
            }
          })

          spellcheckProcess.on('error', (err) => {
            spellcheckProcess = null
            reject(err)
          })
        })

        const correctedMap = new Map(corrected.map((c) => [c.id, c.text]))
        finalSegments = segments.map((s) => ({
          id: s.id,
          start: s.start,
          end: s.end,
          text: s.text,
          correctedText: correctedMap.get(s.id) || s.text,
          isEdited: false
        }))

        sendToRenderer('subtitle:correction-complete', finalSegments)
      } catch (err) {
        console.error('[SpellCheck] 교정 실패, 원본 유지:', err)
        finalSegments = segments.map((s) => ({
          id: s.id,
          start: s.start,
          end: s.end,
          text: s.text,
          correctedText: s.text,
          isEdited: false
        }))
      }

      // 자동 저장
      const srtPath = join(getSubtitleDir(filePath), basename(filePath, extname(filePath)) + '.srt')
      const srtSegments: SrtSegment[] = finalSegments.map((s) => ({
        id: s.id,
        start: s.start,
        end: s.end,
        text: s.correctedText
      }))
      await saveSrtFile(srtPath, srtSegments)

      // 완료
      sendToRenderer('subtitle:progress', {
        stage: 'complete',
        percent: 100,
        message: `처리가 완료되었습니다! (${srtPath})`
      })

      sendToRenderer('subtitle:complete', { segments: finalSegments, srtPath })
    } catch (err) {
      console.error('[subtitle:start-process error]', err)
      const message = err instanceof Error ? err.message : '알 수 없는 오류가 발생했습니다.'
      sendToRenderer('subtitle:error', message)
      sendToRenderer('subtitle:progress', {
        stage: 'error',
        percent: 0,
        message
      })
    }
  })

  ipcMain.handle('subtitle:cancel-process', async () => {
    if (ffmpegProcess) {
      ffmpegProcess.kill('SIGTERM')
      ffmpegProcess = null
    }
    if (transcribeProcess) {
      transcribeProcess.kill('SIGTERM')
      transcribeProcess = null
    }
    if (spellcheckProcess) {
      spellcheckProcess.kill('SIGTERM')
      spellcheckProcess = null
    }
    if (youtubeProcess) {
      youtubeProcess.kill('SIGTERM')
      youtubeProcess = null
    }
    if (translateProcess) {
      translateProcess.kill('SIGTERM')
      translateProcess = null
    }
    if (translateTextProcess) {
      translateTextProcess.kill('SIGTERM')
      translateTextProcess = null
    }
    resourceManager.release('subtitle')
  })

  ipcMain.handle(
    'subtitle:save-srt',
    async (
      _event,
      segments: Array<{ id: number; start: number; end: number; correctedText: string }>,
      filePath?: string
    ) => {
      let savePath = filePath

      if (!savePath) {
        const win = getMainWindow()
        if (!win) return null

        const result = await dialog.showSaveDialog(win, {
          title: 'SRT 파일 저장',
          defaultPath: 'subtitle.srt',
          filters: [{ name: 'SRT 자막 파일', extensions: ['srt'] }]
        })

        if (result.canceled || !result.filePath) return null
        savePath = result.filePath
      }

      const srtSegments: SrtSegment[] = segments.map((s) => ({
        id: s.id,
        start: s.start,
        end: s.end,
        text: s.correctedText
      }))

      await saveSrtFile(savePath, srtSegments)
      return savePath
    }
  )

  ipcMain.handle('subtitle:select-srt-file', async () => {
    const win = getMainWindow()
    if (!win) return null

    const result = await dialog.showOpenDialog(win, {
      title: 'SRT 자막 파일 선택',
      filters: [
        { name: 'SRT 자막 파일', extensions: ['srt'] },
        { name: '모든 파일', extensions: ['*'] }
      ],
      properties: ['openFile']
    })

    if (result.canceled) return null

    const srtPath = result.filePaths[0]
    const segments = await loadSrtFile(srtPath)
    return {
      srtPath,
      segments: segments.map((s) => ({
        id: s.id,
        start: s.start,
        end: s.end,
        text: s.text,
        correctedText: s.text
      }))
    }
  })

  ipcMain.handle('subtitle:get-youtube-info', async (_event, url: string) => {
    try {
      return new Promise((resolve) => {
        youtubeProcess = runPythonScript('subtitle', 'download.py', [url, '--info-only'], (data) => {
          if ((data.status as string) === 'info') {
            resolve({
              title: data.title as string,
              duration: data.duration as number,
              description: (data.description as string) || ''
            })
          }
        })

        youtubeProcess.on('close', () => {
          youtubeProcess = null
          // If we haven't resolved yet, resolve with null
          resolve(null)
        })
        youtubeProcess.on('error', () => {
          youtubeProcess = null
          resolve(null)
        })
      })
    } catch {
      return null
    }
  })

  ipcMain.handle('subtitle:start-youtube-download', async (_event, url: string, defaultName: string) => {
    const win = getMainWindow()
    if (!win) return null

    const result = await dialog.showSaveDialog(win, {
      title: '영상 저장 위치 선택',
      defaultPath: defaultName + '.mp4',
      filters: [{ name: '비디오 파일', extensions: ['mp4'] }]
    })

    if (result.canceled || !result.filePath) return null
    const savePath = result.filePath

    try {
      sendToRenderer('subtitle:progress', {
        stage: 'downloading',
        percent: 0,
        message: '다운로드 시작...'
      })

      await new Promise<void>((resolve, reject) => {
        youtubeProcess = runPythonScript('subtitle', 'download.py', [url, savePath], (data) => {
          const status = data.status as string
          if (status === 'info') {
            sendToRenderer('subtitle:progress', {
              stage: 'downloading',
              percent: 0,
              message: `"${data.title}" 다운로드 중...`
            })
          } else if (status === 'downloading') {
            sendToRenderer('subtitle:progress', {
              stage: 'downloading',
              percent: (data.percent as number) ?? -1,
              message: `다운로드 중... ${(data.percent as number) ?? 0}%`
            })
          } else if (status === 'processing') {
            sendToRenderer('subtitle:progress', {
              stage: 'downloading',
              percent: -1,
              message: '영상 변환 중...'
            })
          } else if (status === 'error') {
            reject(new Error(data.message as string))
          }
        })

        youtubeProcess.on('close', (code) => {
          youtubeProcess = null
          if (code === 0) {
            resolve()
          } else {
            reject(new Error(`다운로드 실패 (code: ${code})`))
          }
        })

        youtubeProcess.on('error', (err) => {
          youtubeProcess = null
          reject(err)
        })
      })

      sendToRenderer('subtitle:download-complete', savePath)
    } catch (err) {
      const message = err instanceof Error ? err.message : '다운로드 실패'
      sendToRenderer('subtitle:error', message)
      sendToRenderer('subtitle:progress', {
        stage: 'error',
        percent: 0,
        message
      })
    }
  })

  ipcMain.handle(
    'subtitle:translate-subtitles',
    async (
      _event,
      segments: Array<{ id: number; start: number; end: number; text: string }>,
      lang: string,
      baseSrtPath: string,
      description?: string,
      aiEngine?: string
    ) => {
      // 이전 번역 프로세스가 있으면 종료
      if (translateProcess) {
        translateProcess.kill('SIGTERM')
        translateProcess = null
      }

      const useClaude = aiEngine === 'claude'

      if (!useClaude) {
        const check = resourceManager.acquire('subtitle', ['ollama'])
        if (check.conflict) {
          sendToRenderer('subtitle:translate-progress', { percent: -1, message: check.message || '리소스 충돌' })
          return null
        }
      }

      try {
        const langLabel = lang === 'en' ? '영어' : '일본어'
        const engineLabel = useClaude ? 'Claude' : 'Ollama'
        sendToRenderer('subtitle:translate-progress', { percent: 0, message: `${langLabel} 번역 중... (${engineLabel})` })

        const scriptName = useClaude ? 'translate_claude.py' : 'translate.py'
        const translated = await new Promise<Array<{ id: number; text: string }>>((resolve, reject) => {
          translateProcess = runPythonScript('subtitle', scriptName, [], (data) => {
            const status = data.status as string
            if (status === 'progress') {
              const processed = data.processed as number
              const total = data.total as number
              const percent = Math.round((processed / total) * 100)
              sendToRenderer('subtitle:translate-progress', {
                percent,
                message: `${langLabel} 번역 중... (${processed}/${total})`
              })
            } else if (status === 'done') {
              resolve((data.segments as Array<{ id: number; text: string }>) || [])
            } else if (status === 'error') {
              reject(new Error(data.message as string))
            }
          })

          const input = JSON.stringify({
            lang,
            description: description || '',
            segments: segments.map((s) => ({ id: s.id, text: s.text }))
          })
          translateProcess.stdin?.write(input)
          translateProcess.stdin?.end()

          translateProcess.on('close', (code) => {
            translateProcess = null
            if (code !== 0) {
              reject(new Error(`번역 프로세스 오류 (코드: ${code})`))
            }
          })

          translateProcess.on('error', (err) => {
            translateProcess = null
            reject(err)
          })
        })

        const translatedMap = new Map(translated.map((t) => [t.id, t.text]))
        const srtSegments: SrtSegment[] = segments.map((s) => ({
          id: s.id,
          start: s.start,
          end: s.end,
          text: translatedMap.get(s.id) || s.text
        }))

        const suffix = lang === 'en' ? '_en' : '_jp'
        const savePath = baseSrtPath.replace(/\.srt$/, `${suffix}.srt`)
        await saveSrtFile(savePath, srtSegments)

        sendToRenderer('subtitle:translate-complete', { lang, segments: srtSegments })
        sendToRenderer('subtitle:translate-progress', { percent: 100, message: `${langLabel} 번역 완료! (${savePath})` })

        return savePath
      } catch (err) {
        const message = err instanceof Error ? err.message : '번역 실패'
        sendToRenderer('subtitle:translate-progress', { percent: -1, message: `번역 실패: ${message}` })
        return null
      } finally {
        if (!useClaude) {
          resourceManager.release('subtitle')
        }
      }
    }
  )

  ipcMain.handle(
    'subtitle:translate-description',
    async (
      _event,
      title: string,
      description: string,
      lang: string
    ) => {
      try {
        return await new Promise<{ title: string; description: string } | null>((resolve, reject) => {
          translateTextProcess = runPythonScript('subtitle', 'translate_text.py', [], (data) => {
            const status = data.status as string
            if (status === 'done') {
              resolve({ title: data.title as string, description: data.description as string })
            } else if (status === 'error') {
              reject(new Error(data.message as string))
            }
          })

          const input = JSON.stringify({ title, description, lang })
          translateTextProcess.stdin?.write(input)
          translateTextProcess.stdin?.end()

          translateTextProcess.on('close', (code) => {
            translateTextProcess = null
            if (code !== 0) {
              resolve(null)
            }
          })

          translateTextProcess.on('error', () => {
            translateTextProcess = null
            resolve(null)
          })
        })
      } catch (err) {
        console.error('[subtitle:translate-description error]', err)
        return null
      }
    }
  )
}

export function cleanupSubtitleProcess(): void {
  if (ffmpegProcess) {
    ffmpegProcess.kill('SIGTERM')
    ffmpegProcess = null
  }
  if (transcribeProcess) {
    transcribeProcess.kill('SIGTERM')
    transcribeProcess = null
  }
  if (spellcheckProcess) {
    spellcheckProcess.kill('SIGTERM')
    spellcheckProcess = null
  }
  if (translateProcess) {
    translateProcess.kill('SIGTERM')
    translateProcess = null
  }
  if (translateTextProcess) {
    translateTextProcess.kill('SIGTERM')
    translateTextProcess = null
  }
  if (youtubeProcess) {
    youtubeProcess.kill('SIGTERM')
    youtubeProcess = null
  }
  resourceManager.release('subtitle')
}
