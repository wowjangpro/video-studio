import { useCallback } from 'react'

interface PlayheadProps {
  position: number
  zoom: number
  labelOffset: number
  onSeek: (globalTime: number) => void
}

export default function Playhead({ position, zoom, labelOffset, onSeek }: PlayheadProps): JSX.Element {
  const left = labelOffset + position * zoom

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      document.body.style.cursor = 'ew-resize'

      const handleMove = (ev: MouseEvent): void => {
        const contentEl = (e.target as HTMLElement).closest('.timeline__content')
        if (!contentEl) return
        const rect = contentEl.getBoundingClientRect()
        const x = ev.clientX - rect.left - labelOffset
        const globalTime = Math.max(0, x / zoom)
        onSeek(globalTime)
      }

      const handleUp = (): void => {
        document.body.style.cursor = ''
        document.removeEventListener('mousemove', handleMove)
        document.removeEventListener('mouseup', handleUp)
      }

      document.addEventListener('mousemove', handleMove)
      document.addEventListener('mouseup', handleUp)
    },
    [zoom, labelOffset, onSeek]
  )

  return (
    <div className="playhead" style={{ left }} onMouseDown={handleMouseDown}>
      <div className="playhead__head" />
    </div>
  )
}
