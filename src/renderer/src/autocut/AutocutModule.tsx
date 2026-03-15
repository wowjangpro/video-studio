import { useEffect, useRef, useState, useCallback } from 'react'
import { useAutocutStore } from './autocut-store'
import FolderDropZone from './FolderDropZone'
import FileListPanel from './FileListPanel'
import VideoPreview from './VideoPreview'
import TimelinePanel from './TimelinePanel'
import SettingsPanel from './SettingsPanel'
import ProgressPanel from './ProgressPanel'

const isProcessing = (stage: string): boolean =>
  ['initializing', 'extracting', 'stage1_scan', 'vad', 'stt', 'stage2_vision', 'scene_grouping', 'editing', 'editing_pass1', 'editing_pass2', 'merging', 'generating_srt'].includes(stage)

export default function AutocutModule(): JSX.Element {
  const folderPath = useAutocutStore((s) => s.folderPath)
  const stage = useAutocutStore((s) => s.stage)
  const errorMessage = useAutocutStore((s) => s.errorMessage)
  const setProgress = useAutocutStore((s) => s.setProgress)
  const addWindowResult = useAutocutStore((s) => s.addWindowResult)
  const setAnalysisComplete = useAutocutStore((s) => s.setAnalysisComplete)
  const setError = useAutocutStore((s) => s.setError)
  const setPaused = useAutocutStore((s) => s.setPaused)
  const reset = useAutocutStore((s) => s.reset)
  const keepSegments = useAutocutStore((s) => s.keepSegments)
  const srtPath = useAutocutStore((s) => s.srtPath)
  const previewMode = useAutocutStore((s) => s.previewMode)
  const previewPaused = useAutocutStore((s) => s.previewPaused)
  const startPreview = useAutocutStore((s) => s.startPreview)
  const pausePreview = useAutocutStore((s) => s.pausePreview)
  const resumePreview = useAutocutStore((s) => s.resumePreview)
  const stopPreview = useAutocutStore((s) => s.stopPreview)
  const loadSrt = useAutocutStore((s) => s.loadSrt)
  const analysisFileIndex = useAutocutStore((s) => s.analysisFileIndex)
  const userPlayback = useAutocutStore((s) => s.userPlayback)
  const files = useAutocutStore((s) => s.files)
  const seekTo = useAutocutStore((s) => s.seekTo)

  // 리사이즈 상태
  const [fileListWidth, setFileListWidth] = useState(220)
  const [topHeight, setTopHeight] = useState<number | null>(null)
  const [isDraggingV, setIsDraggingV] = useState(false)
  const [isDraggingH, setIsDraggingH] = useState(false)
  const editorRef = useRef<HTMLDivElement>(null)

  // IPC 이벤트 리스너
  useEffect(() => {
    const cleanupProgress = window.electronAPI.autocut.onProgress((data) => {
      setProgress(data.stage as ReturnType<typeof useAutocutStore.getState>['stage'], data.percent, data.message)
    })
    const cleanupWindow = window.electronAPI.autocut.onWindowResult((data) => {
      addWindowResult(data)
    })
    const cleanupComplete = window.electronAPI.autocut.onAnalysisComplete((data) => {
      setAnalysisComplete(data.keepSegments, data.srtPath)
    })
    const cleanupError = window.electronAPI.autocut.onError((message) => {
      setError(message)
    })
    const cleanupCancelled = window.electronAPI.autocut.onCancelled(() => {
      setPaused(false)
      setProgress('idle', 0, '')
    })
    const cleanupFileComplete = window.electronAPI.autocut.onFileComplete((data) => {
      const nextIndex = data.fileIndex + 1
      const { files: currentFiles } = useAutocutStore.getState()
      if (nextIndex < currentFiles.length) {
        useAutocutStore.setState({ analysisFileIndex: nextIndex })
      }
    })
    const cleanupPaused = window.electronAPI.autocut.onPaused(() => {
      setPaused(true)
    })
    const cleanupResumed = window.electronAPI.autocut.onResumed(() => {
      setPaused(false)
    })

    return () => {
      cleanupProgress()
      cleanupWindow()
      cleanupComplete()
      cleanupError()
      cleanupCancelled()
      cleanupFileComplete()
      cleanupPaused()
      cleanupResumed()
    }
  }, [setProgress, addWindowResult, setAnalysisComplete, setError, setPaused])

  // 분석 중 파일 전환 시 자동 이동 (재생 중에는 이동하지 않음, 정지 후 10초 뒤 이동)
  const returnTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevAnalysisFileRef = useRef(-1)

  // analysisFileIndex 변경 시 → 사용자가 재생 중이 아니면 즉시 이동
  useEffect(() => {
    if (analysisFileIndex < 0 || !isProcessing(stage)) return
    if (analysisFileIndex === prevAnalysisFileRef.current) return
    prevAnalysisFileRef.current = analysisFileIndex

    const { userPlayback: up, videoPlaying: vp } = useAutocutStore.getState()
    if (!up && !vp && files[analysisFileIndex]) {
      seekTo(files[analysisFileIndex].cumulativeOffset)
    }
  }, [analysisFileIndex, stage, files, seekTo])

  // 수동 재생 정지 후 10초 뒤 분석 파일로 복귀 (한 번만)
  const wasPlayingRef = useRef(false)
  useEffect(() => {
    const justStopped = wasPlayingRef.current && !userPlayback
    wasPlayingRef.current = userPlayback

    if (returnTimerRef.current) {
      clearTimeout(returnTimerRef.current)
      returnTimerRef.current = null
    }

    if (!justStopped) return
    if (!isProcessing(stage)) return

    returnTimerRef.current = setTimeout(() => {
      const { analysisFileIndex: idx, files: fs, userPlayback: up, videoPlaying: vp } = useAutocutStore.getState()
      if (idx >= 0 && fs[idx] && !up && !vp) {
        seekTo(fs[idx].cumulativeOffset)
      }
      returnTimerRef.current = null
    }, 10000)
  }, [userPlayback, stage, seekTo])

  // 수직 분할선 드래그
  const handleVDragStart = useCallback(() => {
    setIsDraggingV(true)
    document.body.style.cursor = 'col-resize'
    const handleMove = (e: MouseEvent): void => {
      const newWidth = Math.max(160, Math.min(400, e.clientX))
      setFileListWidth(newWidth)
    }
    const handleUp = (): void => {
      setIsDraggingV(false)
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', handleMove)
      document.removeEventListener('mouseup', handleUp)
    }
    document.addEventListener('mousemove', handleMove)
    document.addEventListener('mouseup', handleUp)
  }, [])

  // 수평 분할선 드래그
  const handleHDragStart = useCallback(() => {
    setIsDraggingH(true)
    document.body.style.cursor = 'row-resize'
    const handleMove = (e: MouseEvent): void => {
      if (!editorRef.current) return
      const rect = editorRef.current.getBoundingClientRect()
      const totalHeight = rect.height
      const mouseY = e.clientY - rect.top
      const newTopHeight = Math.max(200, Math.min(totalHeight - 100, mouseY))
      setTopHeight(newTopHeight)
    }
    const handleUp = (): void => {
      setIsDraggingH(false)
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', handleMove)
      document.removeEventListener('mouseup', handleUp)
    }
    document.addEventListener('mousemove', handleMove)
    document.addEventListener('mouseup', handleUp)
  }, [])

  // 폴더 미선택: 폴더 드롭존
  if (!folderPath) {
    return (
      <div className="autocut-module" ref={editorRef}>
        <div className="module-idle">
          <FolderDropZone />
          <div className="module-guide">
            <h3 className="module-guide__title">AI 편집 워크플로우</h3>
            <ol className="module-guide__steps">
              <li><strong>폴더 선택</strong> — 영상 파일이 들어있는 폴더를 선택합니다</li>
              <li><strong>오디오 추출</strong> — FFmpeg로 각 영상에서 오디오를 분리합니다</li>
              <li><strong>Stage 1 스캔</strong> — 모션/오디오/밝기 등 경량 메트릭 분석</li>
              <li><strong>VAD + STT</strong> — 음성 구간 감지 및 텍스트 변환 <span className="module-guide__model">Silero VAD + faster-whisper (large-v3)</span></li>
              <li><strong>Stage 2 비전</strong> — 5프레임 맥락 기반 장면 분류 및 행동 태깅 <span className="module-guide__model">Qwen2.5-VL:7B 또는 Claude</span></li>
              <li><strong>AI 편집</strong> — 스토리보드 기반 KEEP/CUT 판단 <span className="module-guide__model">Qwen3:14B 또는 Claude</span></li>
              <li><strong>SRT 출력</strong> — 편집 가이드 SRT 파일 생성</li>
            </ol>
          </div>
        </div>
      </div>
    )
  }

  // 에러 상태
  if (stage === 'error') {
    return (
      <div className="autocut-module" ref={editorRef}>
        <div className="module-header">
          <button className="btn btn--sm" onClick={reset}>
            새 폴더
          </button>
        </div>
        <div className="module-idle">
          <div className="error-panel">
            <div className="error-panel__message">{errorMessage}</div>
            <button className="btn btn--primary" onClick={reset}>
              다시 시작
            </button>
          </div>
        </div>
      </div>
    )
  }

  // 에디터 레이아웃
  const topStyle = topHeight ? { height: topHeight, flex: 'none' as const } : { flex: 1 }

  return (
    <div className="autocut-module" ref={editorRef}>
      <div className="module-header">
        <div className="module-header__left">
          <button
            className="btn btn--sm"
            onClick={async () => {
              const result = await window.electronAPI.autocut.loadSrt()
              if (result) {
                const segments = result.segments.map((s, i) => ({
                  id: i,
                  globalStart: s.globalStart,
                  globalEnd: s.globalEnd,
                  label: s.label,
                  score: s.score
                }))
                loadSrt(segments, result.srtPath)
              }
            }}
          >
            SRT 불러오기
          </button>
        </div>
        <div className="module-header__right">
          <button className="btn btn--sm" onClick={reset}>
            새 폴더
          </button>
        </div>
      </div>
      <div className="editor">
        <div className="editor__top" style={topStyle}>
          <div className="editor__file-list" style={{ width: fileListWidth }}>
            <FileListPanel />
          </div>
          <div
            className={`editor__divider-v ${isDraggingV ? 'editor__divider-v--active' : ''}`}
            onMouseDown={handleVDragStart}
          />
          <div className="editor__preview">
            <VideoPreview />
            {stage === 'idle' && <SettingsPanel />}
            {isProcessing(stage) && <ProgressPanel />}
            {stage === 'complete' && (
              <div className="settings-panel settings-panel--compact">
                <div className="settings-panel__row">
                  <span className="settings-panel__info">
                    {keepSegments.length}개 KEEP — {(() => {
                      const totalSec = keepSegments.reduce((sum, s) => sum + (s.globalEnd - s.globalStart), 0)
                      const m = Math.floor(totalSec / 60)
                      const s = Math.floor(totalSec % 60)
                      return `${m}분 ${s}초`
                    })()}
                  </span>
                  <div className="settings-panel__controls">
                    <button
                      className="btn btn--sm btn--primary"
                      onClick={async () => {
                        const saved = await window.electronAPI.autocut.saveSrt(keepSegments)
                        if (!saved) return
                      }}
                    >
                      SRT 저장
                    </button>
                    <button
                      className="btn btn--sm"
                      onClick={async () => {
                        if (srtPath) await window.electronAPI.autocut.saveSrt(keepSegments, srtPath)
                      }}
                      disabled={!srtPath}
                    >
                      업데이트
                    </button>
                    {!previewMode && (
                      <button className="btn btn--sm btn--primary" onClick={startPreview}>
                        프리뷰
                      </button>
                    )}
                    {previewMode && (
                      <>
                        <button
                          className="btn btn--sm btn--primary"
                          onClick={() => previewPaused ? resumePreview() : pausePreview()}
                        >
                          {previewPaused ? '재생' : '정지'}
                        </button>
                        <button className="btn btn--sm btn--danger" onClick={stopPreview}>
                          중지
                        </button>
                      </>
                    )}
                    <button className="btn btn--sm" onClick={() => setProgress('idle', 0, '')}>
                      설정
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
        <div
          className={`editor__divider-h ${isDraggingH ? 'editor__divider-h--active' : ''}`}
          onMouseDown={handleHDragStart}
        />
        <div
          className="editor__timeline"
          style={topHeight ? { flex: 1 } : {}}
        >
          <TimelinePanel />
        </div>
      </div>
    </div>
  )
}
