import { create } from 'zustand'

export type ProcessStage = 'idle' | 'analyzing' | 'analyzed' | 'generating' | 'complete' | 'error'

const PREFERENCE_KEY = 'bgm-studio-preference'

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
  musicPrompt: string
  generateCount: number
  sceneDescription: string | null
  stage: ProcessStage
  percent: number
  message: string
  bgmPaths: string[]
  bgmUrls: string[]
  errorMessage: string | null

  setFile: (filePath: string) => void
  setMediaUrl: (url: string) => void
  setDuration: (duration: number) => void
  setRange: (start: number, end: number) => void
  setMusicPreference: (pref: string) => void
  setMusicPrompt: (prompt: string) => void
  setGenerateCount: (count: number) => void
  setSceneDescription: (desc: string, musicPrompt?: string) => void
  setProgress: (stage: ProcessStage, percent: number, message: string) => void
  setBgm: (bgmPaths: string[], bgmUrls: string[]) => void
  setError: (message: string) => void
  reset: () => void
}

const initialState = {
  filePath: null as string | null,
  fileName: null as string | null,
  mediaUrl: null as string | null,
  duration: 0,
  rangeStart: 0,
  rangeEnd: 0,
  musicPreference: loadPreference(),
  musicPrompt: '',
  generateCount: 1,
  sceneDescription: null as string | null,
  stage: 'idle' as ProcessStage,
  percent: 0,
  message: '',
  bgmPaths: [] as string[],
  bgmUrls: [] as string[],
  errorMessage: null as string | null
}

export const useBgmStore = create<BgmState>((set) => ({
  ...initialState,

  setFile: (filePath) => {
    const fileName = filePath.split('/').pop() || filePath
    set((state) => ({
      ...initialState,
      musicPreference: state.musicPreference,
      filePath,
      fileName
    }))
  },

  setMediaUrl: (url) => set({ mediaUrl: url }),

  setDuration: (duration) =>
    set((state) => {
      if (state.duration > 0) return {}
      return { duration, rangeStart: 0, rangeEnd: Math.min(duration, 180) }
    }),

  setRange: (start, end) => set({ rangeStart: start, rangeEnd: end }),

  setMusicPreference: (pref) => {
    savePreference(pref)
    set({ musicPreference: pref })
  },

  setMusicPrompt: (prompt) => set({ musicPrompt: prompt }),

  setGenerateCount: (count) => set({ generateCount: count }),

  setSceneDescription: (desc, musicPrompt) =>
    set({ sceneDescription: desc, musicPrompt: musicPrompt || '', stage: 'analyzed' }),

  setProgress: (stage, percent, message) => set({ stage, percent, message }),

  setBgm: (bgmPaths, bgmUrls) => set({ stage: 'complete', bgmPaths, bgmUrls }),

  setError: (message) => set({ stage: 'error', errorMessage: message }),

  reset: () => set((state) => ({ ...initialState, musicPreference: state.musicPreference }))
}))
