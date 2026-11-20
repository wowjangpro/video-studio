import { create } from 'zustand'

// preload 타입은 window.electronAPI 를 통해서만 참조한다 (renderer tsconfig 범위 밖)
type BgmApi = Window['electronAPI']['bgm']
type Data<T> = Awaited<T> extends infer R ? (R extends { ok: true; data: infer D } ? D : never) : never
export type RecommendData = Data<ReturnType<BgmApi['recommend']>>
export type RecommendedTrack = RecommendData['tracks'][number]
export type BgmCue = Data<ReturnType<BgmApi['cues']>>[number]
export type BgmExportData = Data<ReturnType<BgmApi['exportDavinci']>>

export type ProcessStage = 'idle' | 'recommending' | 'error'

const PREFERENCE_KEY = 'bgm-studio-preference'
/** 한 번에 고를 수 있는 최대 구간 (초) */
export const MAX_RANGE_SEC = 600

function loadPreference(): string {
  try {
    return localStorage.getItem(PREFERENCE_KEY) || ''
  } catch {
    return ''
  }
}

function savePreference(value: string): void {
  try {
    localStorage.setItem(PREFERENCE_KEY, value)
  } catch { /* ignore */ }
}

interface BgmState {
  filePath: string | null
  fileName: string | null
  mediaUrl: string | null
  duration: number
  rangeStart: number
  rangeEnd: number
  musicPreference: string

  stage: ProcessStage
  percent: number
  message: string
  errorMessage: string | null
  result: RecommendData | null
  /** 추천 결과가 어느 구간에 대한 것인지 (구간을 옮긴 뒤 "넣기"를 눌러도 추천받은 구간으로 넣는다) */
  resultRange: { start: number; end: number } | null

  cues: BgmCue[]
  cueBusy: boolean
  notice: string | null
  exportResult: BgmExportData | null

  setFile: (filePath: string) => void
  setMediaUrl: (url: string) => void
  setDuration: (duration: number) => void
  setRange: (start: number, end: number) => void
  setMusicPreference: (pref: string) => void
  setProgress: (percent: number, message: string) => void
  setNotice: (notice: string | null) => void

  recommend: (root: string) => Promise<void>
  cancel: () => Promise<void>
  loadCues: (root: string) => Promise<void>
  addCue: (root: string, trackId: string, offsetSec: number, range?: { start: number; end: number }) => Promise<void>
  removeCue: (root: string, cueId: string) => Promise<void>
  exportDavinci: (root: string) => Promise<void>
  reset: () => void
}

const initialState = {
  filePath: null as string | null,
  fileName: null as string | null,
  mediaUrl: null as string | null,
  duration: 0,
  rangeStart: 0,
  rangeEnd: 0,
  stage: 'idle' as ProcessStage,
  percent: 0,
  message: '',
  errorMessage: null as string | null,
  result: null as RecommendData | null,
  resultRange: null as { start: number; end: number } | null,
  cues: [] as BgmCue[],
  cueBusy: false,
  notice: null as string | null,
  exportResult: null as BgmExportData | null
}

// 추천 요청 번호 — 기다리는 사이 새 요청·취소·영상 변경이 있었으면 늦게 온 응답을 버린다
let recommendSeq = 0

export const useBgmStore = create<BgmState>((set, get) => ({
  ...initialState,
  musicPreference: loadPreference(),

  setFile: (filePath) => {
    const fileName = filePath.split('/').pop() || filePath
    if (get().stage === 'recommending') void get().cancel()
    set((state) => ({ ...initialState, musicPreference: state.musicPreference, filePath, fileName }))
  },

  setMediaUrl: (url) => set({ mediaUrl: url }),

  setDuration: (duration) =>
    set((state) => {
      if (state.duration > 0) return {}
      return { duration, rangeStart: 0, rangeEnd: Math.min(duration, 60) }
    }),

  setRange: (start, end) => set({ rangeStart: start, rangeEnd: end }),

  setMusicPreference: (pref) => {
    savePreference(pref)
    set({ musicPreference: pref })
  },

  setProgress: (percent, message) => set({ percent, message }),
  setNotice: (notice) => set({ notice }),

  recommend: async (root) => {
    const { filePath, rangeStart, rangeEnd, musicPreference, cues } = get()
    if (!filePath) return
    const seq = ++recommendSeq
    set({ stage: 'recommending', percent: -1, message: '추천을 준비하고 있습니다...', errorMessage: null })
    const used = [...new Set(cues.map((c) => c.trackId))]
    const res = await window.electronAPI.bgm.recommend(root, filePath, rangeStart, rangeEnd, musicPreference, used)
    if (seq !== recommendSeq || get().filePath !== filePath) return // 그사이 취소·새 요청·영상 변경
    if (res.ok) {
      set({ stage: 'idle', result: res.data, resultRange: { start: rangeStart, end: rangeEnd } })
    } else if (res.message === '취소되었습니다') {
      set({ stage: 'idle' })
    } else {
      set({ stage: 'error', errorMessage: res.message })
    }
  },

  cancel: async () => {
    recommendSeq++
    set({ stage: 'idle' })
    await window.electronAPI.bgm.cancel()
  },

  loadCues: async (root) => {
    const { filePath } = get()
    if (!filePath) return
    const res = await window.electronAPI.bgm.cues(root, filePath)
    if (get().filePath !== filePath) return
    set(res.ok ? { cues: res.data } : { notice: res.message })
  },

  addCue: async (root, trackId, offsetSec, range) => {
    const { filePath, rangeStart, rangeEnd } = get()
    if (!filePath) return
    const r = range ?? { start: rangeStart, end: rangeEnd }
    set({ cueBusy: true, exportResult: null })
    const res = await window.electronAPI.bgm.addCue(root, filePath, {
      trackId, rangeStart: r.start, rangeEnd: r.end, offsetSec
    })
    if (get().filePath !== filePath) return set({ cueBusy: false }) // 그사이 다른 영상으로 바뀜
    set(res.ok ? { cues: res.data, cueBusy: false, notice: '곡을 넣었습니다' } : { cueBusy: false, notice: res.message })
  },

  removeCue: async (root, cueId) => {
    const { filePath } = get()
    if (!filePath) return
    set({ cueBusy: true, exportResult: null })
    const res = await window.electronAPI.bgm.removeCue(root, filePath, cueId)
    if (get().filePath !== filePath) return set({ cueBusy: false })
    set(res.ok ? { cues: res.data, cueBusy: false } : { cueBusy: false, notice: res.message })
  },

  exportDavinci: async (root) => {
    const { filePath } = get()
    if (!filePath) return
    set({ cueBusy: true })
    const res = await window.electronAPI.bgm.exportDavinci(root, filePath)
    if (get().filePath !== filePath) return set({ cueBusy: false })
    set(res.ok ? { exportResult: res.data, cueBusy: false } : { cueBusy: false, notice: res.message })
  },

  reset: () => {
    if (get().stage === 'recommending') void get().cancel()
    set((state) => ({ ...initialState, musicPreference: state.musicPreference }))
  }
}))
