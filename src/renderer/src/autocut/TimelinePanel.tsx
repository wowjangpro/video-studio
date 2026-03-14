import { useRef, useCallback, useEffect } from 'react'
import { useAutocutStore } from './autocut-store'
import TimelineRuler from './TimelineRuler'
import SubtitleTrack from './SubtitleTrack'
import VideoTrack from './VideoTrack'
import AudioTrack from './AudioTrack'
import Playhead from './Playhead'

const LABEL_WIDTH = 48

export default function TimelinePanel(): JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const files = useAutocutStore((s) => s.files)
  const totalDuration = useAutocutStore((s) => s.totalDuration)
  const keepSegments = useAutocutStore((s) => s.keepSegments)
  const selectedFileIndex = useAutocutStore((s) => s.selectedFileIndex)
  const playheadPosition = useAutocutStore((s) => s.playheadPosition)
  const timelineZoom = useAutocutStore((s) => s.timelineZoom)
  const setTimelineZoom = useAutocutStore((s) => s.setTimelineZoom)
  const seekTo = useAutocutStore((s) => s.seekTo)

  const contentWidth = LABEL_WIDTH + totalDuration * timelineZoom

  // 플레이헤드가 화면 밖으로 나가면 가운데로 자동 스크롤
  useEffect(() => {
    if (!scrollRef.current) return
    const el = scrollRef.current
    const playheadPx = LABEL_WIDTH + playheadPosition * timelineZoom
    const visibleLeft = el.scrollLeft
    const visibleRight = visibleLeft + el.clientWidth

    if (playheadPx < visibleLeft || playheadPx > visibleRight) {
      el.scrollLeft = playheadPx - el.clientWidth / 2
    }
  }, [playheadPosition, timelineZoom])

  const handleZoomIn = useCallback(() => {
    setTimelineZoom(timelineZoom * 1.5)
  }, [timelineZoom, setTimelineZoom])

  const handleZoomOut = useCallback(() => {
    setTimelineZoom(timelineZoom / 1.5)
  }, [timelineZoom, setTimelineZoom])

  const handleSegmentClick = useCallback(
    (globalTime: number) => {
      seekTo(globalTime)
    },
    [seekTo]
  )

  const handleClipClick = useCallback(
    (_fileIndex: number, globalTime: number) => {
      seekTo(globalTime)
    },
    [seekTo]
  )

  // 타임라인 빈 영역 클릭 → seek
  const handleContentClick = useCallback(
    (e: React.MouseEvent) => {
      if (!contentRef.current) return
      // 자식 블록 클릭은 무시 (블록 자체 핸들러가 처리)
      if ((e.target as HTMLElement).closest('.subtitle-track__block, .video-track__clip, .playhead')) return
      const rect = contentRef.current.getBoundingClientRect()
      const x = e.clientX - rect.left - LABEL_WIDTH
      if (x < 0) return
      const globalTime = Math.max(0, Math.min(totalDuration, x / timelineZoom))
      seekTo(globalTime)
    },
    [totalDuration, timelineZoom, seekTo]
  )

  // Playhead 드래그 seek
  const handlePlayheadSeek = useCallback(
    (globalTime: number) => {
      const clamped = Math.min(totalDuration, globalTime)
      seekTo(clamped)
    },
    [totalDuration, seekTo]
  )

  // non-passive wheel 이벤트로 Ctrl+Wheel 줌 구현
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const handler = (e: WheelEvent): void => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        const delta = e.deltaY > 0 ? 0.8 : 1.25
        const state = useAutocutStore.getState()
        state.setTimelineZoom(state.timelineZoom * delta)
      }
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [])

  return (
    <div className="timeline">
      <div className="timeline__toolbar">
        <button className="btn btn--sm" onClick={handleZoomOut}>
          -
        </button>
        <button className="btn btn--sm" onClick={handleZoomIn}>
          +
        </button>
        <span className="timeline__toolbar-label">
          {Math.round(timelineZoom)}px/s
        </span>
      </div>
      <div
        className="timeline__scroll-container"
        ref={scrollRef}
      >
        <div
          className="timeline__content"
          style={{ width: contentWidth }}
          ref={contentRef}
          onClick={handleContentClick}
        >
          <TimelineRuler
            totalDuration={totalDuration}
            zoom={timelineZoom}
            labelOffset={LABEL_WIDTH}
          />
          <SubtitleTrack
            keepSegments={keepSegments}
            zoom={timelineZoom}
            onClickSegment={handleSegmentClick}
          />
          <VideoTrack
            files={files}
            zoom={timelineZoom}
            selectedFileIndex={selectedFileIndex}
            onClickClip={handleClipClick}
          />
          <AudioTrack
            files={files}
            zoom={timelineZoom}
          />
          <Playhead
            position={playheadPosition}
            zoom={timelineZoom}
            labelOffset={LABEL_WIDTH}
            onSeek={handlePlayheadSeek}
          />
        </div>
      </div>
    </div>
  )
}
