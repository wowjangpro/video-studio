import { useCallback, useState, DragEvent } from 'react'
import { useSubtitleStore } from './subtitle-store'

export function FileDropZone(): JSX.Element {
  const { setFile, fileName } = useSubtitleStore()
  const [isDragging, setIsDragging] = useState(false)

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setIsDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) {
        setFile(file.path)
      }
    },
    [setFile]
  )

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setIsDragging(false)
  }, [])

  const handleClick = useCallback(async () => {
    const filePath = await window.electronAPI.subtitle.selectFile()
    if (filePath) {
      setFile(filePath)
    }
  }, [setFile])

  return (
    <div
      className={`module-drop-zone ${isDragging ? 'module-drop-zone--active' : ''} ${fileName ? 'module-drop-zone--has-file' : ''}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={handleClick}
    >
      {fileName ? (
        <>
          <div className="module-drop-zone__icon">🎬</div>
          <div className="module-drop-zone__filename">{fileName}</div>
          <div className="module-drop-zone__hint">클릭하여 다른 파일 선택</div>
        </>
      ) : (
        <>
          <div className="module-drop-zone__icon">📁</div>
          <div className="module-drop-zone__text">영상 파일을 드래그하거나 클릭하여 선택하세요</div>
          <div className="module-drop-zone__formats">mp4, mkv, avi, mov, wmv, flv, webm, ts, m4v</div>
        </>
      )}
    </div>
  )
}
