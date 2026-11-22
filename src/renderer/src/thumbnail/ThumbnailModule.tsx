import { useCallback, useEffect, useState, DragEvent } from 'react'
import { useThumbnailStore } from './thumbnail-store'
import ThumbCard from './ThumbCard'

function formatDuration(sec: number): string {
  const m = Math.round(sec / 60)
  return m >= 60 ? `${Math.floor(m / 60)}시간 ${m % 60}분` : `${m}분`
}

function FolderDropZone(): JSX.Element {
  const [isDragging, setIsDragging] = useState(false)
  const openFolder = useThumbnailStore((s) => s.openFolder)

  const handleClick = useCallback(async () => {
    const folder = await window.electronAPI.thumbnail.selectFolder()
    if (folder) void openFolder(folder)
  }, [openFolder])

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) void openFolder(window.electronAPI.getPathForFile(file))
    },
    [openFolder]
  )

  return (
    <div
      className={`module-drop-zone ${isDragging ? 'module-drop-zone--active' : ''}`}
      onClick={() => void handleClick()}
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
      onDragLeave={() => setIsDragging(false)}
    >
      <div className="module-drop-zone__icon">🖼️</div>
      <div className="module-drop-zone__text">원본 촬영 폴더를 드래그하거나 클릭하여 선택</div>
      <div className="module-drop-zone__formats">편집하지 않은 원본 영상이 들어 있는 폴더</div>
    </div>
  )
}

function SetupPanel(): JSX.Element {
  const info = useThumbnailStore((s) => s.info)
  const synopsis = useThumbnailStore((s) => s.synopsis)
  const setSynopsis = useThumbnailStore((s) => s.setSynopsis)
  const stage = useThumbnailStore((s) => s.stage)
  const percent = useThumbnailStore((s) => s.percent)
  const message = useThumbnailStore((s) => s.message)
  const errorMessage = useThumbnailStore((s) => s.errorMessage)
  const hasResult = useThumbnailStore((s) => (s.result?.items.length ?? 0) > 0)
  const generate = useThumbnailStore((s) => s.generate)
  const cancel = useThumbnailStore((s) => s.cancel)

  const busy = stage === 'generating'

  const start = (): void => {
    if (hasResult && !window.confirm('지금 있는 썸네일과 제목·설명을 새로 만든 것으로 바꿉니다. 계속할까요?')) return
    void generate()
  }

  return (
    <section className="thumb-setup">
      {info && (
        <p className="thumb-setup__info">
          원본 영상 {info.clipCount}개{info.totalSec ? ` · 총 ${formatDuration(info.totalSec)}` : ''}
          {' · '}
          {info.hasAnalysis
            ? 'AI 편집 장면 분석을 씁니다'
            : '장면 분석이 없어 촬영 전체에서 고르게 뽑은 프레임을 봅니다'}
          {info.hasStory && ' · 스토리 편집 설계 참고'}
        </p>
      )}
      <label className="thumb-setup__label">그날 무슨 일이 있었나요? (시놉시스)</label>
      <textarea
        className="thumb-setup__field"
        value={synopsis}
        onChange={(e) => setSynopsis(e.target.value)}
        placeholder={'3~10줄이면 됩니다. 영상의 핵심 사건과 가장 보여 주고 싶은 장면을 적어 주면 썸네일·제목이 정확해집니다.\n예: 에어펌프를 두고 와서 입으로 텐트를 세웠다'}
        rows={6}
        disabled={busy}
      />
      <div className="thumb-setup__footer">
        <span className="thumb-setup__hint">Claude 호출 2번 · 보통 5~10분 걸립니다</span>
        {busy ? (
          <button className="btn" onClick={() => void cancel()}>취소</button>
        ) : (
          <button className="btn btn--primary" onClick={start}>
            {hasResult ? '다시 만들기' : '썸네일 5장 만들기'}
          </button>
        )}
      </div>
      {busy && (
        <div className="thumb-setup__progress">
          <div className="progress-bar-container">
            <div className="progress-bar" style={{ width: `${Math.max(2, percent)}%` }} />
          </div>
          <p className="thumb-setup__message">{message}</p>
        </div>
      )}
      {stage === 'error' && errorMessage && <div className="thumb-setup__error">{errorMessage}</div>}
    </section>
  )
}

export default function ThumbnailModule(): JSX.Element {
  const folder = useThumbnailStore((s) => s.folder)
  const stage = useThumbnailStore((s) => s.stage)
  const result = useThumbnailStore((s) => s.result)
  const notice = useThumbnailStore((s) => s.notice)
  const setNotice = useThumbnailStore((s) => s.setNotice)
  const setProgress = useThumbnailStore((s) => s.setProgress)
  const reset = useThumbnailStore((s) => s.reset)

  useEffect(() => {
    return window.electronAPI.thumbnail.onProgress((data) => setProgress(data.percent, data.message))
  }, [setProgress])

  // 알림은 잠깐 보여 주고 지운다
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 2500)
    return () => clearTimeout(t)
  }, [notice, setNotice])

  const folderName = folder?.replace(/\/+$/, '').split('/').pop() ?? ''

  return (
    <div className="thumb-module">
      {folder && (
        <div className="module-header">
          <div className="module-header__left">
            <span className="thumb-module__folder" title={folder}>{folderName}</span>
          </div>
          <div className="module-header__right">
            {result && result.items.length > 0 && (
              <button className="btn btn--sm" onClick={() => void window.electronAPI.thumbnail.reveal(folder, 'result.json')}>
                결과 폴더 열기
              </button>
            )}
            <button className="btn btn--sm" onClick={reset}>새 폴더</button>
          </div>
        </div>
      )}

      <div className="thumb-module__content">
        {!folder ? (
          <div className="module-idle">
            <FolderDropZone />
            <div className="module-guide">
              <h3 className="module-guide__title">썸네일 · 제목 만들기</h3>
              <ol className="module-guide__steps">
                <li><strong>원본 폴더 선택</strong> — 편집하지 않은 촬영 원본 폴더를 엽니다 (AI 편집 탭에서 분석한 폴더면 그 장면 분석을 그대로 씁니다)</li>
                <li><strong>시놉시스 확인</strong> — 그날 있었던 일을 몇 줄 적습니다 (AI 편집에서 쓴 시놉시스가 있으면 자동으로 채워집니다)</li>
                <li><strong>썸네일 5장 만들기</strong> — AI가 서로 다른 콘셉트 5개를 정하고, 원본에서 장면을 골라 문구를 얹습니다 <span className="module-guide__model">Claude</span></li>
                <li><strong>고르기 · 다듬기</strong> — 한 장을 고르고, 필요하면 문구·제목·설명을 고쳐 다시 합성합니다. 결과는 원본 폴더의 thumbnail/ 에 저장됩니다</li>
              </ol>
            </div>
          </div>
        ) : stage === 'loading' ? (
          <div className="module-idle">
            <div className="module-drop-zone__spinner" />
          </div>
        ) : (
          <div className="thumb-module__editor">
            <SetupPanel />
            {result && result.items.length > 0 && (
              <div className="thumb-grid">
                {result.items.map((item) => (
                  <ThumbCard key={item.id} item={item} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {notice && <div className="thumb-module__notice">{notice}</div>}
    </div>
  )
}
