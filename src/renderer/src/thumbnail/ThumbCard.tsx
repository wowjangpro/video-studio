import { useState } from 'react'
import type { ThumbColor, ThumbFont, ThumbItem, ThumbPosition, ThumbText } from './types'
import { useThumbnailStore } from './thumbnail-store'

const POSITION_LABEL: Record<ThumbPosition, string> = {
  'top-left': '위 왼쪽',
  'top-center': '위 가운데',
  'top-right': '위 오른쪽',
  'bottom-left': '아래 왼쪽',
  'bottom-center': '아래 가운데',
  'bottom-right': '아래 오른쪽'
}
const COLOR_LABEL: Record<ThumbColor, string> = {
  yellow: '노랑',
  red: '빨강',
  orange: '주황',
  cyan: '하늘',
  green: '초록',
  white: '흰색'
}
const FONT_LABEL: Record<ThumbFont, string> = {
  dohyeon: '도현 (굵고 단단함)',
  hanna: '한나 (둥글고 친근함)',
  ssurround: '써라운드 (통통함)',
  gothic: '고딕 (깔끔함)'
}

function formatTime(sec: number): string {
  const s = Math.floor(sec)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function EditForm({ item, onDone }: { item: ThumbItem; onDone: () => void }): JSX.Element {
  const compose = useThumbnailStore((s) => s.compose)
  const busy = useThumbnailStore((s) => s.busyId === item.id)
  const [text, setText] = useState<ThumbText>(item.text)
  const [title, setTitle] = useState(item.title)
  const [description, setDescription] = useState(item.description)
  const [hashtags, setHashtags] = useState(item.hashtags.join(' '))

  const set = <K extends keyof ThumbText>(key: K, value: ThumbText[K]): void => setText((t) => ({ ...t, [key]: value }))

  const save = async (): Promise<void> => {
    const ok = await compose(item.id, {
      ...text,
      title,
      description,
      hashtags: hashtags.split(/\s+/).filter(Boolean)
    })
    if (ok) onDone()
  }

  return (
    <div className="thumb-edit">
      <label className="thumb-edit__label">큰 문구 (줄바꿈 = 두 줄)</label>
      <textarea className="thumb-edit__field" rows={2} value={text.headline} onChange={(e) => set('headline', e.target.value)} />
      <div className="thumb-edit__row">
        <div>
          <label className="thumb-edit__label">보조 문구</label>
          <input className="thumb-edit__field" value={text.subline} onChange={(e) => set('subline', e.target.value)} />
        </div>
        <div>
          <label className="thumb-edit__label">강조할 단어</label>
          <input className="thumb-edit__field" value={text.highlight} onChange={(e) => set('highlight', e.target.value)} />
        </div>
      </div>
      <div className="thumb-edit__row thumb-edit__row--3">
        <div>
          <label className="thumb-edit__label">위치</label>
          <select className="thumb-edit__field" value={text.position} onChange={(e) => set('position', e.target.value as ThumbPosition)}>
            {Object.entries(POSITION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div>
          <label className="thumb-edit__label">강조색</label>
          <select className="thumb-edit__field" value={text.color} onChange={(e) => set('color', e.target.value as ThumbColor)}>
            {Object.entries(COLOR_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div>
          <label className="thumb-edit__label">글꼴</label>
          <select className="thumb-edit__field" value={text.font} onChange={(e) => set('font', e.target.value as ThumbFont)}>
            {Object.entries(FONT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      <label className="thumb-edit__label">제목</label>
      <input className="thumb-edit__field" value={title} onChange={(e) => setTitle(e.target.value)} />
      <label className="thumb-edit__label">설명</label>
      <textarea className="thumb-edit__field" rows={5} value={description} onChange={(e) => setDescription(e.target.value)} />
      <label className="thumb-edit__label">해시태그 (띄어쓰기로 구분)</label>
      <input className="thumb-edit__field" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
      <div className="thumb-edit__actions">
        <button className="btn btn--sm" onClick={onDone} disabled={busy}>취소</button>
        <button className="btn btn--sm btn--primary" onClick={() => void save()} disabled={busy}>
          {busy ? '합성 중...' : '저장하고 다시 합성'}
        </button>
      </div>
    </div>
  )
}

export default function ThumbCard({ item }: { item: ThumbItem }): JSX.Element {
  const folder = useThumbnailStore((s) => s.folder)
  const baseUrl = useThumbnailStore((s) => s.baseUrl)
  const version = useThumbnailStore((s) => s.version)
  const selected = useThumbnailStore((s) => s.result?.selected === item.id)
  const busy = useThumbnailStore((s) => s.busyId !== null)
  const select = useThumbnailStore((s) => s.select)
  const setNotice = useThumbnailStore((s) => s.setNotice)
  const [editing, setEditing] = useState(false)
  const [showRaw, setShowRaw] = useState(false)

  const copy = async (text: string, label: string): Promise<void> => {
    await window.electronAPI.thumbnail.copyText(text)
    setNotice(`${label}을 복사했습니다`)
  }
  const tags = item.hashtags.join(' ')
  const src = baseUrl ? `${baseUrl}${showRaw ? item.raw : item.image}?v=${version}` : ''

  return (
    <article className={`thumb-card ${selected ? 'thumb-card--selected' : ''}`}>
      <div className="thumb-card__image" onClick={() => setShowRaw((v) => !v)} title="클릭하면 문구 없는 원본 프레임과 번갈아 봅니다">
        {src && <img src={src} alt={item.title} draggable={false} />}
        <span className="thumb-card__no">{item.id}</span>
        {selected && <span className="thumb-card__picked">선택됨</span>}
        {showRaw && <span className="thumb-card__raw">원본 프레임</span>}
      </div>
      <div className="thumb-card__body">
        <div className="thumb-card__angle">{item.angle}</div>
        {editing ? (
          <EditForm item={item} onDone={() => setEditing(false)} />
        ) : (
          <>
            <h3 className="thumb-card__title">{item.title}</h3>
            <p className="thumb-card__desc">{item.description}</p>
            {tags && <p className="thumb-card__tags">{tags}</p>}
            <p className="thumb-card__meta">
              {item.source.clip} · {formatTime(item.source.time)}
              {item.reason && <> — {item.reason}</>}
            </p>
            <div className="thumb-card__actions">
              <button className="btn btn--sm btn--primary" disabled={busy || selected} onClick={() => void select(item.id)}>
                {selected ? '선택한 안' : '이걸로 선택'}
              </button>
              <button className="btn btn--sm" disabled={busy} onClick={() => setEditing(true)}>문구 수정</button>
              <button className="btn btn--sm" onClick={() => void copy(item.title, '제목')}>제목 복사</button>
              <button className="btn btn--sm" onClick={() => void copy(tags ? `${item.description}\n\n${tags}` : item.description, '설명')}>
                설명 복사
              </button>
              <button className="btn btn--sm" onClick={() => folder && void window.electronAPI.thumbnail.reveal(folder, item.image)}>
                Finder
              </button>
            </div>
          </>
        )}
      </div>
    </article>
  )
}
