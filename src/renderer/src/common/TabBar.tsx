import { useAppStore, ModuleName } from './app-store'

const TABS: { id: ModuleName; label: string }[] = [
  { id: 'subtitle', label: '자막 추출' },
  { id: 'bgm', label: 'BGM 생성' },
  { id: 'autocut', label: 'AI 편집' }
]

export default function TabBar(): JSX.Element {
  const activeModule = useAppStore((s) => s.activeModule)
  const moduleStatus = useAppStore((s) => s.moduleStatus)
  const setActiveModule = useAppStore((s) => s.setActiveModule)

  return (
    <div className="tab-bar">
      {TABS.map((tab) => {
        const status = moduleStatus[tab.id]
        return (
          <button
            key={tab.id}
            className={`tab-bar__tab ${activeModule === tab.id ? 'tab-bar__tab--active' : ''}`}
            onClick={() => setActiveModule(tab.id)}
          >
            <span className="tab-bar__label">{tab.label}</span>
            {status === 'running' && <span className="tab-bar__indicator tab-bar__indicator--running" aria-label="실행 중" />}
            {status === 'complete' && <span className="tab-bar__indicator tab-bar__indicator--complete" aria-label="완료" />}
            {status === 'error' && <span className="tab-bar__indicator tab-bar__indicator--error" aria-label="오류" />}
          </button>
        )
      })}
    </div>
  )
}
