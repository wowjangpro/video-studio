import { useAutocutStore } from './autocut-store'

export default function SettingsPanel(): JSX.Element {
  const folderPath = useAutocutStore((s) => s.folderPath)
  const setProgress = useAutocutStore((s) => s.setProgress)
  const setError = useAutocutStore((s) => s.setError)
  const resumeInfo = useAutocutStore((s) => s.resumeInfo)
  const editingComment = useAutocutStore((s) => s.editingComment)
  const setEditingComment = useAutocutStore((s) => s.setEditingComment)
  const aiEngine = useAutocutStore((s) => s.aiEngine)
  const setAiEngine = useAutocutStore((s) => s.setAiEngine)
  const handleStartAnalysis = async (resume = false, forceReanalyze = false): Promise<void> => {
    if (!folderPath) return
    const msg = forceReanalyze ? '전체 재분석 준비 중...' : resume ? '이전 분석 재개 준비 중...' : '분석 준비 중...'
    setProgress('initializing', 0, msg)
    try {
      await window.electronAPI.autocut.startAnalysis(folderPath, {
        window_duration: 10,
        resume,
        force_reanalyze: forceReanalyze,
        editing_comment: editingComment.trim() || undefined,
        ai_engine: aiEngine
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const setResumeInfo = useAutocutStore((s) => s.setResumeInfo)

  const handleFreshStart = async (): Promise<void> => {
    if (!folderPath) return
    if (!window.confirm('이전 분석 데이터가 모두 삭제됩니다. 처음부터 다시 분석하시겠습니까?')) return
    await window.electronAPI.autocut.deleteProgress(folderPath)
    setResumeInfo(null)
  }

  const canResume = resumeInfo?.canResume
  const hasCache = resumeInfo?.hasAnalysisCache

  return (
    <div className="settings-panel settings-panel--compact">
      <div className="settings-panel__row">
        <textarea
          className="settings-panel__comment-input"
          placeholder="편집 코멘트 (선택) — 예: 요리 장면 위주로, 완성본 20분"
          value={editingComment}
          onChange={(e) => setEditingComment(e.target.value)}
          rows={1}
          maxLength={500}
        />
        <div className="settings-panel__controls">
          <select
            className="settings-panel__engine-select"
            value={aiEngine}
            onChange={(e) => setAiEngine(e.target.value as 'ollama' | 'claude')}
          >
            <option value="ollama">Ollama</option>
            <option value="claude">Claude</option>
          </select>
          {hasCache && !canResume && (
            <>
              <button className="btn btn--primary btn--sm" onClick={() => handleStartAnalysis(false)}>
                재편집
              </button>
              <button className="btn btn--sm" onClick={() => {
                if (!window.confirm('이전 분석 데이터를 삭제하고 처음부터 다시 분석하시겠습니까?')) return
                handleStartAnalysis(false, true)
              }}>
                전체 재분석
              </button>
            </>
          )}
          {canResume && (
            <>
              <button className="btn btn--primary btn--sm" onClick={() => handleStartAnalysis(true)}>
                이어서 하기
              </button>
              <button className="btn btn--sm" onClick={handleFreshStart}>
                처음부터
              </button>
            </>
          )}
          {!hasCache && !canResume && (
            <button className="btn btn--primary btn--sm" onClick={() => handleStartAnalysis(false)}>
              분석 시작
            </button>
          )}
        </div>
      </div>
      {(hasCache || canResume) && (
        <div className="settings-panel__status">
          {hasCache && !canResume && '이전 분석 캐시 사용 가능'}
          {canResume && `이전 분석 진행 중 — ${resumeInfo!.completedFiles}/${resumeInfo!.totalFiles} 파일 완료`}
        </div>
      )}
    </div>
  )
}
