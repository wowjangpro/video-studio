import { useEffect } from 'react'
import { useBgmStore } from './bgm-store'
import { usePreview } from './preview'
import { useLibraryStore } from './library-store'
import { useAppStore } from '../common/app-store'
import FileDropZone from './FileDropZone'
import VideoPlayer from './VideoPlayer'
import RecommendPanel from './RecommendPanel'
import CuePanel from './CuePanel'
import MusicLibraryPanel from './MusicLibraryPanel'

export default function BgmModule(): JSX.Element {
  const filePath = useBgmStore((s) => s.filePath)
  const fileName = useBgmStore((s) => s.fileName)
  const notice = useBgmStore((s) => s.notice)
  const setMediaUrl = useBgmStore((s) => s.setMediaUrl)
  const setProgress = useBgmStore((s) => s.setProgress)
  const setNotice = useBgmStore((s) => s.setNotice)
  const reset = useBgmStore((s) => s.reset)
  const stopPreview = usePreview((s) => s.stop)

  useEffect(() => {
    if (!filePath) return
    window.electronAPI.getMediaUrl(filePath).then(setMediaUrl)
  }, [filePath, setMediaUrl])

  useEffect(() => {
    return window.electronAPI.bgm.onProgress((data) => setProgress(data.percent, data.message))
  }, [setProgress])

  // 알림은 잠깐 보여 주고 지운다
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 2500)
    return () => clearTimeout(t)
  }, [notice, setNotice])

  // 탭은 숨겨질 뿐 언마운트되지 않는다 → 다른 탭으로 가면 미리듣기(장면·라이브러리)를 직접 멈춘다
  const activeModule = useAppStore((s) => s.activeModule)
  useEffect(() => {
    if (activeModule !== 'bgm') {
      stopPreview()
      useLibraryStore.getState().setPlaying(null)
    }
  }, [activeModule, stopPreview])

  return (
    <div className="bgm-module bgm-module--with-library">
      <div className="bgm-module__main">
        {filePath && (
          <div className="module-header">
            <div className="module-header__left">
              <span className="bgm-module__file" title={filePath}>{fileName}</span>
            </div>
            <div className="module-header__right">
              <button className="btn btn--sm" onClick={() => { stopPreview(); reset() }}>
                새 파일
              </button>
            </div>
          </div>
        )}

        <div className="bgm-module__content">
          {!filePath ? (
            <div className="module-idle">
              <FileDropZone />
              <div className="module-guide">
                <h3 className="module-guide__title">BGM 추천 워크플로우</h3>
                <ol className="module-guide__steps">
                  <li><strong>라이브러리 선택</strong> — 오른쪽에서 유튜브 오디오 보관함 라이브러리 폴더를 고릅니다</li>
                  <li><strong>영상 선택</strong> — 다빈치에서 렌더링한 1차 편집 영상을 엽니다</li>
                  <li><strong>구간 설정</strong> — 음악을 깔 구간을 지정합니다</li>
                  <li><strong>곡 찾기</strong> — AI가 장면을 보고 라이브러리에서 어울리는 곡과 시작 지점을 고릅니다 <span className="module-guide__model">Claude + CLAP</span></li>
                  <li><strong>미리듣기 · 넣기</strong> — 장면과 함께 들어 보고 구간에 넣습니다 (여러 구간 반복)</li>
                  <li><strong>다빈치용 내보내기</strong> — 영상 + 음악이 배치된 타임라인(FCPXML)과 유튜브 출처 문구를 만듭니다</li>
                </ol>
              </div>
            </div>
          ) : (
            <div className="bgm-module__editor">
              <VideoPlayer />
              <RecommendPanel />
              <CuePanel />
            </div>
          )}
        </div>
        {notice && <div className="bgm-module__notice">{notice}</div>}
      </div>

      <MusicLibraryPanel />
    </div>
  )
}
