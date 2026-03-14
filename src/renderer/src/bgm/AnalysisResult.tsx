import { useCallback } from 'react'
import { useBgmStore } from './bgm-store'

interface AnalysisResultProps {
  onGenerate: () => void
}

export default function AnalysisResult({ onGenerate }: AnalysisResultProps): JSX.Element {
  const sceneDescription = useBgmStore((s) => s.sceneDescription)
  const musicPrompt = useBgmStore((s) => s.musicPrompt)
  const setMusicPrompt = useBgmStore((s) => s.setMusicPrompt)
  const generateCount = useBgmStore((s) => s.generateCount)
  const setGenerateCount = useBgmStore((s) => s.setGenerateCount)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        onGenerate()
      }
    },
    [onGenerate]
  )

  return (
    <div className="analysis-result">
      <div className="analysis-result__section">
        <label className="analysis-result__label">AI 영상 분석 결과</label>
        <div className="analysis-result__description">{sceneDescription}</div>
      </div>

      <div className="analysis-result__section">
        <label className="analysis-result__label">음악 생성 프롬프트</label>
        <span className="analysis-result__hint">이 텍스트가 음악 생성 AI에 직접 전달됩니다. 수정하거나 한글로 입력할 수 있습니다.</span>
        <textarea
          className="style-input__field"
          value={musicPrompt}
          onChange={(e) => setMusicPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="AI가 추천한 프롬프트를 수정하거나 직접 입력하세요"
          rows={3}
        />
      </div>

      <div className="analysis-result__actions">
        <div className="generate-count">
          <span className="generate-count__label">생성 갯수</span>
          {[1, 2, 3].map((n) => (
            <button
              key={n}
              className={`generate-count__btn ${generateCount === n ? 'generate-count__btn--active' : ''}`}
              onClick={() => setGenerateCount(n)}
            >
              {n}
            </button>
          ))}
        </div>
        <div className="analysis-result__buttons">
          <button
            className="btn"
            onClick={() => useBgmStore.setState({ stage: 'idle', sceneDescription: null })}
          >
            다시 분석
          </button>
          <button
            className="btn btn--primary"
            onClick={onGenerate}
            disabled={!musicPrompt.trim()}
          >
            BGM 생성
          </button>
        </div>
      </div>
    </div>
  )
}
