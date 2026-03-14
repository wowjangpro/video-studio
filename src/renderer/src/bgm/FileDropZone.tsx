import { useState, useCallback, DragEvent } from 'react'
import { useBgmStore } from './bgm-store'

const VIDEO_EXTENSIONS = ['mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v']

export default function FileDropZone(): JSX.Element {
  const [isDragging, setIsDragging] = useState(false)
  const setFile = useBgmStore((s) => s.setFile)

  const handleFile = useCallback(
    (filePath: string) => {
      const ext = filePath.split('.').pop()?.toLowerCase() || ''
      if (VIDEO_EXTENSIONS.includes(ext)) {
        setFile(filePath)
      }
    },
    [setFile]
  )

  const handleClick = useCallback(async () => {
    const filePath = await window.electronAPI.bgm.selectFile()
    if (filePath) {
      handleFile(filePath)
    }
  }, [handleFile])

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) {
        handleFile(file.path)
      }
    },
    [handleFile]
  )

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setIsDragging(false)
  }, [])

  return (
    <div
      className={`drop-zone ${isDragging ? 'drop-zone--active' : ''}`}
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      <div className="drop-zone__icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M15 10l-4 4l6 6l4-16l-18 7l6 2l2 6l4-6" />
        </svg>
      </div>
      <p className="drop-zone__title">영상 파일을 드래그하거나 클릭하여 선택</p>
      <p className="drop-zone__subtitle">mp4, mkv, avi, mov, webm, m4v</p>
    </div>
  )
}
