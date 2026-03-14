import { useState, useCallback } from 'react'
import { useSubtitleStore, type SubtitleSegment } from './subtitle-store'

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.round((seconds % 1) * 1000)
  return (
    String(h).padStart(2, '0') +
    ':' +
    String(m).padStart(2, '0') +
    ':' +
    String(s).padStart(2, '0') +
    '.' +
    String(ms).padStart(3, '0')
  )
}

function parseTimeInput(str: string): number | null {
  const match = str.match(/^(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})$/)
  if (!match) return null
  const [, h, m, s, ms] = match
  return parseInt(h) * 3600 + parseInt(m) * 60 + parseInt(s) + parseInt(ms.padEnd(3, '0')) / 1000
}

interface Props {
  segment: SubtitleSegment
  readOnly?: boolean
}

export function SubtitleRow({ segment, readOnly }: Props): JSX.Element {
  const { updateSegment, updateSegmentTime, seekTo, activeSegmentId } = useSubtitleStore()
  const [isEditing, setIsEditing] = useState(false)
  const [editText, setEditText] = useState(segment.correctedText)
  const [editStart, setEditStart] = useState(formatTime(segment.start))
  const [editEnd, setEditEnd] = useState(formatTime(segment.end))

  const handleSave = useCallback(() => {
    updateSegment(segment.id, editText)

    const newStart = parseTimeInput(editStart)
    const newEnd = parseTimeInput(editEnd)
    if (newStart !== null && newEnd !== null && newStart < newEnd) {
      updateSegmentTime(segment.id, newStart, newEnd)
    }

    setIsEditing(false)

    setTimeout(() => {
      const { segments, srtPath } = useSubtitleStore.getState()
      if (!srtPath || !window.electronAPI) return
      window.electronAPI.subtitle.saveSrt(
        segments.map((s) => ({
          id: s.id,
          start: s.start,
          end: s.end,
          correctedText: s.correctedText
        })),
        srtPath
      )
    }, 0)
  }, [segment.id, editText, editStart, editEnd, updateSegment, updateSegmentTime])

  const handleCancel = useCallback(() => {
    setEditText(segment.correctedText)
    setEditStart(formatTime(segment.start))
    setEditEnd(formatTime(segment.end))
    setIsEditing(false)
  }, [segment])

  const adjustTime = useCallback(
    (field: 'start' | 'end', delta: number) => {
      const setter = field === 'start' ? setEditStart : setEditEnd
      const current = parseTimeInput(field === 'start' ? editStart : editEnd)
      if (current === null) return
      const next = Math.max(0, current + delta)
      setter(formatTime(next))
    },
    [editStart, editEnd]
  )

  const hasCorrection = segment.text !== segment.correctedText && !segment.isEdited
  const isActive = activeSegmentId === segment.id

  return (
    <div
      className={`sub-row ${segment.isEdited ? 'sub-row--edited' : ''} ${isActive ? 'sub-row--active' : ''}`}
      ref={(el) => { if (isActive && el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }}
      onClick={() => { if (!isEditing) seekTo(segment.start, segment.end) }}
      style={{ cursor: isEditing ? 'default' : 'pointer' }}
    >
      <div className="sub-row__num">{segment.id + 1}</div>

      {isEditing ? (
        <>
          <div className="sub-row__time-edit">
            <div className="sub-time-adjust">
              <button className="sub-time-adjust__btn" onClick={() => adjustTime('start', -0.1)}>&#9664;</button>
              <input
                value={editStart}
                onChange={(e) => setEditStart(e.target.value)}
                className="sub-time-input"
                placeholder="00:00:00.000"
              />
              <button className="sub-time-adjust__btn" onClick={() => adjustTime('start', 0.1)}>&#9654;</button>
            </div>
            <span className="sub-time-sep">&rarr;</span>
            <div className="sub-time-adjust">
              <button className="sub-time-adjust__btn" onClick={() => adjustTime('end', -0.1)}>&#9664;</button>
              <input
                value={editEnd}
                onChange={(e) => setEditEnd(e.target.value)}
                className="sub-time-input"
                placeholder="00:00:00.000"
              />
              <button className="sub-time-adjust__btn" onClick={() => adjustTime('end', 0.1)}>&#9654;</button>
            </div>
          </div>
          <div className="sub-row__text-edit">
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              className="sub-text-input"
              rows={2}
            />
          </div>
          <div className="sub-row__actions">
            <button className="btn btn--sm btn--primary" onClick={handleSave}>
              저장
            </button>
            <button className="btn btn--sm" onClick={handleCancel}>
              취소
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="sub-row__time">
            {formatTime(segment.start)} &rarr; {formatTime(segment.end)}
          </div>
          <div className="sub-row__text">
            <div className="sub-row__corrected">{segment.correctedText}</div>
            {hasCorrection && (
              <div className="sub-row__original" title="원본">
                {segment.text}
              </div>
            )}
          </div>
          <div className="sub-row__actions">
            {!readOnly && (
              <button
                className="btn btn--sm"
                onClick={(e) => {
                  e.stopPropagation()
                  setEditText(segment.correctedText)
                  setEditStart(formatTime(segment.start))
                  setEditEnd(formatTime(segment.end))
                  setIsEditing(true)
                }}
              >
                편집
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
