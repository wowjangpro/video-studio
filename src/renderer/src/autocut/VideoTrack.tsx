import type { FileInfo } from './autocut-store'
import { useAutocutStore } from './autocut-store'

interface VideoTrackProps {
  files: FileInfo[]
  zoom: number
  selectedFileIndex: number
  onClickClip: (fileIndex: number, globalTime: number) => void
}

export default function VideoTrack({
  files,
  zoom,
  selectedFileIndex,
  onClickClip
}: VideoTrackProps): JSX.Element {
  const analysisFileIndex = useAutocutStore((s) => s.analysisFileIndex)
  const stage = useAutocutStore((s) => s.stage)
  const excludedFiles = useAutocutStore((s) => s.excludedFiles)
  const isAnalyzing = stage !== 'idle' && stage !== 'complete' && stage !== 'error'

  return (
    <div className="video-track">
      <div className="video-track__label">V1</div>
      <div className="video-track__content">
        {files.map((file, i) => {
          const left = file.cumulativeOffset * zoom
          const width = file.duration * zoom
          if (width < 1) return null
          const isSelected = i === selectedFileIndex
          const isCurrentAnalysis = isAnalyzing && i === analysisFileIndex
          const isExcluded = excludedFiles.has(i)
          return (
            <div
              key={file.path}
              className={`video-track__clip${isSelected ? ' video-track__clip--selected' : ''}${isCurrentAnalysis ? ' video-track__clip--analyzing' : ''}${isExcluded ? ' video-track__clip--excluded' : ''}`}
              style={{ left, width }}
              onClick={() => onClickClip(i, file.cumulativeOffset)}
            >
              {file.thumbnailUrl && (
                <img
                  className="video-track__thumb"
                  src={file.thumbnailUrl}
                  alt=""
                  draggable={false}
                />
              )}
              <span className="video-track__name">{file.name}</span>
              {isCurrentAnalysis && <div className="video-track__analyzing-badge">분석중</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
