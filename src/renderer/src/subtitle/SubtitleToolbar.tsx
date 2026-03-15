import { useState, useCallback, useEffect } from 'react'
import { useSubtitleStore, type SubtitleLang } from './subtitle-store'

const LANG_LABELS: Record<SubtitleLang, string> = {
  ko: '한국어',
  en: 'English',
  jp: '日本語'
}

export function SubtitleToolbar(): JSX.Element {
  const {
    segments,
    translatedSegments,
    selectedLang,
    setSelectedLang,
    srtPath,
    videoDescription,
    setVideoDescription
  } = useSubtitleStore()

  const [searchQuery, setSearchQuery] = useState('')
  const [translating, setTranslating] = useState(false)
  const [translateMsg, setTranslateMsg] = useState('')
  const [aiEngine, setAiEngine] = useState<'ollama' | 'claude'>(() => {
    const saved = localStorage.getItem('subtitle:aiEngine')
    return saved === 'ollama' ? 'ollama' : 'claude'
  })

  useEffect(() => {
    window.dispatchEvent(new CustomEvent('subtitle-search', { detail: searchQuery }))
  }, [searchQuery])

  useEffect(() => {
    const unsub = window.electronAPI.subtitle.onTranslateProgress((data) => {
      setTranslateMsg(data.message)
      if (data.percent === 100 || data.percent === -1) {
        setTranslating(false)
        setTimeout(() => setTranslateMsg(''), 5000)
      }
    })
    return unsub
  }, [])

  const activeSegments = selectedLang === 'ko'
    ? segments
    : (translatedSegments[selectedLang].length > 0 ? translatedSegments[selectedLang] : segments)

  const handleExport = async (): Promise<void> => {
    const result = await window.electronAPI.subtitle.saveSrt(activeSegments)
    if (result) {
      alert(`저장 완료: ${result}`)
    }
  }

  const handleTranslate = useCallback(async (lang: 'en' | 'jp') => {
    if (!srtPath || translating) return
    setTranslating(true)
    setTranslateMsg(`${lang === 'en' ? '영어' : '일본어'} 번역 준비 중...`)
    const input = segments.map((s) => ({
      id: s.id,
      start: s.start,
      end: s.end,
      text: s.correctedText || s.text
    }))
    await window.electronAPI.subtitle.translateSubtitles(input, lang, srtPath, videoDescription, aiEngine)
  }, [segments, srtPath, translating, videoDescription, aiEngine])

  const hasEn = translatedSegments.en.length > 0
  const hasJp = translatedSegments.jp.length > 0

  return (
    <div className="sub-toolbar">
      <div className="sub-toolbar__row">
        <div className="sub-editor__lang-group">
          {(['ko', 'en', 'jp'] as SubtitleLang[]).map((lang) => {
            const available = lang === 'ko' || (lang === 'en' && hasEn) || (lang === 'jp' && hasJp)
            if (!available) return null
            return (
              <button
                key={lang}
                className={`btn btn--sm${selectedLang === lang ? ' btn--primary' : ''}`}
                onClick={() => setSelectedLang(lang)}
              >
                {LANG_LABELS[lang]}
              </button>
            )
          })}
        </div>
        <div className="sub-editor__info">{activeSegments.length}개 자막</div>
        <input
          type="text"
          className="sub-editor__search"
          placeholder="자막 검색..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <button className="btn btn--sm btn--primary" onClick={handleExport}>
          SRT 저장
        </button>
        <div className="sub-toolbar__sep" />
        <input
          type="text"
          className="sub-editor__desc"
          placeholder="영상 설명 (예: 캠핑 브이로그, 남성 1인)"
          value={videoDescription}
          onChange={(e) => setVideoDescription(e.target.value)}
        />
        <select
          className="sub-toolbar__engine-select"
          value={aiEngine}
          onChange={(e) => {
            const v = e.target.value as 'ollama' | 'claude'
            setAiEngine(v)
            localStorage.setItem('subtitle:aiEngine', v)
          }}
        >
          <option value="ollama">Ollama</option>
          <option value="claude">Claude</option>
        </select>
        <button className="btn btn--sm" onClick={() => handleTranslate('en')} disabled={translating}>
          {hasEn ? '영어 재번역' : '영어 번역'}
        </button>
        <button className="btn btn--sm" onClick={() => handleTranslate('jp')} disabled={translating}>
          {hasJp ? '일본어 재번역' : '일본어 번역'}
        </button>
        {translateMsg && <span className="sub-editor__translate-msg">{translateMsg}</span>}
      </div>
    </div>
  )
}
