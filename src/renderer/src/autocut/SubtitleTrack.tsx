import type { KeepSegment } from './autocut-store'

interface SubtitleTrackProps {
  keepSegments: KeepSegment[]
  zoom: number
  onClickSegment: (globalTime: number) => void
}

export default function SubtitleTrack({
  keepSegments,
  zoom,
  onClickSegment
}: SubtitleTrackProps): JSX.Element {
  return (
    <div className="subtitle-track">
      <div className="subtitle-track__label">자막</div>
      <div className="subtitle-track__content">
        {keepSegments.map((seg) => {
          const left = seg.globalStart * zoom
          const width = (seg.globalEnd - seg.globalStart) * zoom
          if (width < 1) return null
          return (
            <div
              key={seg.id}
              className="subtitle-track__block"
              style={{ left, width }}
              onClick={() => onClickSegment(seg.globalStart)}
              title={`${seg.label} (score: ${seg.score})`}
            >
              <span className="subtitle-track__block-label">{seg.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
