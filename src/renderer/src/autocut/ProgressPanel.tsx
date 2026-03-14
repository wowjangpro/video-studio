import { useRef, useEffect, useState } from 'react'
import { useAutocutStore, type ProcessStage } from './autocut-store'

const STEPS: { key: ProcessStage; label: string }[] = [
  { key: 'initializing', label: '초기화' },
  { key: 'extracting', label: '추출' },
  { key: 'stage1_scan', label: 'Stage 1' },
  { key: 'vad', label: 'VAD' },
  { key: 'stt', label: 'STT' },
  { key: 'stage2_vision', label: 'Stage 2' },
  { key: 'scene_grouping', label: '장면분석' },
  { key: 'editing', label: 'AI 편집' },
  { key: 'merging', label: '병합' },
  { key: 'generating_srt', label: 'SRT' }
]

function normalizeStage(stage: ProcessStage): ProcessStage {
  if (stage === 'editing_pass1' || stage === 'editing_pass2') return 'editing'
  return stage
}

function getStepClass(stepKey: ProcessStage, currentStage: ProcessStage): string {
  const order = STEPS.map((s) => s.key)
  const normalized = normalizeStage(currentStage)
  const currentIdx = order.indexOf(normalized)
  const stepIdx = order.indexOf(stepKey)

  if (stepIdx < currentIdx) return 'progress-step progress-step--done'
  if (stepIdx === currentIdx) return 'progress-step progress-step--active'
  return 'progress-step'
}

function formatTime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}초`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}분 ${s}초`
}

interface ProgressPoint {
  percent: number
  time: number
}

export default function ProgressPanel(): JSX.Element {
  const stage = useAutocutStore((s) => s.stage)
  const percent = useAutocutStore((s) => s.percent)
  const message = useAutocutStore((s) => s.message)
  const paused = useAutocutStore((s) => s.paused)

  const startTimeRef = useRef<number>(0)
  const pausedDurationRef = useRef<number>(0)
  const pauseStartRef = useRef<number>(0)
  const historyRef = useRef<ProgressPoint[]>([])
  const smoothedEtaRef = useRef<number>(0)
  const [elapsed, setElapsed] = useState(0)
  const [eta, setEta] = useState('')

  // 활성 경과시간 (일시정지 제외)
  const getActiveElapsed = (): number => {
    if (!startTimeRef.current) return 0
    const pauseNow = paused && pauseStartRef.current > 0
      ? Date.now() - pauseStartRef.current
      : 0
    return (Date.now() - startTimeRef.current - pausedDurationRef.current - pauseNow) / 1000
  }

  // 분석 시작 시 타이머 리셋
  useEffect(() => {
    if (stage === 'initializing') {
      startTimeRef.current = Date.now()
      pausedDurationRef.current = 0
      pauseStartRef.current = 0
      historyRef.current = []
      smoothedEtaRef.current = 0
      setElapsed(0)
      setEta('')
    }
  }, [stage])

  // 일시정지 시간 추적
  useEffect(() => {
    if (paused) {
      pauseStartRef.current = Date.now()
    } else if (pauseStartRef.current > 0) {
      pausedDurationRef.current += Date.now() - pauseStartRef.current
      pauseStartRef.current = 0
    }
  }, [paused])

  // percent 변경 시 history 기록 (stage 변경과 무관하게 누적)
  useEffect(() => {
    if (!startTimeRef.current || percent <= 0) return
    const now = getActiveElapsed()

    const last = historyRef.current[historyRef.current.length - 1]
    // percent가 감소하면 history 리셋 (initializing→processing 전환 시)
    if (last && percent < last.percent) {
      historyRef.current = []
      smoothedEtaRef.current = 0
    }
    const history = historyRef.current
    const lastAfterReset = history[history.length - 1]
    if (!lastAfterReset || percent !== lastAfterReset.percent) {
      history.push({ percent, time: now })
      if (history.length > 30) history.shift()
    }
  }, [percent])

  // 1초마다 경과 시간 + ETA 갱신
  useEffect(() => {
    if (!startTimeRef.current || paused) return
    const timer = setInterval(() => {
      const sec = getActiveElapsed()
      setElapsed(sec)

      const history = historyRef.current
      if (history.length < 2 || percent >= 100 || percent <= 0) {
        setEta('')
        return
      }

      // 최소 5초 이상 구간의 데이터로 속도 계산
      const latest = history[history.length - 1]
      let oldest = history[0]
      for (let i = history.length - 2; i >= 0; i--) {
        if (latest.time - history[i].time >= 5) {
          oldest = history[i]
          break
        }
      }

      const dt = latest.time - oldest.time
      const dp = latest.percent - oldest.percent
      if (dt < 3 || dp <= 0) return

      const speed = dp / dt
      const rawEta = (100 - percent) / speed

      // 스무딩: 이전 ETA와 가중 평균
      const prev = smoothedEtaRef.current
      const smoothed = prev > 0 ? prev * 0.7 + rawEta * 0.3 : rawEta
      smoothedEtaRef.current = smoothed

      if (smoothed > 0 && smoothed < 86400) {
        setEta(`약 ${formatTime(smoothed)} 남음`)
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [paused, percent])

  const handleTogglePause = (): void => {
    if (paused) {
      window.electronAPI.autocut.resumeAnalysis()
    } else {
      window.electronAPI.autocut.pauseAnalysis()
    }
  }

  return (
    <div className="progress-panel">
      <div className="progress-panel__steps">
        {STEPS.map((step) => (
          <div key={step.key} className={getStepClass(step.key, stage)}>
            {step.label}
          </div>
        ))}
      </div>
      <div className={`progress-bar ${paused ? 'progress-bar--paused' : ''}`}>
        <div
          className="progress-bar__fill"
          style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
        />
      </div>
      <div className="progress-panel__info">
        <div className="progress-panel__message">
          {paused ? (message.startsWith('⚠') ? message : '일시정지됨') : message}
        </div>
        <div className="progress-panel__time">
          {elapsed > 0 && (
            <>
              <span>{formatTime(elapsed)}</span>
              {eta && <span> / {eta}</span>}
            </>
          )}
        </div>
      </div>
      <div className="progress-panel__actions">
        <button
          className={`btn btn--sm ${paused ? 'btn--primary' : ''}`}
          onClick={handleTogglePause}
        >
          {paused ? '재개' : '일시정지'}
        </button>
        <button
          className="btn btn--sm btn--danger"
          onClick={() => {
            if (!window.confirm('진행 중인 분석을 취소하시겠습니까? 현재까지의 진행 상황은 저장됩니다.')) return
            window.electronAPI.autocut.cancelAnalysis()
          }}
        >
          취소
        </button>
      </div>
    </div>
  )
}
