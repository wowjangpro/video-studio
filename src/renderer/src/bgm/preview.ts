import { create } from 'zustand'
import { useLibraryStore } from './library-store'

/** VideoPlayer 가 자기 <video> 를 여기에 등록한다 (미리듣기가 영상과 함께 재생하려고) */
export const player: { video: HTMLVideoElement | null } = { video: null }

/** 경로의 #, ?, 공백 등이 URL 로 오해되지 않도록 구간별로 인코딩한다 */
export function toFileUrl(absPath: string): string {
  return 'file://' + absPath.split('/').map(encodeURIComponent).join('/')
}

export function formatTime(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return '--:--'
  const total = Math.max(0, Math.round(sec))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

interface PreviewRequest {
  key: string
  audioPath: string
  offsetSec: number
  rangeStart: number
  rangeEnd: number
}

interface PreviewState {
  playingKey: string | null
  play: (req: PreviewRequest) => void
  stop: () => void
}

const audio = new Audio()
// 영상 원래 소리는 작게 깔고 음악을 크게 들려준다
const VIDEO_VOLUME_DURING_PREVIEW = 0.25

/**
 * 지금 미리듣기가 건드린 것들. play() 를 시작하는 순간 채운다 —
 * 음악 재생이 실패해도(파일 없음·디스크 빠짐·다른 곡으로 바꿈) stop() 이 영상을 원래대로 돌려놓을 수 있게.
 */
let session: { token: number; video: HTMLVideoElement | null; volume: number; cleanup: () => void } | null = null
let nextToken = 0

export const usePreview = create<PreviewState>((set) => {
  const stop = (): void => {
    const s = session
    session = null
    audio.pause()
    if (s) {
      s.cleanup()
      if (s.video) {
        s.video.pause()
        s.video.volume = s.volume
      }
    }
    set({ playingKey: null })
  }

  audio.addEventListener('ended', () => stop())

  return {
    playingKey: null,
    stop,
    play: (req) => {
      stop()
      useLibraryStore.getState().setPlaying(null) // 라이브러리 미리듣기와 겹치지 않게
      const token = ++nextToken
      const v = player.video
      const cleanups: (() => void)[] = []
      session = { token, video: v, volume: v ? v.volume : 1, cleanup: () => cleanups.forEach((f) => f()) }

      // 이 요청이 아직 현재일 때만 멈춘다 (늦게 도착한 이전 요청의 실패가 새 미리듣기를 끄지 않게)
      const stopIfCurrent = (): void => {
        if (session?.token === token) stop()
      }
      const onError = (): void => stopIfCurrent()
      audio.addEventListener('error', onError)
      cleanups.push(() => audio.removeEventListener('error', onError))

      audio.src = toFileUrl(req.audioPath)
      audio.currentTime = req.offsetSec
      if (v) {
        v.currentTime = req.rangeStart
        v.volume = VIDEO_VOLUME_DURING_PREVIEW
        const onTime = (): void => {
          if (v.currentTime >= req.rangeEnd || v.currentTime < req.rangeStart - 0.5) stopIfCurrent()
        }
        // 사용자가 영상을 멈추면 음악도 같이 멈춘다
        const onPause = (): void => stopIfCurrent()
        v.addEventListener('timeupdate', onTime)
        v.addEventListener('pause', onPause)
        cleanups.push(() => {
          v.removeEventListener('timeupdate', onTime)
          v.removeEventListener('pause', onPause)
        })
        void v.play().catch(() => undefined)
      } else {
        // 영상이 없으면 구간 길이만큼만 음악을 들려준다
        const t = setTimeout(stopIfCurrent, (req.rangeEnd - req.rangeStart) * 1000)
        cleanups.push(() => clearTimeout(t))
      }
      audio.play().then(
        () => {
          if (session?.token === token) set({ playingKey: req.key })
        },
        () => stopIfCurrent()
      )
    }
  }
})
