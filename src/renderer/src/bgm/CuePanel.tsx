import { useEffect, useState } from 'react'
import { useBgmStore } from './bgm-store'
import { useLibraryStore } from './library-store'
import { usePreview, formatTime } from './preview'

export default function CuePanel(): JSX.Element | null {
  const root = useLibraryStore((s) => s.info?.root)
  const filePath = useBgmStore((s) => s.filePath)
  const cues = useBgmStore((s) => s.cues)
  const cueBusy = useBgmStore((s) => s.cueBusy)
  const exportResult = useBgmStore((s) => s.exportResult)
  const loadCues = useBgmStore((s) => s.loadCues)
  const removeCue = useBgmStore((s) => s.removeCue)
  const exportDavinci = useBgmStore((s) => s.exportDavinci)
  const playingKey = usePreview((s) => s.playingKey)
  const play = usePreview((s) => s.play)
  const stop = usePreview((s) => s.stop)
  const [copied, setCopied] = useState(false)

  // 영상이나 라이브러리가 바뀌면 그 영상에 넣어 둔 곡을 다시 읽는다
  useEffect(() => {
    if (root && filePath) void loadCues(root)
  }, [root, filePath, loadCues])

  if (!root || !filePath) return null

  const needsCredit = cues.some((c) => c.licenseType === 'attribution_required')

  return (
    <section className="cue-panel">
      <div className="cue-panel__header">
        <h3 className="cue-panel__title">이 영상에 넣은 곡 ({cues.length})</h3>
        <button
          className="btn btn--sm btn--primary"
          disabled={cues.length === 0 || cueBusy}
          onClick={() => void exportDavinci(root)}
        >
          다빈치용 내보내기
        </button>
      </div>

      {cues.length === 0 && <p className="cue-panel__empty">추천 목록이나 라이브러리에서 곡을 넣으면 여기에 쌓입니다.</p>}

      {cues.map((c) => {
        const key = `cue:${c.cueId}`
        const isPlaying = playingKey === key
        return (
          <div key={c.cueId} className={`cue-row ${isPlaying ? 'cue-row--playing' : ''}`}>
            <span className="cue-row__time">{formatTime(c.rangeStart)}–{formatTime(c.rangeEnd)}</span>
            <div className="cue-row__info">
              <div className="cue-row__name">{c.title} <span className="cue-row__artist">{c.artist}</span></div>
              <div className="cue-row__sub">
                곡의 {formatTime(c.offsetSec)}부터
                {c.licenseType === 'attribution_required' && <span className="rec-card__badge rec-card__badge--warn">출처 표기</span>}
              </div>
            </div>
            <button
              className="btn btn--sm"
              disabled={!c.audioPath}
              onClick={() =>
                isPlaying
                  ? stop()
                  : c.audioPath &&
                    play({ key, audioPath: c.audioPath, offsetSec: c.offsetSec, rangeStart: c.rangeStart, rangeEnd: c.rangeEnd })
              }
            >
              {isPlaying ? '■' : '▶'}
            </button>
            <button className="btn btn--sm" disabled={cueBusy} onClick={() => void removeCue(root, c.cueId)}>
              빼기
            </button>
          </div>
        )
      })}

      {exportResult && (
        <div className="cue-panel__export">
          <p className="cue-panel__done">
            다빈치에서 <b>File › Import › Timeline › Final Cut Pro XML</b> 로 아래 파일을 불러오세요.
          </p>
          <div className="cue-panel__file">
            <code>{exportResult.fcpxmlPath}</code>
            <button className="btn btn--sm" onClick={() => void window.electronAPI.bgm.reveal(exportResult.fcpxmlPath)}>
              Finder에서 보기
            </button>
          </div>
          <div className="cue-panel__credits-head">
            <span>{needsCredit ? '유튜브 설명란에 꼭 넣을 출처' : '출처 표기가 필요한 곡이 없습니다'}</span>
            <button
              className="btn btn--sm"
              onClick={() => {
                void navigator.clipboard.writeText(exportResult.credits).then(() => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1500)
                })
              }}
            >
              {copied ? '복사됨' : '출처 문구 복사'}
            </button>
          </div>
          <pre className="cue-panel__credits">{exportResult.credits}</pre>
        </div>
      )}
    </section>
  )
}
