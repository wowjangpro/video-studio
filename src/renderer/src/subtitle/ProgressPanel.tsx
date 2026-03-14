import { useSubtitleStore, type ProcessStage } from './subtitle-store'

const STAGES_DEFAULT: { key: ProcessStage; label: string }[] = [
  { key: 'extracting', label: '오디오 추출' },
  { key: 'transcribing', label: '음성인식' },
  { key: 'correcting', label: '맞춤법 교정' },
  { key: 'complete', label: '완료' }
]

const STAGES_WITH_DOWNLOAD: { key: ProcessStage; label: string }[] = [
  { key: 'downloading', label: '다운로드' },
  ...STAGES_DEFAULT
]

export function SubtitleProgressPanel(): JSX.Element {
  const { stage, percent, message, segments, currentChunk, totalChunks } = useSubtitleStore()

  const stages = stage === 'downloading' ? STAGES_WITH_DOWNLOAD : STAGES_DEFAULT
  const currentIdx = stages.findIndex((s) => s.key === stage)

  const handleCancel = async (): Promise<void> => {
    await window.electronAPI.subtitle.cancelProcess()
  }

  const displayPercent =
    stage === 'transcribing' && totalChunks > 0
      ? Math.round((currentChunk / totalChunks) * 100)
      : percent

  return (
    <div className="sub-progress-panel">
      <div className="sub-progress-steps">
        {stages.map((s, idx) => {
          let status = 'pending'
          if (idx < currentIdx) status = 'done'
          else if (idx === currentIdx) status = 'active'

          return (
            <div key={s.key} className={`sub-progress-step sub-progress-step--${status}`}>
              <div className="sub-progress-step__indicator">
                {status === 'done' ? '\u2713' : idx + 1}
              </div>
              <div className="sub-progress-step__label">{s.label}</div>
            </div>
          )
        })}
      </div>

      <div className="sub-progress-bar-container">
        <div
          className="sub-progress-bar"
          style={{ width: displayPercent >= 0 ? `${displayPercent}%` : '100%' }}
          data-indeterminate={displayPercent < 0 ? 'true' : undefined}
        />
      </div>

      <div className="sub-progress-message">{message}</div>

      {segments.length > 0 && (
        <div className="sub-progress-segment-count">
          {segments.length}개 자막 인식됨
        </div>
      )}

      {stage !== 'complete' && stage !== 'error' && (
        <button className="btn btn--danger btn--sm" onClick={handleCancel}>
          취소
        </button>
      )}
    </div>
  )
}
