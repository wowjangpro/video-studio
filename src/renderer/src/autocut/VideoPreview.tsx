import { useRef, useEffect, useCallback } from 'react'
import { useAutocutStore } from './autocut-store'

export default function VideoPreview(): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const pendingSeekRef = useRef(false)
  const autoPlayNextRef = useRef(false)
  const mediaUrl = useAutocutStore((s) => s.mediaUrl)
  const seekTime = useAutocutStore((s) => s.seekTime)
  const seekCounter = useAutocutStore((s) => s.seekCounter)
  const selectedFileIndex = useAutocutStore((s) => s.selectedFileIndex)
  const files = useAutocutStore((s) => s.files)
  const setMediaUrl = useAutocutStore((s) => s.setMediaUrl)
  const updatePlayhead = useAutocutStore((s) => s.updatePlayhead)
  const keepSegments = useAutocutStore((s) => s.keepSegments)
  const playheadPosition = useAutocutStore((s) => s.playheadPosition)
  const previewMode = useAutocutStore((s) => s.previewMode)
  const previewPaused = useAutocutStore((s) => s.previewPaused)
  const previewSegmentIndex = useAutocutStore((s) => s.previewSegmentIndex)
  const advancePreview = useAutocutStore((s) => s.advancePreview)
  const pausePreview = useAutocutStore((s) => s.pausePreview)
  const resumePreview = useAutocutStore((s) => s.resumePreview)
  const setVideoPlaying = useAutocutStore((s) => s.setVideoPlaying)
  const setUserPlayback = useAutocutStore((s) => s.setUserPlayback)

  // 파일 선택 시 mediaUrl 로드
  useEffect(() => {
    const file = files[selectedFileIndex]
    if (!file) return
    window.electronAPI.getMediaUrl(file.path).then((url) => {
      setMediaUrl(url)
    })
  }, [selectedFileIndex, files, setMediaUrl])

  // seekTime 변경 시 비디오 이동
  useEffect(() => {
    const video = videoRef.current
    if (!video || seekTime === null) return
    pendingSeekRef.current = true
    const { previewMode } = useAutocutStore.getState()
    const wasPlaying = !video.paused || previewMode || autoPlayNextRef.current
    const doSeek = (): void => {
      video.currentTime = seekTime
      if (wasPlaying) {
        video.play().catch(() => {})
      }
    }
    const onSeeked = (): void => {
      pendingSeekRef.current = false
    }
    video.addEventListener('seeked', onSeeked, { once: true })
    if (video.readyState >= 1) {
      doSeek()
    } else {
      video.addEventListener('loadedmetadata', doSeek, { once: true })
    }
    return () => {
      video.removeEventListener('seeked', onSeeked)
    }
  }, [seekTime, seekCounter])

  // 영상 소스 변경 시 대기 중인 seek 재적용 (파일 전환 시 필수)
  useEffect(() => {
    const video = videoRef.current
    if (!video || !mediaUrl) return
    const onLoaded = (): void => {
      const { seekTime, previewMode } = useAutocutStore.getState()
      if (seekTime !== null) {
        video.currentTime = seekTime
      }
      if (previewMode || autoPlayNextRef.current) {
        autoPlayNextRef.current = false
        video.play().catch(() => {})
      }
      pendingSeekRef.current = false
    }
    video.addEventListener('loadedmetadata', onLoaded, { once: true })
    return () => video.removeEventListener('loadedmetadata', onLoaded)
  }, [mediaUrl])

  // 재생 시작/정지 시 스토어에 반영 + pendingSeek 해제
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onPlaying = (): void => {
      pendingSeekRef.current = false
      setVideoPlaying(true)
    }
    const onPause = (): void => {
      setVideoPlaying(false)
      if (!video.ended && !autoPlayNextRef.current) {
        setUserPlayback(false)
      }
    }
    const onPlay = (): void => {
      if (!autoPlayNextRef.current && !useAutocutStore.getState().previewMode) {
        setUserPlayback(true)
      }
    }
    video.addEventListener('playing', onPlaying)
    video.addEventListener('pause', onPause)
    video.addEventListener('play', onPlay)
    return () => {
      video.removeEventListener('playing', onPlaying)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('play', onPlay)
    }
  }, [setVideoPlaying, setUserPlayback, mediaUrl])

  // previewMode/previewPaused 상태 변경 시 비디오 재생/정지
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (!previewMode) {
      if (!video.paused) video.pause()
      return
    }
    if (previewPaused) {
      video.pause()
    } else {
      video.play().catch(() => {})
    }
  }, [previewPaused, previewMode])

  // 영상 끝 도달 시: 프리뷰 모드 → 다음 구간, 일반 재생 → 다음 파일
  const handleEnded = useCallback(() => {
    const state = useAutocutStore.getState()
    const { previewMode: pm, previewPaused: pp, keepSegments: segs, previewSegmentIndex: idx, files: fs, selectedFileIndex: fi } = state

    // 프리뷰 모드
    if (pm && !pp) {
      const currentSeg = segs[idx]
      const currentFile = fs[fi]
      if (!currentSeg || !currentFile) return

      const fileEndGlobal = currentFile.cumulativeOffset + currentFile.duration

      if (currentSeg.globalEnd > fileEndGlobal + 0.5) {
        pendingSeekRef.current = true
        state.seekTo(fileEndGlobal)
        return
      }

      const result = advancePreview()
      if (result === 'seeked') {
        pendingSeekRef.current = true
      }
      return
    }

    // 일반 재생: 다음 파일로 자동 전환
    if (fi < fs.length - 1) {
      autoPlayNextRef.current = true
      pendingSeekRef.current = true
      state.seekTo(fs[fi + 1].cumulativeOffset)
    }
  }, [advancePreview])

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current
    if (!video || video.seeking || pendingSeekRef.current) return
    const file = files[selectedFileIndex]
    if (!file) return
    const globalTime = file.cumulativeOffset + video.currentTime
    updatePlayhead(globalTime)

    // 프리뷰 모드: 현재 구간 끝에 도달하면 다음 구간으로 이동
    const { previewMode: pm, previewPaused: pp, keepSegments: segs, previewSegmentIndex: idx } = useAutocutStore.getState()
    if (pm && !pp && segs[idx]) {
      if (globalTime >= segs[idx].globalEnd) {
        const result = advancePreview()
        if (!result) {
          video.pause()
        } else if (result === 'seeked') {
          pendingSeekRef.current = true
        }
      }
    }
  }, [files, selectedFileIndex, updatePlayhead, advancePreview])

  // 스페이스바 재생/정지 토글
  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if (e.code !== 'Space') return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      e.preventDefault()
      const video = videoRef.current
      if (!video) return
      const { previewMode: pm, previewPaused: pp } = useAutocutStore.getState()
      if (pm) {
        if (pp) {
          useAutocutStore.getState().resumePreview()
        } else {
          useAutocutStore.getState().pausePreview()
        }
      } else {
        if (video.paused) {
          video.play().catch(() => {})
        } else {
          video.pause()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // playheadPosition 기반으로 KEEP 라벨 찾기 (재생 중 실시간 갱신)
  const activeSegment = keepSegments.find(
    (seg) => playheadPosition >= seg.globalStart && playheadPosition <= seg.globalEnd
  )

  if (!mediaUrl) {
    return (
      <div className="video-preview">
        <div className="video-preview__empty">파일을 선택하세요</div>
      </div>
    )
  }

  return (
    <div className="video-preview">
      <video
        ref={videoRef}
        className="video-preview__player"
        src={mediaUrl}
        controls
        preload="metadata"
        onTimeUpdate={handleTimeUpdate}
        onEnded={handleEnded}
      />
      {activeSegment && (
        <div className="video-preview__label">[{activeSegment.label}]</div>
      )}
    </div>
  )
}
