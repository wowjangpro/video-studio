import { useEffect, useMemo, useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useLibraryStore, type LibraryTrack } from './library-store'
import { useBgmStore } from './bgm-store'
import { usePreview, toFileUrl, formatTime as formatDuration } from './preview'

function matches(track: LibraryTrack, q: string): boolean {
  return [track.title, track.artist, track.genre, track.mood, track.instruments]
    .some((v) => v != null && v.toLowerCase().includes(q))
}

export default function MusicLibraryPanel(): JSX.Element {
  const info = useLibraryStore((s) => s.info)
  const tracks = useLibraryStore((s) => s.tracks)
  const loading = useLibraryStore((s) => s.loading)
  const errorMessage = useLibraryStore((s) => s.errorMessage)
  const query = useLibraryStore((s) => s.query)
  const playingId = useLibraryStore((s) => s.playingId)
  const selectLibrary = useLibraryStore((s) => s.selectLibrary)
  const reload = useLibraryStore((s) => s.reload)
  const selectAudioDir = useLibraryStore((s) => s.selectAudioDir)
  const setQuery = useLibraryStore((s) => s.setQuery)
  const setPlaying = useLibraryStore((s) => s.setPlaying)
  const hasVideo = useBgmStore((s) => !!s.filePath)
  const cueBusy = useBgmStore((s) => s.cueBusy)
  const addCue = useBgmStore((s) => s.addCue)

  const listRef = useRef<HTMLDivElement>(null)
  const audioRef = useRef<HTMLAudioElement>(null)

  // 처음 열 때 지난번 라이브러리를 다시 읽는다
  useEffect(() => {
    if (!useLibraryStore.getState().info) void reload()
  }, [reload])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? tracks.filter((t) => matches(t, q)) : tracks
  }, [tracks, query])

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 56,
    overscan: 8
  })

  // 재생 대상이 바뀌면 오디오 소스를 교체한다
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const track = playingId ? tracks.find((t) => t.id === playingId) : null
    if (track?.audioPath) {
      usePreview.getState().stop() // 장면 미리듣기와 겹치지 않게
      audio.src = toFileUrl(track.audioPath)
      audio.play().catch(() => setPlaying(null))
    } else {
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
  }, [playingId, tracks, setPlaying])

  const folderName = info?.root.split('/').filter(Boolean).pop() ?? ''

  return (
    <aside className="music-lib">
      <div className="music-lib__header">
        <div className="music-lib__title-row">
          <h3 className="music-lib__title">음악 라이브러리</h3>
          <div className="music-lib__actions">
            {info && (
              <button className="btn btn--sm" onClick={() => void reload()} disabled={loading}>
                새로고침
              </button>
            )}
            <button className="btn btn--sm btn--primary" onClick={() => void selectLibrary()} disabled={loading}>
              {info ? '폴더 변경' : '폴더 선택'}
            </button>
          </div>
        </div>

        {info && (
          <div className="music-lib__meta" title={info.root}>
            <span className="music-lib__folder">{folderName}</span>
            <span className="music-lib__counts">
              {info.trackCount.toLocaleString()}곡 · 음원 {info.downloadedCount.toLocaleString()} · 분석 {info.analyzedCount.toLocaleString()}
            </span>
          </div>
        )}

        {info && (
          <div className="music-lib__audio-dir" title={info.audioDir}>
            <span className="music-lib__audio-path">
              음원: {info.audioDirCustom ? info.audioDir : '라이브러리 안 audio 폴더'}
            </span>
            <button className="btn btn--sm" onClick={() => void selectAudioDir()} disabled={loading}>
              음원 폴더
            </button>
          </div>
        )}

        {info && !info.audioDirOk && (
          <div className="music-lib__error">
            음원 폴더가 보이지 않습니다 ({info.audioDir}). NAS 연결을 확인한 뒤 새로고침하세요. 미리듣기 · 넣기 · 다빈치 내보내기가 되지 않습니다.
          </div>
        )}

        {errorMessage && <div className="music-lib__error">{errorMessage}</div>}

        {info && info.trackCount > 0 && (
          <input
            className="music-lib__search"
            type="search"
            placeholder="제목 · 아티스트 · 장르 · 분위기 · 악기 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
      </div>

      {loading && <div className="music-lib__empty">라이브러리를 읽는 중...</div>}

      {!loading && !info && !errorMessage && (
        <div className="music-lib__empty">
          음악 라이브러리 폴더를 선택하세요.
          <br />
          빈 폴더를 고르면 새 라이브러리를 만듭니다.
        </div>
      )}

      {!loading && info && info.trackCount === 0 && (
        <div className="music-lib__empty">
          아직 수집된 곡이 없습니다.
          <br />
          오디오 보관함 수집 기능으로 곡을 채울 수 있습니다.
        </div>
      )}

      {!loading && info && info.trackCount > 0 && filtered.length === 0 && (
        <div className="music-lib__empty">검색 결과가 없습니다.</div>
      )}

      {!loading && filtered.length > 0 && (
        <div ref={listRef} className="music-lib__list">
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative', width: '100%' }}>
            {virtualizer.getVirtualItems().map((item) => {
              const track = filtered[item.index]
              const isPlaying = playingId === track.id
              const tags = [track.genre, track.mood].filter(Boolean).join(' · ')
              return (
                <div
                  key={track.id}
                  className={`music-lib__row ${isPlaying ? 'music-lib__row--playing' : ''}`}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: `${item.size}px`, transform: `translateY(${item.start}px)` }}
                >
                  <button
                    className="music-lib__play"
                    disabled={!track.audioPath}
                    title={track.audioPath ? (isPlaying ? '정지' : '미리듣기') : '음원 파일 없음'}
                    onClick={() => setPlaying(isPlaying ? null : track.id)}
                  >
                    {isPlaying ? '■' : '▶'}
                  </button>
                  <div className="music-lib__info">
                    <div className="music-lib__name" title={track.title}>{track.title}</div>
                    <div className="music-lib__sub">
                      <span>{track.artist ?? '아티스트 미상'}</span>
                      {tags && <span className="music-lib__tags">{tags}</span>}
                    </div>
                  </div>
                  <div className="music-lib__side">
                    <span className="music-lib__duration">{formatDuration(track.durationSec)}</span>
                    <div className="music-lib__badges">
                      {track.licenseType === 'attribution_required' && (
                        <span className="music-lib__badge music-lib__badge--warn" title={track.attributionText ?? ''}>출처 표기</span>
                      )}
                      {track.hasVocals === true && <span className="music-lib__badge">보컬</span>}
                      {track.bpm != null && <span className="music-lib__badge">{Math.round(track.bpm)} BPM</span>}
                      {!track.audioPath && <span className="music-lib__badge music-lib__badge--dim">음원 없음</span>}
                    </div>
                  </div>
                  {hasVideo && info && (
                    <button
                      className="music-lib__add"
                      title="지금 고른 구간에 이 곡을 처음부터 넣기"
                      disabled={!track.audioPath || cueBusy}
                      onClick={() => void addCue(info.root, track.id, 0)}
                    >
                      넣기
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <audio ref={audioRef} onEnded={() => setPlaying(null)} onError={() => setPlaying(null)} hidden />
    </aside>
  )
}
