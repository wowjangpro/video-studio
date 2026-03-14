import { useBgmStore } from './bgm-store'

interface StyleInputProps {
  onAnalyze: () => void
}

export default function StyleInput({ onAnalyze }: StyleInputProps): JSX.Element {
  const rangeStart = useBgmStore((s) => s.rangeStart)
  const rangeEnd = useBgmStore((s) => s.rangeEnd)
  const musicPreference = useBgmStore((s) => s.musicPreference)
  const setMusicPreference = useBgmStore((s) => s.setMusicPreference)

  const rangeDuration = rangeEnd - rangeStart

  return (
    <div className="style-input">
      <div className="style-input__section">
        <label className="style-input__label">나의 음악 취향</label>
        <span className="style-input__hint">AI가 영상을 분석할 때 이 취향을 반영하여 BGM 스타일을 추천합니다</span>
        <textarea
          className="style-input__field"
          value={musicPreference}
          onChange={(e) => setMusicPreference(e.target.value)}
          placeholder="예: 어쿠스틱 기타와 피아노 위주, 따뜻하고 잔잔한 느낌"
          rows={2}
        />
      </div>
      <div className="style-input__footer">
        <span className="style-input__info">
          선택 구간: {Math.round(rangeDuration)}초 (최대 60초)
        </span>
        <button className="btn btn--primary" onClick={onAnalyze}>
          영상 분석
        </button>
      </div>
    </div>
  )
}
