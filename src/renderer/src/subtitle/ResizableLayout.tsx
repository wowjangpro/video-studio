import { useRef, useCallback, useState, type ReactNode } from 'react'

interface Props {
  left: ReactNode
  right: ReactNode
  initialRatio?: number
  minRatio?: number
  maxRatio?: number
}

export function SubtitleResizableLayout({
  left,
  right,
  initialRatio = 0.6,
  minRatio = 0.3,
  maxRatio = 0.8
}: Props): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const [ratio, setRatio] = useState(initialRatio)
  const dragging = useRef(false)

  const rafRef = useRef(0)

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragging.current = true

      const onMouseMove = (ev: MouseEvent): void => {
        if (!dragging.current || !containerRef.current) return
        cancelAnimationFrame(rafRef.current)
        rafRef.current = requestAnimationFrame(() => {
          if (!containerRef.current) return
          const rect = containerRef.current.getBoundingClientRect()
          let newRatio = (ev.clientX - rect.left) / rect.width
          if (newRatio < minRatio) newRatio = minRatio
          if (newRatio > maxRatio) newRatio = maxRatio
          setRatio(newRatio)
        })
      }

      const onMouseUp = (): void => {
        dragging.current = false
        cancelAnimationFrame(rafRef.current)
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }

      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    },
    [minRatio, maxRatio]
  )

  const leftPercent = `${ratio * 100}%`
  const rightPercent = `${(1 - ratio) * 100}%`

  return (
    <div ref={containerRef} className="sub-resizable-layout">
      <div className="sub-resizable-layout__left" style={{ width: leftPercent }}>
        {left}
      </div>
      <div className="sub-resizable-layout__divider" onMouseDown={handleMouseDown} />
      <div className="sub-resizable-layout__right" style={{ width: rightPercent }}>
        {right}
      </div>
    </div>
  )
}
