import { useEffect, useCallback, useState } from 'react'
import { useSubtitleStore, type ProcessStage } from './subtitle-store'
import { FileDropZone } from './FileDropZone'
import { SubtitleProgressPanel } from './ProgressPanel'
import { SubtitleEditor } from './SubtitleEditor'
import { SubtitleToolbar } from './SubtitleToolbar'
import { DescriptionTranslator } from './DescriptionTranslator'
import { SubtitleVideoPreview } from './VideoPreview'
import { SubtitleResizableLayout } from './ResizableLayout'

function DescriptionInput(): JSX.Element {
  const { videoDescription, setVideoDescription } = useSubtitleStore()
  return (
    <input
      type="text"
      className="sub-file-preview__desc"
      placeholder="영상 설명 (예: 캠핑 브이로그, 남성 1인) - 자막 품질 향상에 도움"
      value={videoDescription}
      onChange={(e) => setVideoDescription(e.target.value)}
    />
  )
}

export default function SubtitleModule(): JSX.Element {
  const {
    stage,
    filePath,
    fileName,
    setProgress,
    setChunkProgress,
    setMediaUrl,
    addSegment,
    setSegments,
    setError,
    reset,
    setFile,
    setSrtPath,
    setTranslatedSegments,
    loadComplete
  } = useSubtitleStore()

  useEffect(() => {
    if (!filePath || !window.electronAPI?.getMediaUrl) return
    window.electronAPI.getMediaUrl(filePath).then((url) => {
      setMediaUrl(url)
    })
  }, [filePath, setMediaUrl])

  useEffect(() => {
    const api = window.electronAPI?.subtitle
    if (!api) return

    const unsubProgress = api.onProgress((data) => {
      setProgress(data.stage as ProcessStage, data.percent, data.message)
    })

    const unsubChunk = api.onChunkProgress((data) => {
      setChunkProgress(data.chunk, data.totalChunks, data.chunkStart, data.chunkEnd)
    })

    const unsubSegment = api.onSegmentAdded((segment) => {
      addSegment({ ...segment, isEdited: false })
    })

    const unsubCorrection = api.onCorrectionComplete((segments) => {
      setSegments(segments.map((s) => ({ ...s, isEdited: false })))
    })

    const unsubComplete = api.onProcessComplete((data) => {
      setSegments(data.segments.map((s) => ({ ...s, isEdited: false })))
      if (data.srtPath) {
        setSrtPath(data.srtPath)
      }
    })

    const unsubError = api.onError((message) => {
      setError(message)
    })

    const unsubDownload = api.onDownloadComplete((downloadedPath) => {
      setFile(downloadedPath)
    })

    const unsubTranslate = api.onTranslateComplete((data) => {
      const lang = data.lang as 'en' | 'jp'
      const segs = data.segments.map((s) => ({
        ...s,
        correctedText: s.text,
        isEdited: false
      }))
      setTranslatedSegments(lang, segs)
    })

    return () => {
      unsubProgress()
      unsubChunk()
      unsubSegment()
      unsubCorrection()
      unsubComplete()
      unsubError()
      unsubDownload()
      unsubTranslate()
    }
  }, [setProgress, setChunkProgress, addSegment, setSegments, setError, setSrtPath, setFile, setTranslatedSegments])

  const handleStart = useCallback(async () => {
    if (!filePath || !window.electronAPI) return
    const desc = useSubtitleStore.getState().videoDescription
    await window.electronAPI.subtitle.startProcess(filePath, undefined, desc || undefined)
  }, [filePath])

  const handleChangeFile = useCallback(async () => {
    if (!window.electronAPI) return
    const newPath = await window.electronAPI.subtitle.selectFile()
    if (newPath) {
      setFile(newPath)
    }
  }, [setFile])

  const handleLoadSrt = useCallback(async () => {
    if (!filePath || !window.electronAPI) return
    const result = await window.electronAPI.subtitle.selectSrtFile()
    if (!result) return
    loadComplete(
      filePath,
      result.srtPath,
      result.segments.map((s) => ({ ...s, isEdited: false }))
    )
  }, [filePath, loadComplete])

  const [currentView, setCurrentView] = useState<'subtitle' | 'description'>('subtitle')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [urlLoading, setUrlLoading] = useState(false)

  const handleDownload = useCallback(async () => {
    if (!youtubeUrl.trim() || !window.electronAPI) return
    setUrlLoading(true)

    try {
      const info = await window.electronAPI.subtitle.getYoutubeInfo(youtubeUrl.trim())
      if (!info) {
        setError('유효하지 않은 YouTube URL입니다.')
        setUrlLoading(false)
        return
      }

      setUrlLoading(false)
      const safeName = info.title.replace(/[/\\?%*:|"<>]/g, '_')
      await window.electronAPI.subtitle.startYoutubeDownload(youtubeUrl.trim(), safeName)
    } catch {
      setUrlLoading(false)
    }
  }, [youtubeUrl, setError])

  const showEditor = stage === 'complete'

  return (
    <div className="sub-module">
      <div className="module-header">
        <div className="module-header__left">
          <button
            className={`btn btn--sm${currentView === 'subtitle' ? ' btn--primary' : ''}`}
            onClick={() => setCurrentView('subtitle')}
          >
            자막 생성
          </button>
          <button
            className={`btn btn--sm${currentView === 'description' ? ' btn--primary' : ''}`}
            onClick={() => setCurrentView('description')}
          >
            설명 번역
          </button>
        </div>
        <div className="module-header__right">
          {showEditor && currentView === 'subtitle' && (
            <button className="btn btn--sm" onClick={reset}>
              새 파일
            </button>
          )}
        </div>
      </div>

      <div className="sub-module__content">
        {currentView === 'description' && (
          <DescriptionTranslator />
        )}

        {currentView === 'subtitle' && stage === 'idle' && !filePath && (
          <div className="module-idle">
            <FileDropZone />
            <div className="sub-url-input-group">
              <input
                className="sub-url-input"
                type="text"
                placeholder="YouTube URL 붙여넣기"
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleDownload() }}
              />
              <button
                className="btn btn--primary"
                onClick={handleDownload}
                disabled={!youtubeUrl.trim() || urlLoading}
              >
                {urlLoading ? '확인 중...' : '다운로드'}
              </button>
            </div>
            <div className="module-guide">
              <h3 className="module-guide__title">자막 추출 워크플로우</h3>
              <ol className="module-guide__steps">
                <li><strong>영상 선택</strong> — 로컬 파일 또는 YouTube URL로 영상을 불러옵니다</li>
                <li><strong>오디오 추출</strong> — FFmpeg로 16kHz WAV 오디오를 분리합니다</li>
                <li><strong>음성인식</strong> — VAD로 음성 구간을 감지하고 자막을 생성합니다 <span className="module-guide__model">Silero VAD + faster-whisper (large-v3)</span></li>
                <li><strong>맞춤법 교정</strong> — 생성된 자막의 맞춤법을 자동 교정합니다 <span className="module-guide__model">네이버 맞춤법 검사기</span></li>
                <li><strong>편집 및 저장</strong> — 자막을 수정하고 SRT 파일로 저장합니다</li>
                <li><strong>번역 (선택)</strong> — 영어/일본어로 자막을 번역합니다 <span className="module-guide__model">Qwen2.5:14B (Ollama) 또는 Claude</span></li>
              </ol>
            </div>
          </div>
        )}

        {currentView === 'subtitle' && stage === 'idle' && filePath && (
          <div className="sub-file-preview">
            <div className="sub-file-preview__video">
              <SubtitleVideoPreview />
            </div>
            <div className="sub-file-preview__info">
              <div className="sub-file-preview__name">{fileName}</div>
              <DescriptionInput />
              <div className="sub-file-preview__actions">
                <button className="btn btn--primary btn--lg" onClick={handleStart}>
                  자막 생성 시작
                </button>
                <button className="btn" onClick={handleLoadSrt}>
                  자막 파일 선택
                </button>
                <button className="btn btn--sm" onClick={handleChangeFile}>
                  다른 파일 선택
                </button>
              </div>
            </div>
          </div>
        )}

        {currentView === 'subtitle' && stage === 'downloading' && (
          <SubtitleProgressPanel />
        )}

        {currentView === 'subtitle' && (stage === 'extracting' || stage === 'transcribing' || stage === 'correcting') && (
          <SubtitleResizableLayout
            initialRatio={0.4}
            minRatio={0.2}
            left={<SubtitleVideoPreview />}
            right={<SubtitleProgressPanel />}
          />
        )}

        {currentView === 'subtitle' && showEditor && (
          <>
            <SubtitleToolbar />
            <SubtitleResizableLayout
              initialRatio={0.4}
              minRatio={0.2}
              left={<SubtitleVideoPreview />}
              right={<SubtitleEditor />}
            />
          </>
        )}

        {currentView === 'subtitle' && stage === 'error' && (
          <div className="sub-error-panel">
            <div className="sub-error-panel__message">
              {useSubtitleStore.getState().errorMessage}
            </div>
            <button className="btn btn--primary" onClick={reset}>
              다시 시작
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
