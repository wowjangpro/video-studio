import { useRef, useState, useMemo, useEffect } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useSubtitleStore } from './subtitle-store'
import { SubtitleRow } from './SubtitleRow'

export function SubtitleEditor(): JSX.Element {
  const { segments, translatedSegments, selectedLang } = useSubtitleStore()
  const parentRef = useRef<HTMLDivElement>(null)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    const handler = (e: Event): void => {
      setSearchQuery((e as CustomEvent).detail || '')
    }
    window.addEventListener('subtitle-search', handler)
    return () => window.removeEventListener('subtitle-search', handler)
  }, [])

  const activeSegments = useMemo(() => {
    if (selectedLang === 'ko') return segments
    const translated = translatedSegments[selectedLang]
    return translated.length > 0 ? translated : segments
  }, [segments, translatedSegments, selectedLang])

  const filteredSegments = useMemo(() => {
    if (!searchQuery.trim()) return activeSegments
    const q = searchQuery.toLowerCase()
    return activeSegments.filter(
      (s) =>
        s.text.toLowerCase().includes(q) || s.correctedText.toLowerCase().includes(q)
    )
  }, [activeSegments, searchQuery])

  const virtualizer = useVirtualizer({
    count: filteredSegments.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80
  })

  return (
    <div className="sub-editor">
      <div className="sub-editor__header">
        <div className="sub-row__num">#</div>
        <div className="sub-row__time">시간</div>
        <div className="sub-row__text">자막</div>
        <div className="sub-row__actions"></div>
      </div>

      <div ref={parentRef} className="sub-editor__list">
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: '100%',
            position: 'relative'
          }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const segment = filteredSegments[virtualItem.index]
            return (
              <div
                key={segment.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${virtualItem.start}px)`
                }}
                data-index={virtualItem.index}
                ref={virtualizer.measureElement}
              >
                <SubtitleRow segment={segment} readOnly={selectedLang !== 'ko'} />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
