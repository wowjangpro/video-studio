import { useRef, useCallback, useEffect, useState } from 'react'
import { useBgmStore } from './bgm-store'

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function VideoPlayer(): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef<'start' | 'end' | 'fill' | null>(null)
  const dragOffsetRef = useRef(0)

  const mediaUrl = useBgmStore((s) => s.mediaUrl)
  const duration = useBgmStore((s) => s.duration)
  const rangeStart = useBgmStore((s) => s.rangeStart)
  const rangeEnd = useBgmStore((s) => s.rangeEnd)
  const setDuration = useBgmStore((s) => s.setDuration)
  const setRange = useBgmStore((s) => s.setRange)

  const [currentTime, setCurrentTime] = useState(0)
  const [activeHandle, setActiveHandle] = useState<'start' | 'end' | null>(null)

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current
    if (video) {
      setDuration(video.duration)
    }
  }, [setDuration])

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current
    if (video) {
      setCurrentTime(video.currentTime)
    }
  }, [])

  const positionToTime = useCallback(
    (clientX: number): number => {
      const track = trackRef.current
      if (!track || duration === 0) return 0
      const rect = track.getBoundingClientRect()
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
      return ratio * duration
    },
    [duration]
  )

  const handleMouseDown = useCallback(
    (handle: 'start' | 'end' | 'fill', e: React.MouseEvent) => {
      e.preventDefault()
      draggingRef.current = handle

      if (handle === 'start' || handle === 'end') {
        setActiveHandle(handle)
        ;(e.currentTarget as HTMLElement).focus()
      }

      if (handle === 'fill') {
        dragOffsetRef.current = positionToTime(e.clientX) - rangeStart
      }

      const handleMouseMove = (ev: MouseEvent): void => {
        const time = positionToTime(ev.clientX)
        const minGap = 1
        const maxRange = 180

        if (draggingRef.current === 'start') {
          const clamped = Math.max(0, Math.min(time, rangeEnd - minGap))
          const newStart = Math.max(clamped, rangeEnd - maxRange)
          setRange(newStart, rangeEnd)
          if (videoRef.current) videoRef.current.currentTime = newStart
        } else if (draggingRef.current === 'end') {
          const clamped = Math.min(duration, Math.max(time, rangeStart + minGap))
          const newEnd = Math.min(clamped, rangeStart + maxRange)
          setRange(rangeStart, newEnd)
          if (videoRef.current) videoRef.current.currentTime = newEnd
        } else if (draggingRef.current === 'fill') {
          const span = rangeEnd - rangeStart
          let newStart = time - dragOffsetRef.current
          newStart = Math.max(0, Math.min(newStart, duration - span))
          setRange(newStart, newStart + span)
          if (videoRef.current) {
            videoRef.current.currentTime = activeHandle === 'end' ? newStart + span : newStart
          }
        }
      }

      const handleMouseUp = (): void => {
        draggingRef.current = null
        window.removeEventListener('mousemove', handleMouseMove)
        window.removeEventListener('mouseup', handleMouseUp)
      }

      window.addEventListener('mousemove', handleMouseMove)
      window.addEventListener('mouseup', handleMouseUp)
    },
    [duration, rangeStart, rangeEnd, setRange, positionToTime]
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!activeHandle || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
      e.preventDefault()

      const step = e.shiftKey ? 1 : 0.1
      const dir = e.key === 'ArrowRight' ? step : -step
      const minGap = 1
      const maxRange = 180

      if (activeHandle === 'start') {
        const newStart = Math.max(0, Math.min(rangeStart + dir, rangeEnd - minGap))
        const clamped = Math.max(newStart, rangeEnd - maxRange)
        setRange(clamped, rangeEnd)
        if (videoRef.current) videoRef.current.currentTime = clamped
      } else {
        const newEnd = Math.min(duration, Math.max(rangeEnd + dir, rangeStart + minGap))
        const clamped = Math.min(newEnd, rangeStart + maxRange)
        setRange(rangeStart, clamped)
        if (videoRef.current) videoRef.current.currentTime = clamped
      }
    },
    [activeHandle, duration, rangeStart, rangeEnd, setRange]
  )

  useEffect(() => {
    return () => {
      draggingRef.current = null
    }
  }, [])

  const startPercent = duration > 0 ? (rangeStart / duration) * 100 : 0
  const endPercent = duration > 0 ? (rangeEnd / duration) * 100 : 100
  const currentPercent = duration > 0 ? (currentTime / duration) * 100 : 0

  return (
    <div className="video-player">
      <div className="video-player__container">
        <video
          ref={videoRef}
          className="video-player__video"
          src={mediaUrl || undefined}
          controls
          preload="metadata"
          onLoadedMetadata={handleLoadedMetadata}
          onTimeUpdate={handleTimeUpdate}
        />
      </div>

      {duration > 0 && (
        <div className="range-selector">
          <div
            className="range-selector__track"
            ref={trackRef}
            onClick={(e) => {
              if (draggingRef.current) return
              const time = positionToTime(e.clientX)
              if (videoRef.current) videoRef.current.currentTime = time
            }}
          >
            <div
              className="range-selector__fill"
              style={{
                left: `${startPercent}%`,
                width: `${endPercent - startPercent}%`
              }}
              onMouseDown={(e) => handleMouseDown('fill', e)}
              onClick={(e) => e.stopPropagation()}
            />
            <div
              className="range-selector__playhead"
              style={{ left: `${currentPercent}%` }}
            />
            <div
              className={`range-selector__handle ${activeHandle === 'start' ? 'range-selector__handle--active' : ''}`}
              style={{ left: `${startPercent}%` }}
              tabIndex={0}
              onMouseDown={(e) => handleMouseDown('start', e)}
              onFocus={() => setActiveHandle('start')}
              onBlur={() => setActiveHandle(null)}
              onKeyDown={handleKeyDown}
            />
            <div
              className={`range-selector__handle ${activeHandle === 'end' ? 'range-selector__handle--active' : ''}`}
              style={{ left: `${endPercent}%` }}
              tabIndex={0}
              onMouseDown={(e) => handleMouseDown('end', e)}
              onFocus={() => setActiveHandle('end')}
              onBlur={() => setActiveHandle(null)}
              onKeyDown={handleKeyDown}
            />
          </div>
          <div className="range-selector__labels">
            <span>{formatTime(rangeStart)}</span>
            <span className="range-selector__duration">
              선택 구간: {formatTime(rangeEnd - rangeStart)}
            </span>
            <span>{formatTime(rangeEnd)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
