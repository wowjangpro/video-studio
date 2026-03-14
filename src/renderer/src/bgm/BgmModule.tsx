import { useEffect, useCallback, useRef } from 'react'
import { useBgmStore } from './bgm-store'
import FileDropZone from './FileDropZone'
import VideoPlayer from './VideoPlayer'
import StyleInput from './StyleInput'
import AnalysisResult from './AnalysisResult'
import ProgressPanel from './ProgressPanel'
import AudioPreview from './AudioPreview'

export default function BgmModule(): JSX.Element {
  const filePath = useBgmStore((s) => s.filePath)
  const stage = useBgmStore((s) => s.stage)
  const errorMessage = useBgmStore((s) => s.errorMessage)
  const setMediaUrl = useBgmStore((s) => s.setMediaUrl)
  const setProgress = useBgmStore((s) => s.setProgress)
  const setSceneDescription = useBgmStore((s) => s.setSceneDescription)
  const setBgm = useBgmStore((s) => s.setBgm)
  const setError = useBgmStore((s) => s.setError)
  const reset = useBgmStore((s) => s.reset)

  useEffect(() => {
    if (!filePath) return
    window.electronAPI.getMediaUrl(filePath).then(setMediaUrl)
  }, [filePath, setMediaUrl])

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    const cleanupProgress = window.electronAPI.bgm.onProgress((data) => {
      setProgress(data.stage as 'analyzing' | 'generating', data.percent, data.message)
    })

    const cleanupAnalyzed = window.electronAPI.bgm.onAnalyzeComplete((desc, musicPrompt) => {
      setSceneDescription(desc, musicPrompt)
    })

    const cleanupComplete = window.electronAPI.bgm.onGenerateComplete(async (bgmPaths) => {
      const bgmUrls = await Promise.all(
        bgmPaths.map((p) => window.electronAPI.getMediaUrl(p))
      )
      if (mountedRef.current) setBgm(bgmPaths, bgmUrls)
    })

    const cleanupError = window.electronAPI.bgm.onError((message) => {
      setError(message)
    })

    return () => {
      cleanupProgress()
      cleanupAnalyzed()
      cleanupComplete()
      cleanupError()
    }
  }, [setProgress, setSceneDescription, setBgm, setError])

  const handleAnalyze = useCallback(async () => {
    const { filePath, rangeStart, rangeEnd, musicPreference } = useBgmStore.getState()
    if (!filePath) return

    setProgress('analyzing', -1, '영상 분석을 준비하고 있습니다...')
    await window.electronAPI.bgm.analyzeVideo(filePath, rangeStart, rangeEnd, musicPreference)
  }, [setProgress])

  const handleGenerate = useCallback(async () => {
    const { filePath, rangeStart, rangeEnd, musicPrompt, generateCount } = useBgmStore.getState()
    if (!filePath || !musicPrompt.trim()) return

    setProgress('generating', -1, 'BGM 생성을 준비하고 있습니다...')
    await window.electronAPI.bgm.generateBgm(
      filePath, rangeStart, rangeEnd, musicPrompt.trim(), generateCount
    )
  }, [setProgress])

  return (
    <div className="bgm-module">
      <header className="bgm-module__header">
        <h2 className="bgm-module__title">BGM Studio</h2>
        {filePath && (
          <button className="btn btn--sm" onClick={reset}>
            새 파일
          </button>
        )}
      </header>

      <div className="bgm-module__content">
        {stage === 'idle' && !filePath && <FileDropZone />}

        {stage === 'idle' && filePath && (
          <div className="bgm-module__editor">
            <VideoPlayer />
            <StyleInput onAnalyze={handleAnalyze} />
          </div>
        )}

        {stage === 'analyzing' && (
          <div className="bgm-module__processing">
            <VideoPlayer />
            <ProgressPanel />
          </div>
        )}

        {stage === 'analyzed' && (
          <div className="bgm-module__editor">
            <VideoPlayer />
            <AnalysisResult onGenerate={handleGenerate} />
          </div>
        )}

        {stage === 'generating' && (
          <div className="bgm-module__processing">
            <VideoPlayer />
            <ProgressPanel />
          </div>
        )}

        {stage === 'complete' && <AudioPreview />}

        {stage === 'error' && (
          <div className="error-panel">
            <p className="error-panel__message">{errorMessage}</p>
            <div className="error-panel__actions">
              <button className="btn btn--primary" onClick={() => {
                useBgmStore.setState({ stage: 'idle', errorMessage: null })
              }}>
                다시 분석
              </button>
              <button className="btn" onClick={reset}>
                새 영상 선택
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
