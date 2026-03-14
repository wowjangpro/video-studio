import { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { useSubtitleStore } from './subtitle-store'

export function SubtitleVideoPreview(): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const { mediaUrl, stage, chunkStart, segments, translatedSegments, selectedLang, seekTime, seekEndTime, seekId, setActiveSegmentId } = useSubtitleStore()

  const displaySegments = useMemo(() => {
    if (selectedLang === 'ko') return segments
    const translated = translatedSegments[selectedLang]
    return translated.length > 0 ? translated : segments
  }, [segments, translatedSegments, selectedLang])
  const [timeBasedSubtitle, setTimeBasedSubtitle] = useState('')
  const [userSeeking, setUserSeeking] = useState(false)
  const prevSegCount = useRef(0)
  const lastAutoSeek = useRef(0)
  const playEndTime = useRef<number | null>(null)

  const isTranscribing = stage === 'transcribing'

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    if (stage === 'correcting' || stage === 'complete') {
      video.pause()
    }
    if (stage !== 'transcribing') {
      prevSegCount.current = 0
      lastAutoSeek.current = 0
    }
  }, [stage])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !isTranscribing || userSeeking) return

    if (segments.length > prevSegCount.current) {
      prevSegCount.current = segments.length
      const lastSeg = segments[segments.length - 1]
      video.currentTime = lastSeg.start
      playEndTime.current = lastSeg.end
      video.muted = false
      video.play()
    }
  }, [segments.length, isTranscribing, userSeeking])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !isTranscribing || userSeeking) return
    if (segments.length > 0) return
    if (chunkStart <= 0 || chunkStart === lastAutoSeek.current) return

    lastAutoSeek.current = chunkStart
    video.currentTime = chunkStart
  }, [chunkStart, isTranscribing, userSeeking, segments.length])

  useEffect(() => {
    const video = videoRef.current
    if (!video || seekTime === null || seekId === 0) return

    video.muted = false
    video.currentTime = seekTime
    playEndTime.current = seekEndTime
    video.play()
  }, [seekId])

  const handleSeeking = useCallback(() => {
    setUserSeeking(true)
  }, [])

  const handleSeeked = useCallback(() => {
    setTimeout(() => setUserSeeking(false), 2000)
  }, [])

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current
    if (!video) return

    if (playEndTime.current !== null && video.currentTime >= playEndTime.current) {
      video.pause()
      playEndTime.current = null
    }

    if (isTranscribing) return
    if (displaySegments.length === 0) {
      setTimeBasedSubtitle('')
      return
    }

    const t = video.currentTime
    let lo = 0
    let hi = displaySegments.length - 1
    let found = ''
    let foundId: number | null = null

    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      const seg = displaySegments[mid]
      if (t < seg.start) {
        hi = mid - 1
      } else if (t > seg.end) {
        lo = mid + 1
      } else {
        found = seg.correctedText || seg.text
        foundId = seg.id
        break
      }
    }

    setTimeBasedSubtitle(found)
    setActiveSegmentId(foundId)
  }, [displaySegments, isTranscribing, setActiveSegmentId])

  const isProcessing = stage === 'extracting' || isTranscribing || stage === 'correcting'
  const lastSeg = segments.length > 0 ? segments[segments.length - 1] : null
  const overlayText = isProcessing
    ? (lastSeg?.correctedText || lastSeg?.text || '')
    : timeBasedSubtitle

  if (!mediaUrl) return <div className="sub-video-preview sub-video-preview--empty" />

  return (
    <div className="sub-video-preview">
      <video
        ref={videoRef}
        className="sub-video-preview__player"
        src={mediaUrl}
        controls
        preload="metadata"
        onSeeking={handleSeeking}
        onSeeked={handleSeeked}
        onTimeUpdate={handleTimeUpdate}
      />
      {(overlayText || isProcessing) && (
        <div className="sub-video-preview__subtitle">
          {overlayText || '자막 생성 대기 중...'}
        </div>
      )}
      {isProcessing && (
        <div className="sub-video-preview__status">
          {segments.length > 0
            ? `${segments.length}개 자막`
            : isTranscribing ? '음성인식 중...' : stage === 'correcting' ? '자막 교정 중...' : '오디오 추출 중...'
          }
        </div>
      )}
    </div>
  )
}
