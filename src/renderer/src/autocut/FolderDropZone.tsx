import { useState, useCallback } from 'react'
import { useAutocutStore } from './autocut-store'

export default function FolderDropZone(): JSX.Element {
  const [isDragOver, setIsDragOver] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const setFolder = useAutocutStore((s) => s.setFolder)

  const handleClick = useCallback(async () => {
    if (isLoading) return
    setIsLoading(true)
    try {
      const result = await window.electronAPI.autocut.selectFolder()
      if (result) {
        setFolder(result.folderPath, result.files, result.resumeInfo)
      }
    } finally {
      setIsLoading(false)
    }
  }, [setFolder, isLoading])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false)
  }, [])

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragOver(false)
      if (isLoading) return
      const items = e.dataTransfer.files
      if (items.length > 0) {
        const path = (items[0] as unknown as { path: string }).path
        if (path) {
          setIsLoading(true)
          try {
            const result = await window.electronAPI.autocut.scanFolder(path)
            if (result) {
              setFolder(result.folderPath, result.files, result.resumeInfo)
            }
          } finally {
            setIsLoading(false)
          }
        }
      }
    },
    [setFolder, isLoading]
  )

  if (isLoading) {
    return (
      <div className="module-drop-zone module-drop-zone--loading">
        <div className="module-drop-zone__spinner" />
        <div className="module-drop-zone__text">영상 파일 스캔 중...</div>
        <div className="module-drop-zone__formats">썸네일 생성 및 파일 정보를 읽고 있습니다</div>
      </div>
    )
  }

  return (
    <div
      className={`module-drop-zone ${isDragOver ? 'module-drop-zone--active' : ''}`}
      onClick={handleClick}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="module-drop-zone__icon">📁</div>
      <div className="module-drop-zone__text">캠핑 영상 폴더를 드래그하거나 클릭하여 선택</div>
      <div className="module-drop-zone__formats">mp4, mov, mkv 등 영상 파일이 포함된 폴더</div>
    </div>
  )
}
