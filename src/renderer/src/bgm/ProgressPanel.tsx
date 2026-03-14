import { useCallback } from 'react'
import { useBgmStore } from './bgm-store'

const STEPS = [
  { key: 'analyzing', label: '영상 분석' },
  { key: 'generating', label: 'BGM 생성' }
]

export default function ProgressPanel(): JSX.Element {
  const stage = useBgmStore((s) => s.stage)
  const percent = useBgmStore((s) => s.percent)
  const message = useBgmStore((s) => s.message)

  const handleCancel = useCallback(async () => {
    await window.electronAPI.bgm.cancelGenerate()
    useBgmStore.getState().reset()
  }, [])

  const currentStepIndex = STEPS.findIndex((s) => s.key === stage)

  return (
    <div className="bgm-progress-panel">
      <div className="progress-steps">
        {STEPS.map((step, i) => {
          let status = 'pending'
          if (i < currentStepIndex) status = 'done'
          else if (i === currentStepIndex) status = 'active'

          return (
            <div key={step.key} className={`progress-step progress-step--${status}`}>
              <div className="progress-step__indicator">
                {status === 'done' ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  i + 1
                )}
              </div>
              <span className="progress-step__label">{step.label}</span>
            </div>
          )
        })}
      </div>

      <div className="progress-bar-container">
        <div
          className={`progress-bar ${percent < 0 ? 'progress-bar--indeterminate' : ''}`}
          style={percent >= 0 ? { width: `${percent}%` } : undefined}
        />
      </div>

      {message && <p className="bgm-progress-panel__message">{message}</p>}

      <button className="btn btn--cancel" onClick={handleCancel}>
        취소
      </button>
    </div>
  )
}
