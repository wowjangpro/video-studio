import { useBgmStore, type RecommendedTrack } from './bgm-store'
import { useLibraryStore } from './library-store'
import { usePreview, formatTime } from './preview'

const ENERGY_LABEL = { low: '낮음', medium: '보통', high: '높음' } as const

function TrackCard({ track, rank }: { track: RecommendedTrack; rank: number }): JSX.Element {
  const root = useLibraryStore((s) => s.info?.root)
  const resultRange = useBgmStore((s) => s.resultRange)
  const addCue = useBgmStore((s) => s.addCue)
  const cueBusy = useBgmStore((s) => s.cueBusy)
  const playingKey = usePreview((s) => s.playingKey)
  const play = usePreview((s) => s.play)
  const stop = usePreview((s) => s.stop)

  const key = `rec:${track.id}`
  const isPlaying = playingKey === key
  const range = resultRange ?? { start: 0, end: 0 }

  return (
    <div className={`rec-card ${isPlaying ? 'rec-card--playing' : ''}`}>
      <div className="rec-card__rank">{rank}</div>
      <div className="rec-card__body">
        <div className="rec-card__title">
          {track.title}
          <span className="rec-card__artist">{track.artist}</span>
        </div>
        <div className="rec-card__meta">
          {[track.genre, track.mood].filter(Boolean).join(' · ')}
          <span>곡의 {formatTime(track.offsetSec)}부터</span>
          {track.bpm != null && <span>{Math.round(track.bpm)} BPM</span>}
          {track.hasVocals && <span className="rec-card__badge">보컬</span>}
          {track.licenseType === 'attribution_required' && (
            <span className="rec-card__badge rec-card__badge--warn" title={track.attributionText ?? ''}>출처 표기</span>
          )}
        </div>
        {track.reasons.length > 0 && <div className="rec-card__reasons">{track.reasons.join(' · ')}</div>}
      </div>
      <div className="rec-card__actions">
        <button
          className="btn btn--sm"
          disabled={!track.audioPath}
          onClick={() =>
            isPlaying
              ? stop()
              : track.audioPath &&
                play({ key, audioPath: track.audioPath, offsetSec: track.offsetSec, rangeStart: range.start, rangeEnd: range.end })
          }
        >
          {isPlaying ? '■ 정지' : '▶ 장면과 듣기'}
        </button>
        <button
          className="btn btn--sm btn--primary"
          disabled={!root || cueBusy}
          onClick={() => root && void addCue(root, track.id, track.offsetSec, range)}
        >
          이 구간에 넣기
        </button>
      </div>
    </div>
  )
}

export default function RecommendPanel(): JSX.Element {
  const root = useLibraryStore((s) => s.info?.root)
  const analyzed = useLibraryStore((s) => s.info?.analyzedCount ?? 0)
  const rangeStart = useBgmStore((s) => s.rangeStart)
  const rangeEnd = useBgmStore((s) => s.rangeEnd)
  const musicPreference = useBgmStore((s) => s.musicPreference)
  const setMusicPreference = useBgmStore((s) => s.setMusicPreference)
  const stage = useBgmStore((s) => s.stage)
  const percent = useBgmStore((s) => s.percent)
  const message = useBgmStore((s) => s.message)
  const errorMessage = useBgmStore((s) => s.errorMessage)
  const result = useBgmStore((s) => s.result)
  const resultRange = useBgmStore((s) => s.resultRange)
  const recommend = useBgmStore((s) => s.recommend)
  const cancel = useBgmStore((s) => s.cancel)

  const busy = stage === 'recommending'
  const rangeMoved = resultRange && (resultRange.start !== rangeStart || resultRange.end !== rangeEnd)

  return (
    <section className="rec-panel">
      <div className="rec-panel__input">
        <label className="rec-panel__label">원하는 음악 느낌 (선택)</label>
        <textarea
          className="rec-panel__field"
          value={musicPreference}
          onChange={(e) => setMusicPreference(e.target.value)}
          placeholder="비워 두면 장면만 보고 고릅니다. 예: 어쿠스틱 기타 위주, 잔잔하게"
          rows={2}
          disabled={busy}
        />
        <div className="rec-panel__footer">
          <span className="rec-panel__info">
            구간 {formatTime(rangeStart)} ~ {formatTime(rangeEnd)} ({Math.round(rangeEnd - rangeStart)}초)
          </span>
          {busy ? (
            <button className="btn" onClick={() => void cancel()}>취소</button>
          ) : (
            <button className="btn btn--primary" disabled={!root || analyzed === 0} onClick={() => root && void recommend(root)}>
              어울리는 곡 찾기
            </button>
          )}
        </div>
        {!root && <p className="rec-panel__hint">오른쪽에서 음악 라이브러리 폴더를 먼저 선택하세요.</p>}
        {root && analyzed === 0 && <p className="rec-panel__hint">라이브러리 곡 분석이 아직 없습니다. 분석을 마친 곡만 추천에 쓰입니다.</p>}
      </div>

      {busy && (
        <div className="rec-panel__progress">
          <div className="progress-bar-container">
            <div
              className={`progress-bar ${percent < 0 ? 'progress-bar--indeterminate' : ''}`}
              style={percent >= 0 ? { width: `${percent}%` } : undefined}
            />
          </div>
          <p className="rec-panel__message">{message}</p>
        </div>
      )}

      {stage === 'error' && errorMessage && <div className="rec-panel__error">{errorMessage}</div>}

      {result && !busy && (
        <div className="rec-panel__result">
          <div className="rec-panel__criteria">
            <p className="rec-panel__scene">{result.criteria.scene_description}</p>
            <div className="rec-panel__chips">
              {[...result.criteria.genres, ...result.criteria.moods].map((g) => (
                <span key={g} className="rec-chip">{g}</span>
              ))}
              <span className="rec-chip">에너지 {ENERGY_LABEL[result.criteria.energy]}</span>
              {result.criteria.bpm_min && result.criteria.bpm_max && (
                <span className="rec-chip">{Math.round(result.criteria.bpm_min)}~{Math.round(result.criteria.bpm_max)} BPM</span>
              )}
              <span className="rec-chip">{result.criteria.vocals_ok ? '보컬 허용' : '연주곡 위주'}</span>
            </div>
            {rangeMoved && (
              <p className="rec-panel__hint">
                이 추천은 {formatTime(resultRange.start)} ~ {formatTime(resultRange.end)} 구간 기준입니다. 넣으면 그 구간에 들어갑니다.
              </p>
            )}
          </div>
          {result.tracks.length === 0 && <p className="rec-panel__hint">조건에 맞는 곡이 없습니다.</p>}
          {result.tracks.map((t, i) => (
            <TrackCard key={t.id} track={t} rank={i + 1} />
          ))}
        </div>
      )}
    </section>
  )
}
