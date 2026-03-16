import { useAutocutStore, type ProcessStage } from './autocut-store'

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function isFileAnalysis(stage: ProcessStage): boolean {
  return (
    stage === 'extracting' ||
    stage === 'stage1_scan' ||
    stage === 'vad' ||
    stage === 'stt' ||
    stage === 'stage2_vision'
  )
}

export default function FileListPanel(): JSX.Element {
  const files = useAutocutStore((s) => s.files)
  const selectedFileIndex = useAutocutStore((s) => s.selectedFileIndex)
  const selectFile = useAutocutStore((s) => s.selectFile)
  const analysisFileIndex = useAutocutStore((s) => s.analysisFileIndex)
  const stage = useAutocutStore((s) => s.stage)
  const excludedFiles = useAutocutStore((s) => s.excludedFiles)
  const toggleFileExclude = useAutocutStore((s) => s.toggleFileExclude)

  const processing = isFileAnalysis(stage)
  const isIdle = stage === 'idle'
  const isComplete = stage === 'complete'

  return (
    <>
      <div className="file-list__header">
        영상 파일 ({files.length - excludedFiles.size}/{files.length})
      </div>
      {files.map((file, i) => {
        const isCurrent = processing && i === analysisFileIndex
        const isDone = processing && analysisFileIndex >= 0 && i < analysisFileIndex
        const isExcluded = excludedFiles.has(i)

        return (
          <div
            key={file.path}
            className={
              `file-list__item` +
              (i === selectedFileIndex ? ' file-list__item--selected' : '') +
              (isCurrent ? ' file-list__item--analyzing' : '') +
              (isDone ? ' file-list__item--done' : '') +
              (isExcluded ? ' file-list__item--excluded' : '')
            }
            onClick={() => selectFile(i)}
          >
            <div className="file-list__thumb-wrap">
              {file.thumbnailUrl ? (
                <img className="file-list__thumb" src={file.thumbnailUrl} alt="" />
              ) : (
                <div className="file-list__thumb" />
              )}
              <span className="file-list__index">{i + 1}</span>
            </div>
            <div className="file-list__info">
              <div className="file-list__name" title={file.name}>
{file.name}
              </div>
              <div className="file-list__duration">
                {formatDuration(file.duration)}
                {isCurrent && <span className="file-list__status">분석중</span>}
                {isDone && <span className="file-list__status file-list__status--done">완료</span>}
              </div>
            </div>
            {(isIdle || isComplete) && (
              <button
                className={`file-list__exclude-btn ${isExcluded ? 'file-list__exclude-btn--active' : ''}`}
                onClick={(e) => {
                  e.stopPropagation()
                  toggleFileExclude(i)
                }}
                title={isExcluded ? '포함' : '제외'}
              >
                {isExcluded ? '✕' : '✓'}
              </button>
            )}
          </div>
        )
      })}
    </>
  )
}
