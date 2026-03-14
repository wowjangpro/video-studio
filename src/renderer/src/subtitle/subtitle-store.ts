import { create } from 'zustand'

export interface SubtitleSegment {
  id: number
  start: number
  end: number
  text: string
  correctedText: string
  isEdited: boolean
}

export type ProcessStage =
  | 'idle'
  | 'downloading'
  | 'extracting'
  | 'transcribing'
  | 'correcting'
  | 'complete'
  | 'error'

export type SubtitleLang = 'ko' | 'en' | 'jp'

interface TranslatedSegments {
  en: SubtitleSegment[]
  jp: SubtitleSegment[]
}

interface SubtitleState {
  filePath: string | null
  fileName: string | null
  srtPath: string | null
  mediaUrl: string | null
  stage: ProcessStage
  percent: number
  message: string
  segments: SubtitleSegment[]
  translatedSegments: TranslatedSegments
  selectedLang: SubtitleLang
  errorMessage: string | null
  currentChunk: number
  totalChunks: number
  chunkStart: number
  chunkEnd: number
  videoDescription: string
  seekTime: number | null
  seekEndTime: number | null
  seekId: number
  activeSegmentId: number | null

  setFile: (filePath: string) => void
  setMediaUrl: (url: string) => void
  setActiveSegmentId: (id: number | null) => void
  seekTo: (time: number, endTime: number) => void
  setProgress: (stage: ProcessStage, percent: number, message: string) => void
  setChunkProgress: (chunk: number, totalChunks: number, chunkStart: number, chunkEnd: number) => void
  addSegment: (segment: SubtitleSegment) => void
  setSegments: (segments: SubtitleSegment[]) => void
  updateSegment: (id: number, correctedText: string) => void
  updateSegmentTime: (id: number, start: number, end: number) => void
  setSrtPath: (path: string) => void
  setVideoDescription: (desc: string) => void
  setSelectedLang: (lang: SubtitleLang) => void
  setTranslatedSegments: (lang: 'en' | 'jp', segments: SubtitleSegment[]) => void
  loadComplete: (filePath: string, srtPath: string, segments: SubtitleSegment[]) => void
  setError: (message: string) => void
  reset: () => void
}

export const useSubtitleStore = create<SubtitleState>((set) => ({
  filePath: null,
  fileName: null,
  srtPath: null,
  mediaUrl: null,
  stage: 'idle',
  percent: 0,
  message: '',
  segments: [],
  translatedSegments: { en: [], jp: [] },
  selectedLang: 'ko',
  videoDescription: '',
  errorMessage: null,
  currentChunk: 0,
  totalChunks: 0,
  chunkStart: 0,
  chunkEnd: 0,
  seekTime: null,
  seekEndTime: null,
  seekId: 0,
  activeSegmentId: null,

  setFile: (filePath) => {
    const parts = filePath.split('/')
    set({
      filePath,
      fileName: parts[parts.length - 1],
      srtPath: null,
      mediaUrl: null,
      stage: 'idle',
      segments: [],
      translatedSegments: { en: [], jp: [] },
      selectedLang: 'ko',
      errorMessage: null,
      currentChunk: 0,
      totalChunks: 0,
      chunkStart: 0,
      chunkEnd: 0,
      seekTime: null,
      seekEndTime: null,
      seekId: 0
    })
  },

  setMediaUrl: (url) => set({ mediaUrl: url }),

  setActiveSegmentId: (id) => set({ activeSegmentId: id }),

  seekTo: (time, endTime) => set((state) => ({ seekTime: time, seekEndTime: endTime, seekId: state.seekId + 1 })),

  setProgress: (stage, percent, message) => set({ stage, percent, message, errorMessage: null }),

  setChunkProgress: (chunk, totalChunks, chunkStart, chunkEnd) =>
    set({ currentChunk: chunk, totalChunks, chunkStart, chunkEnd }),

  addSegment: (segment) =>
    set((state) => ({
      segments: [...state.segments, segment]
    })),

  setSegments: (segments) => set({ segments }),

  updateSegment: (id, correctedText) =>
    set((state) => ({
      segments: state.segments.map((s) =>
        s.id === id ? { ...s, correctedText, isEdited: true } : s
      )
    })),

  updateSegmentTime: (id, start, end) =>
    set((state) => ({
      segments: state.segments.map((s) => (s.id === id ? { ...s, start, end, isEdited: true } : s))
    })),

  setSrtPath: (path) => set({ srtPath: path }),

  setVideoDescription: (desc) => set({ videoDescription: desc }),

  setSelectedLang: (lang) => set({ selectedLang: lang }),

  setTranslatedSegments: (lang, segments) =>
    set((state) => ({
      translatedSegments: { ...state.translatedSegments, [lang]: segments }
    })),

  loadComplete: (filePath, srtPath, segments) => {
    const parts = filePath.split('/')
    set({
      filePath,
      fileName: parts[parts.length - 1],
      srtPath,
      stage: 'complete',
      percent: 100,
      message: '',
      segments,
      errorMessage: null
    })
  },

  setError: (message) => set({ stage: 'error', errorMessage: message }),

  reset: () =>
    set({
      filePath: null,
      fileName: null,
      srtPath: null,
      mediaUrl: null,
      stage: 'idle',
      percent: 0,
      message: '',
      segments: [],
      translatedSegments: { en: [], jp: [] },
      selectedLang: 'ko',
      errorMessage: null,
      currentChunk: 0,
      totalChunks: 0,
      chunkStart: 0,
      chunkEnd: 0,
      seekTime: null,
      seekEndTime: null,
      seekId: 0,
      activeSegmentId: null
    })
}))
