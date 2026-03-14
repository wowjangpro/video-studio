import { useState, useCallback } from 'react'

interface TranslatedContent {
  title: string
  description: string
}

export function DescriptionTranslator(): JSX.Element {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [translated, setTranslated] = useState<Record<string, TranslatedContent>>({})
  const [translating, setTranslating] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  const handleFetch = useCallback(async () => {
    if (!url.trim()) return
    setLoading(true)
    setError(null)
    setTitle('')
    setDescription('')
    setTranslated({})

    try {
      const info = await window.electronAPI.subtitle.getYoutubeInfo(url.trim())
      if (!info) {
        setError('유효하지 않은 YouTube URL입니다.')
        setLoading(false)
        return
      }
      setTitle(info.title)
      setDescription(info.description || '')
    } catch {
      setError('영상 정보를 가져올 수 없습니다.')
    }
    setLoading(false)
  }, [url])

  const handleTranslate = useCallback(async (lang: string) => {
    if (!title || translating) return
    setTranslating(lang)
    setError(null)

    try {
      const result = await window.electronAPI.subtitle.translateDescription(title, description, lang)
      if (result) {
        setTranslated((prev) => ({ ...prev, [lang]: result }))
      } else {
        setError('번역에 실패했습니다. Ollama가 실행 중인지 확인하세요.')
      }
    } catch {
      setError('번역 중 오류가 발생했습니다.')
    }
    setTranslating(null)
  }, [title, description, translating])

  const handleCopy = useCallback(async (text: string, key: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 2000)
  }, [])

  const hasContent = !!title

  return (
    <div className="sub-desc-translator">
      <div className="sub-desc-translator__url-group">
        <input
          className="sub-url-input"
          type="text"
          placeholder="YouTube URL 붙여넣기"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleFetch() }}
        />
        <button
          className="btn btn--primary"
          onClick={handleFetch}
          disabled={!url.trim() || loading}
        >
          {loading ? '가져오는 중...' : '가져오기'}
        </button>
      </div>

      {error && <div className="sub-desc-translator__error">{error}</div>}

      {hasContent && (
        <div className="sub-desc-translator__content">
          <div className="sub-desc-translator__section">
            <div className="sub-desc-translator__section-header">
              <h3>원본 (한국어)</h3>
              <button
                className="btn btn--sm"
                onClick={() => handleCopy(`${title}\n\n${description}`, 'ko')}
              >
                {copied === 'ko' ? '복사됨!' : '복사'}
              </button>
            </div>
            <div className="sub-desc-translator__title-edit">
              <input
                type="text"
                className="sub-desc-translator__title-input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <span className="sub-desc-translator__char-count">{title.length}/100</span>
            </div>
            <textarea
              className="sub-desc-translator__desc-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={8}
            />
          </div>

          <div className="sub-desc-translator__actions">
            <button
              className="btn btn--primary"
              onClick={() => handleTranslate('en')}
              disabled={!!translating}
            >
              {translating === 'en' ? '번역 중...' : (translated.en ? '영어 재번역' : '영어 번역')}
            </button>
            <button
              className="btn btn--primary"
              onClick={() => handleTranslate('jp')}
              disabled={!!translating}
            >
              {translating === 'jp' ? '번역 중...' : (translated.jp ? '일본어 재번역' : '일본어 번역')}
            </button>
          </div>

          {(['en', 'jp'] as const).map((lang) => {
            const t = translated[lang]
            if (!t) return null
            const label = lang === 'en' ? 'English' : '日本語'
            const titleLen = t.title.length
            const titleOver = titleLen > 100
            return (
              <div key={lang} className="sub-desc-translator__section">
                <div className="sub-desc-translator__section-header">
                  <h3>{label}</h3>
                  <button
                    className="btn btn--sm"
                    onClick={() => handleCopy(`${t.title}\n\n${t.description}`, lang)}
                  >
                    {copied === lang ? '복사됨!' : '복사'}
                  </button>
                </div>
                <div className="sub-desc-translator__title-edit">
                  <div className={`sub-desc-translator__title-readonly${titleOver ? ' sub-desc-translator__title-readonly--over' : ''}`}>
                    {t.title}
                  </div>
                  <span className={`sub-desc-translator__char-count${titleOver ? ' sub-desc-translator__char-count--over' : ''}`}>
                    {titleLen}/100
                  </span>
                </div>
                <div className="sub-desc-translator__text">{t.description}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
