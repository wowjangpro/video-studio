import { create } from 'zustand'

// preload 타입은 window.electronAPI 를 통해서만 참조한다 (renderer tsconfig 범위 밖)
export type LibraryOpenResult = Awaited<ReturnType<Window['electronAPI']['bgm']['openLibrary']>>
export type LibraryInfo = Extract<LibraryOpenResult, { status: 'ok' }>['info']
export type LibraryTrack = Extract<LibraryOpenResult, { status: 'ok' }>['tracks'][number]

const ROOT_KEY = 'bgm:libraryRoot'

function loadSavedRoot(): string | null {
  try {
    return localStorage.getItem(ROOT_KEY)
  } catch {
    return null
  }
}

function saveRoot(root: string | null): void {
  try {
    if (root) localStorage.setItem(ROOT_KEY, root)
    else localStorage.removeItem(ROOT_KEY)
  } catch { /* ignore */ }
}

interface LibraryState {
  info: LibraryInfo | null
  tracks: LibraryTrack[]
  loading: boolean
  errorMessage: string | null
  query: string
  playingId: string | null

  /** 폴더 선택 대화상자 → 열기(없으면 새로 만들기) */
  selectLibrary: () => Promise<void>
  /** 저장된 경로 또는 현재 라이브러리를 다시 읽는다 */
  reload: () => Promise<void>
  setQuery: (query: string) => void
  setPlaying: (id: string | null) => void
}

export const useLibraryStore = create<LibraryState>((set, get) => {
  const apply = (result: LibraryOpenResult): void => {
    if (result.status === 'ok') {
      saveRoot(result.info.root)
      set({ info: result.info, tracks: result.tracks, loading: false, errorMessage: null, playingId: null })
    } else if (result.status === 'canceled') {
      set({ loading: false })
    } else if (result.status === 'not_library') {
      set({ loading: false, errorMessage: `음악 라이브러리 폴더가 아닙니다: ${result.root}` })
    } else {
      set({ loading: false, errorMessage: result.message })
    }
  }

  return {
    info: null,
    tracks: [],
    loading: false,
    errorMessage: null,
    query: '',
    playingId: null,

    selectLibrary: async () => {
      // 오류 메시지는 결과가 나온 뒤에만 바꾼다 (취소 시 직전 오류 유지)
      set({ loading: true })
      apply(await window.electronAPI.bgm.selectLibrary())
    },

    reload: async () => {
      const root = get().info?.root ?? loadSavedRoot()
      if (!root) return
      set({ loading: true, errorMessage: null })
      apply(await window.electronAPI.bgm.openLibrary(root))
    },

    setQuery: (query) => set({ query }),
    setPlaying: (id) => set({ playingId: id })
  }
})
