import { useEffect } from 'react'
import { useAppStore } from './common/app-store'
import TabBar from './common/TabBar'
import AutocutModule from './autocut/AutocutModule'
import SubtitleModule from './subtitle/SubtitleModule'
import BgmModule from './bgm/BgmModule'

export default function App(): JSX.Element {
  const activeModule = useAppStore((s) => s.activeModule)
  const setHealth = useAppStore((s) => s.setHealth)
  const conflictMessage = useAppStore((s) => s.conflictMessage)
  const setConflict = useAppStore((s) => s.setConflict)

  // 헬스체크 리스너
  useEffect(() => {
    const cleanup = window.electronAPI.onHealthCheck((status) => {
      setHealth(status.missingItems.length === 0, status.missingItems)
    })
    // 앱 시작 시 헬스체크 실행
    window.electronAPI.checkHealth().then((status) => {
      setHealth(status.missingItems.length === 0, status.missingItems)
    })
    return cleanup
  }, [setHealth])

  return (
    <div className="app">
      <TabBar />
      <main className="app__main">
        <div style={{ display: activeModule === 'autocut' ? 'contents' : 'none' }}>
          <AutocutModule />
        </div>
        <div style={{ display: activeModule === 'subtitle' ? 'contents' : 'none' }}>
          <SubtitleModule />
        </div>
        <div style={{ display: activeModule === 'bgm' ? 'contents' : 'none' }}>
          <BgmModule />
        </div>
      </main>
      {conflictMessage && (
        <div className="conflict-modal">
          <div className="conflict-modal__backdrop" onClick={() => setConflict(null)} />
          <div className="conflict-modal__content">
            <p className="conflict-modal__message">{conflictMessage}</p>
            <div className="conflict-modal__actions">
              <button className="btn btn--primary" onClick={() => setConflict(null)}>확인</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
