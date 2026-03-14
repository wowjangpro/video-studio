import { useRef, useEffect, useState, memo } from 'react'
import type { FileInfo } from './autocut-store'

const CLIP_BG = '#2e7d32'
const WAVE_COLOR = 'rgba(255, 255, 255, 0.85)'
const CLIP_BORDER = '#1b5e20'
const TRACK_HEIGHT = 36

interface WaveformCache {
  [filePath: string]: number[]
}

interface AudioTrackProps {
  files: FileInfo[]
  zoom: number
}

const AudioClip = memo(function AudioClip({ file, zoom, peaks }: { file: FileInfo; zoom: number; peaks: number[] }): JSX.Element | null {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const left = file.cumulativeOffset * zoom + 1
  const width = file.duration * zoom - 2

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || peaks.length === 0 || width < 1) return

    const dpr = window.devicePixelRatio || 1
    const pxWidth = Math.ceil(width)
    canvas.width = pxWidth * dpr
    canvas.height = TRACK_HEIGHT * dpr
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    // 클립 배경
    ctx.fillStyle = CLIP_BG
    ctx.fillRect(0, 0, pxWidth, TRACK_HEIGHT)

    // 테두리
    ctx.strokeStyle = CLIP_BORDER
    ctx.lineWidth = 1
    ctx.strokeRect(0.5, 0.5, pxWidth - 1, TRACK_HEIGHT - 1)

    // 파형
    ctx.fillStyle = WAVE_COLOR
    const peaksPerPixel = peaks.length / pxWidth
    for (let x = 0; x < pxWidth; x++) {
      const startIdx = Math.floor(x * peaksPerPixel)
      const endIdx = Math.min(Math.floor((x + 1) * peaksPerPixel), peaks.length)
      let max = 0
      for (let i = startIdx; i < endIdx; i++) {
        if (peaks[i] > max) max = peaks[i]
      }
      const half = max * (TRACK_HEIGHT / 2 - 2)
      if (half > 0.5) {
        const mid = TRACK_HEIGHT / 2
        ctx.fillRect(x, mid - half, 1, half * 2)
      }
    }
  }, [peaks, width, zoom])

  if (width < 1) return null

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        left,
        top: 0,
        width: Math.ceil(width),
        height: TRACK_HEIGHT
      }}
    />
  )
})

export default function AudioTrack({ files, zoom }: AudioTrackProps): JSX.Element {
  const [waveforms, setWaveforms] = useState<WaveformCache>({})

  useEffect(() => {
    let cancelled = false
    const load = async (): Promise<void> => {
      const cache: WaveformCache = {}
      for (const file of files) {
        if (cancelled) return
        const peaks = await window.electronAPI.autocut.getWaveform(file.path)
        cache[file.path] = peaks
      }
      if (!cancelled) setWaveforms(cache)
    }
    load()
    return () => { cancelled = true }
  }, [files])

  return (
    <div className="audio-track">
      <div className="audio-track__label">A1</div>
      <div className="audio-track__content">
        {files.map((file) => {
          const peaks = waveforms[file.path]
          if (!peaks || peaks.length === 0) return null
          return <AudioClip key={file.path} file={file} zoom={zoom} peaks={peaks} />
        })}
      </div>
    </div>
  )
}
