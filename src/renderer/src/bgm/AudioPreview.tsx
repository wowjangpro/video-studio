import { useRef, useCallback, useState } from 'react'
import { useBgmStore } from './bgm-store'

export default function AudioPreview(): JSX.Element {
  const videoRef = useRef<HTMLVideoElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  const mediaUrl = useBgmStore((s) => s.mediaUrl)
  const bgmUrls = useBgmStore((s) => s.bgmUrls)
  const bgmPaths = useBgmStore((s) => s.bgmPaths)
  const rangeStart = useBgmStore((s) => s.rangeStart)
  const rangeEnd = useBgmStore((s) => s.rangeEnd)
  const reset = useBgmStore((s) => s.reset)

  const [isPlaying, setIsPlaying] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)

  const play = useCallback(() => {
    const video = videoRef.current
    const audio = audioRef.current
    if (!video || !audio) return

    video.currentTime = rangeStart
    audio.currentTime = 0
    video.play()
    audio.play()
    setIsPlaying(true)
  }, [rangeStart])

  const pause = useCallback(() => {
    videoRef.current?.pause()
    audioRef.current?.pause()
    setIsPlaying(false)
  }, [])

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current
    if (video && video.currentTime >= rangeEnd) {
      pause()
    }
  }, [rangeEnd, pause])

  const handleReanalyze = useCallback(() => {
    useBgmStore.setState({
      stage: 'idle',
      bgmPaths: [],
      bgmUrls: [],
      percent: 0,
      message: ''
    })
  }, [])

  const handleRegenerate = useCallback(() => {
    useBgmStore.setState({
      stage: 'analyzed',
      bgmPaths: [],
      bgmUrls: [],
      percent: 0,
      message: ''
    })
  }, [])

  const handleSelectTrack = useCallback(
    (index: number) => {
      if (isPlaying) pause()
      setSelectedIndex(index)
    },
    [isPlaying, pause]
  )

  return (
    <div className="audio-preview">
      <div className="audio-preview__player">
        <video
          ref={videoRef}
          className="audio-preview__video"
          src={mediaUrl || undefined}
          muted
          preload="metadata"
          onTimeUpdate={handleTimeUpdate}
        />
      </div>

      <audio
        ref={audioRef}
        src={bgmUrls[selectedIndex] || undefined}
        preload="auto"
        onEnded={pause}
      />

      {bgmPaths.length > 1 && (
        <div className="audio-preview__tracks">
          {bgmPaths.map((path, i) => (
            <button
              key={path}
              className={`audio-preview__track ${selectedIndex === i ? 'audio-preview__track--active' : ''}`}
              onClick={() => handleSelectTrack(i)}
            >
              BGM {i + 1}
            </button>
          ))}
        </div>
      )}

      <div className="audio-preview__controls">
        {isPlaying ? (
          <button className="btn btn--primary" onClick={pause}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" />
              <rect x="14" y="4" width="4" height="16" />
            </svg>
            일시정지
          </button>
        ) : (
          <button className="btn btn--primary" onClick={play}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5,3 19,12 5,21" />
            </svg>
            재생
          </button>
        )}
        <button className="btn" onClick={handleRegenerate}>
          다시 생성
        </button>
        <button className="btn" onClick={handleReanalyze}>
          다시 분석
        </button>
        <button className="btn" onClick={reset}>
          새 영상 선택
        </button>
      </div>

      {bgmPaths[selectedIndex] && (
        <p className="audio-preview__path">{bgmPaths[selectedIndex]}</p>
      )}
    </div>
  )
}
