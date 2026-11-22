import { create } from 'zustand'
import type { ThumbFolderInfo, ThumbPatch, ThumbRunResult } from './types'
import { useAppStore } from '../common/app-store'

type Stage = 'idle' | 'loading' | 'ready' | 'generating' | 'error'

interface ThumbnailState {
  folder: string | null
  info: Omit<ThumbFolderInfo, 'result'> | null
  /** thumbnail/ 폴더의 file:// 주소 (끝에 / 포함) */
  baseUrl: string | null
  synopsis: string
  result: ThumbRunResult | null
  /** 이미지가 다시 합성될 때마다 올려 브라우저 캐시를 깬다 */
  version: number

  stage: Stage
  percent: number
  message: string
  errorMessage: string | null
  /** 문구 다시 합성 · 고르기 중인 썸네일 id */
  busyId: number | null
  notice: string | null

  openFolder: (folder: string) => Promise<void>
  reset: () => void
  setSynopsis: (text: string) => void
  setProgress: (percent: number, message: string) => void
  setNotice: (notice: string | null) => void
  generate: () => Promise<void>
  cancel: () => Promise<void>
  compose: (id: number, patch: ThumbPatch) => Promise<boolean>
  select: (id: number) => Promise<void>
}

const initial = {
  folder: null,
  info: null,
  baseUrl: null,
  synopsis: '',
  result: null,
  version: Date.now(),
  stage: 'idle' as Stage,
  percent: 0,
  message: '',
  errorMessage: null,
  busyId: null,
  notice: null
}

export const useThumbnailStore = create<ThumbnailState>((set, get) => ({
  ...initial,

  openFolder: async (folder) => {
    set({ ...initial, folder, stage: 'loading', version: Date.now() })
    const [res, baseUrl] = await Promise.all([
      window.electronAPI.thumbnail.load(folder),
      window.electronAPI.getMediaUrl(folder.replace(/\/+$/, '') + '/thumbnail/')
    ])
    if (get().folder !== folder) return
    if (!res.ok) {
      set({ stage: 'error', errorMessage: res.message })
      return
    }
    const { result, ...info } = res.data
    set({
      info,
      baseUrl,
      result,
      // 이전에 만든 결과가 있으면 그때 쓴 시놉시스를, 없으면 AI 편집의 synopsis.md 를 채워 둔다
      synopsis: result?.synopsis || info.synopsis,
      stage: 'ready'
    })
  },

  reset: () => {
    if (get().stage === 'generating') void window.electronAPI.thumbnail.cancel()
    useAppStore.getState().setModuleStatus('thumbnail', 'idle')
    set({ ...initial, version: Date.now() })
  },

  setSynopsis: (synopsis) => set({ synopsis }),
  setProgress: (percent, message) => set({ percent, message }),
  setNotice: (notice) => set({ notice }),

  generate: async () => {
    const { folder, synopsis } = get()
    if (!folder) return
    set({ stage: 'generating', percent: 0, message: '시작하는 중...', errorMessage: null })
    useAppStore.getState().setModuleStatus('thumbnail', 'running')
    const res = await window.electronAPI.thumbnail.generate(folder, synopsis)
    if (get().folder !== folder) return
    if (res.ok) {
      set({ stage: 'ready', result: res.data, version: Date.now() })
      useAppStore.getState().setModuleStatus('thumbnail', 'complete')
    } else {
      set({ stage: 'error', errorMessage: res.message })
      useAppStore.getState().setModuleStatus('thumbnail', 'error')
    }
  },

  cancel: async () => {
    await window.electronAPI.thumbnail.cancel()
  },

  compose: async (id, patch) => {
    const { folder } = get()
    if (!folder) return false
    set({ busyId: id })
    const res = await window.electronAPI.thumbnail.compose(folder, id, patch)
    if (get().folder !== folder) return false
    if (res.ok) {
      set({ result: res.data, version: Date.now(), busyId: null, notice: '다시 합성했습니다' })
      return true
    }
    set({ busyId: null, notice: res.message })
    return false
  },

  select: async (id) => {
    const { folder } = get()
    if (!folder) return
    set({ busyId: id })
    const res = await window.electronAPI.thumbnail.select(folder, id)
    if (get().folder !== folder) return
    if (res.ok) set({ result: res.data, busyId: null, notice: 'selected.jpg · selected.txt 로 저장했습니다' })
    else set({ busyId: null, notice: res.message })
  }
}))
