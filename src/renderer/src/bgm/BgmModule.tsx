import { useEffect, useCallback, useRef, useState } from 'react'
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

  const [aiEngine, setAiEngine] = useState<'ollama' | 'claude'>(() => {
    const saved = localStorage.getItem('bgm:aiEngine')
    return saved === 'ollama' ? 'ollama' : 'claude'
  })

  const handleAnalyze = useCallback(async () => {
    const { filePath, rangeStart, rangeEnd, musicPreference } = useBgmStore.getState()
    if (!filePath) return

    setProgress('analyzing', -1, '영상 분석을 준비하고 있습니다...')
    await window.electronAPI.bgm.analyzeVideo(filePath, rangeStart, rangeEnd, musicPreference, aiEngine)
  }, [setProgress, aiEngine])

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
      {filePath && (
        <div className="module-header">
          <div className="module-header__left" />
          <div className="module-header__right">
            <button className="btn btn--sm" onClick={reset}>
              새 파일
            </button>
          </div>
        </div>
      )}

      <div className="bgm-module__content">
        {stage === 'idle' && !filePath && (
          <div className="module-idle">
            <FileDropZone />
            <div className="module-guide">
              <h3 className="module-guide__title">BGM 생성 워크플로우</h3>
              <ol className="module-guide__steps">
                <li><strong>영상 선택</strong> — BGM을 만들 영상 파일을 선택합니다</li>
                <li><strong>구간 설정</strong> — BGM을 적용할 영상 구간을 지정합니다</li>
                <li><strong>장면 분석</strong> — AI가 영상의 분위기와 장면을 분석합니다 <span className="module-guide__model">LLaMA 3.2 Vision + Qwen2.5:14B (Ollama) 또는 Claude</span></li>
                <li><strong>프롬프트 편집</strong> — 분석된 음악 프롬프트를 확인하고 수정합니다</li>
                <li><strong>BGM 생성</strong> — AI가 영상에 맞는 배경음악을 생성합니다 <span className="module-guide__model">ACE-Step 1.5</span></li>
                <li><strong>미리듣기 및 저장</strong> — 생성된 BGM을 들어보고 선택합니다</li>
              </ol>
            </div>
          </div>
        )}

        {stage === 'idle' && filePath && (
          <div className="bgm-module__editor">
            <VideoPlayer />
            <StyleInput onAnalyze={handleAnalyze} aiEngine={aiEngine} onAiEngineChange={(v) => { setAiEngine(v); localStorage.setItem('bgm:aiEngine', v) }} />
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
