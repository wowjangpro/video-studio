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
  const targetMinutes = useAutocutStore((s) => s.targetMinutes)
  const updateSettings = useAutocutStore((s) => s.updateSettings)
  const handleStartAnalysis = async (resume = false, forceReanalyze = false): Promise<void> => {
    if (!folderPath) return
    if (aiEngine === 'story' && !editingComment.trim()) {
      // 시놉시스 없이 돌리면 수 시간짜리 분석 뒤에야 실패한다 — 시작 전에 막는다
      setError('스토리 엔진은 시놉시스가 필요합니다 — 그날 무슨 일이 있었는지 순서대로 적어주세요 (3~10줄).')
      return
    }
    const msg = forceReanalyze ? '전체 재분석 준비 중...' : resume ? '이전 분석 재개 준비 중...' : '분석 준비 중...'
    setProgress('initializing', 0, msg)
    try {
      await window.electronAPI.autocut.startAnalysis(folderPath, {
        window_duration: 10,
        resume,
        force_reanalyze: forceReanalyze,
        // 스토리 엔진은 같은 입력칸을 시놉시스(최대 2000자)로, 나머지는 편집 코멘트(500자)로 쓴다
        editing_comment: aiEngine === 'story' ? undefined : editingComment.trim().slice(0, 500) || undefined,
        synopsis: aiEngine === 'story' ? editingComment.trim() || undefined : undefined,
        ai_engine: aiEngine,
        target_minutes: targetMinutes || undefined
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
  const isStory = aiEngine === 'story'

  return (
    <div className="settings-panel settings-panel--compact">
      <div className="settings-panel__row">
        <textarea
          className="settings-panel__comment-input"
          placeholder={
            isStory
              ? '시놉시스 (필수) — 그날 무슨 일이 있었는지 순서대로 3~10줄. 예: 에어펌프를 안 가져와서 입으로 불어 텐트를 세웠다 → 저녁 → 불멍 → 밤새 비 → 맑은 아침'
              : '편집 코멘트 (선택) — 예: 요리 장면 위주로, 완성본 20분'
          }
          value={editingComment}
          onChange={(e) => setEditingComment(e.target.value)}
          rows={isStory ? 4 : 1}
          maxLength={isStory ? 2000 : 500}
        />
        <div className="settings-panel__controls">
          <label className="settings-panel__target" title="완성본 목표 시간(분). 0이면 자동(50~70%).">
            목표
            <input
              type="number"
              className="settings-panel__target-input"
              min={0}
              max={300}
              step={5}
              value={targetMinutes || ''}
              placeholder="자동"
              onChange={(e) => {
                const v = parseInt(e.target.value, 10)
                updateSettings({ targetMinutes: Number.isFinite(v) ? v : 0 })
              }}
            />
            분
          </label>
          <select
            className="settings-panel__engine-select"
            value={aiEngine}
            onChange={(e) => setAiEngine(e.target.value as 'claude' | 'scored' | 'story')}
          >
            <option value="story">스토리 (시놉시스 기반)</option>
            <option value="claude">Claude (1차 편집)</option>
            <option value="scored">Score 기반 (LLM 없음)</option>
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
