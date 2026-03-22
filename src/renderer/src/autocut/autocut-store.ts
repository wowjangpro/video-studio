import { create } from 'zustand'

export interface FileInfo {
  path: string
  name: string
  duration: number
  cumulativeOffset: number
  thumbnailUrl: string | null
}

export interface WindowResult {
  windowId: number
  fileIndex: number
  start: number
  end: number
  globalStart: number
  globalEnd: number
  decision: 'keep' | 'drop' | 'pending'
  label: string
  score: number | string
  usable?: string
  usable_reason?: string
}

export interface KeepSegment {
  id: number
  globalStart: number
  globalEnd: number
  label: string
  score: number | string
}

export interface ResumeInfo {
  canResume: boolean
  completedFiles: number
  totalFiles: number
  savedOptions: { window_duration: number }
  hasAnalysisCache?: boolean
}

export type ProcessStage =
  | 'idle'
  | 'initializing'
  | 'extracting'
  | 'stage1_scan'
  | 'vad'
  | 'stt'
  | 'stage2_vision'
  | 'scene_grouping'
  | 'editing'
  | 'editing_pass1'
  | 'editing_pass2'
  | 'merging'
  | 'generating_srt'
  | 'complete'
  | 'error'

interface AppState {
  folderPath: string | null
  files: FileInfo[]
  selectedFileIndex: number
  totalDuration: number

  mediaUrl: string | null
  seekTime: number | null
  seekCounter: number
  playheadPosition: number

  stage: ProcessStage
  percent: number
  message: string
  paused: boolean

  keepSegments: KeepSegment[]
  srtPath: string | null
  edlPath: string | null
  errorMessage: string | null

  timelineZoom: number
  timelineScrollLeft: number

  windowDuration: number
  targetMinutes: number
  analysisFileIndex: number
  previewMode: boolean
  previewPaused: boolean
  previewSegmentIndex: number
  resumeInfo: ResumeInfo | null
  videoPlaying: boolean
  userPlayback: boolean
  editingComment: string
  aiEngine: 'claude' | 'scored'

  removeFile: (index: number) => void
  setEditingComment: (comment: string) => void
  setAiEngine: (engine: 'claude' | 'scored') => void
  setFolder: (folderPath: string, files: FileInfo[], resumeInfo?: ResumeInfo | null) => void
  setResumeInfo: (resumeInfo: ResumeInfo | null) => void
  selectFile: (index: number) => void
  setMediaUrl: (url: string) => void
  seekTo: (globalTime: number) => void
  updatePlayhead: (globalTime: number) => void
  setPaused: (paused: boolean) => void
  setProgress: (stage: ProcessStage, percent: number, message: string) => void
  addWindowResult: (result: WindowResult) => void
  setAnalysisComplete: (keepSegments: KeepSegment[], srtPath: string, edlPath?: string | null) => void
  setError: (message: string) => void
  setTimelineZoom: (zoom: number) => void
  setTimelineScroll: (scrollLeft: number) => void
  setVideoPlaying: (playing: boolean) => void
  setUserPlayback: (active: boolean) => void
  updateSettings: (settings: Partial<{ windowDuration: number; targetMinutes: number }>) => void
  loadSrt: (segments: KeepSegment[], srtPath: string) => void
  startPreview: () => void
  pausePreview: () => void
  resumePreview: () => void
  stopPreview: () => void
  advancePreview: () => false | 'seeked' | 'continuous'
  reset: () => void
}

const initialState = {
  folderPath: null as string | null,
  files: [] as FileInfo[],
  selectedFileIndex: 0,
  totalDuration: 0,
  mediaUrl: null as string | null,
  seekTime: null as number | null,
  seekCounter: 0,
  playheadPosition: 0,
  stage: 'idle' as ProcessStage,
  percent: 0,
  message: '',
  paused: false,
  keepSegments: [] as KeepSegment[],
  srtPath: null as string | null,
  edlPath: null as string | null,
  errorMessage: null as string | null,
  timelineZoom: 10,
  timelineScrollLeft: 0,
  windowDuration: 10,
  targetMinutes: parseInt(localStorage.getItem('autocut:targetMinutes') || '0', 10),
  analysisFileIndex: -1,
  previewMode: false,
  previewPaused: false,
  previewSegmentIndex: 0,
  resumeInfo: null as ResumeInfo | null,
  videoPlaying: false,
  userPlayback: false,
  editingComment: localStorage.getItem('autocut:editingComment') || '',
  aiEngine: (localStorage.getItem('autocut:aiEngine') || 'claude') as 'claude' | 'scored'
}

export const useAutocutStore = create<AppState>((set, get) => ({
  ...initialState,

  setFolder: (folderPath, files, resumeInfo) => {
    const totalDuration = files.reduce((sum, f) => sum + f.duration, 0)
    set({
      folderPath,
      files,
      totalDuration,
      selectedFileIndex: 0,
      mediaUrl: null,
      seekTime: null,
      seekCounter: 0,
      playheadPosition: 0,
      stage: 'idle',
      percent: 0,
      message: '',
      keepSegments: [],
      srtPath: null,
      edlPath: null,
      errorMessage: null,
      resumeInfo: resumeInfo ?? null
    })
  },

  removeFile: (index) => {
    const { files, selectedFileIndex } = get()
    let cumulative = 0
    const newFiles = files
      .filter((_, i) => i !== index)
      .map((f) => {
        const updated = { ...f, cumulativeOffset: cumulative }
        cumulative += f.duration
        return updated
      })
    set({
      files: newFiles,
      totalDuration: cumulative,
      selectedFileIndex: Math.max(0, Math.min(selectedFileIndex, newFiles.length - 1))
    })
  },

  setEditingComment: (comment) => {
    localStorage.setItem('autocut:editingComment', comment)
    set({ editingComment: comment })
  },
  setAiEngine: (engine) => {
    localStorage.setItem('autocut:aiEngine', engine)
    set({ aiEngine: engine })
  },

  setResumeInfo: (resumeInfo) => set({ resumeInfo }),

  selectFile: (index) => {
    const { files } = get()
    if (index >= 0 && index < files.length) {
      set({ selectedFileIndex: index, mediaUrl: null })
    }
  },

  setMediaUrl: (url) => set({ mediaUrl: url }),

  seekTo: (globalTime) => {
    const { files, previewMode, keepSegments } = get()
    if (files.length === 0) return

    let targetIndex = 0
    for (let i = 0; i < files.length; i++) {
      const fileEnd = files[i].cumulativeOffset + files[i].duration
      if (globalTime < fileEnd) {
        targetIndex = i
        break
      }
      if (i === files.length - 1) targetIndex = i
    }

    const localTime = globalTime - files[targetIndex].cumulativeOffset

    // 프리뷰 모드 중 seek → 해당 위치의 구간 인덱스로 동기화
    let segIdx: number | undefined
    if (previewMode && keepSegments.length > 0) {
      // 클릭 위치를 포함하는 구간, 없으면 다음 구간
      const containing = keepSegments.findIndex(
        (s) => globalTime >= s.globalStart && globalTime <= s.globalEnd
      )
      if (containing >= 0) {
        segIdx = containing
      } else {
        const next = keepSegments.findIndex((s) => s.globalStart > globalTime)
        segIdx = next >= 0 ? next : keepSegments.length - 1
      }
    }

    set((state) => ({
      selectedFileIndex: targetIndex,
      seekTime: localTime,
      seekCounter: state.seekCounter + 1,
      playheadPosition: globalTime,
      ...(segIdx !== undefined ? { previewSegmentIndex: segIdx } : {})
    }))
  },

  updatePlayhead: (globalTime) => set({ playheadPosition: globalTime }),

  setPaused: (paused) => set({ paused }),

  setVideoPlaying: (playing) => set({ videoPlaying: playing }),

  setUserPlayback: (active) => set({ userPlayback: active }),

  setProgress: (stage, percent, message) =>
    set(stage === 'idle'
      ? { stage, percent, message, keepSegments: [] }
      : { stage, percent, message }
    ),

  addWindowResult: (result) => {
    const updates: Partial<AppState> = {}
    if (result.fileIndex !== undefined && result.fileIndex !== get().analysisFileIndex) {
      updates.analysisFileIndex = result.fileIndex
    }
    if (result.decision === 'keep') {
      updates.keepSegments = [...get().keepSegments, {
        id: get().keepSegments.length,
        globalStart: result.globalStart,
        globalEnd: result.globalEnd,
        label: result.label,
        score: result.score
      }]
    }
    if (Object.keys(updates).length > 0) {
      set(updates)
    }
  },

  setAnalysisComplete: (keepSegments, srtPath, edlPath) =>
    set({ stage: 'complete', percent: 100, message: '분석 완료', keepSegments, srtPath, edlPath: edlPath || null, analysisFileIndex: -1 }),

  setError: (message) => set({ stage: 'error', errorMessage: message, analysisFileIndex: -1 }),

  setTimelineZoom: (zoom) => set({ timelineZoom: Math.max(2, Math.min(50, zoom)) }),

  setTimelineScroll: (scrollLeft) => set({ timelineScrollLeft: scrollLeft }),

  updateSettings: (settings) => {
    if (settings.targetMinutes !== undefined) {
      localStorage.setItem('autocut:targetMinutes', String(settings.targetMinutes))
    }
    set(settings)
  },

  loadSrt: (segments, srtPath) =>
    set({ stage: 'complete', percent: 100, message: '분석 완료', keepSegments: segments, srtPath }),

  startPreview: () => {
    const { keepSegments } = get()
    if (keepSegments.length === 0) return
    set({ previewMode: true, previewPaused: false, previewSegmentIndex: 0 })
    get().seekTo(keepSegments[0].globalStart)
  },

  pausePreview: () => set({ previewPaused: true }),

  resumePreview: () => set({ previewPaused: false }),

  stopPreview: () => set({ previewMode: false, previewPaused: false }),

  advancePreview: () => {
    const { keepSegments, previewSegmentIndex } = get()
    const nextIndex = previewSegmentIndex + 1
    if (nextIndex >= keepSegments.length) {
      set({ previewMode: false })
      return false
    }
    const currentEnd = keepSegments[previewSegmentIndex].globalEnd
    const nextStart = keepSegments[nextIndex].globalStart
    set({ previewSegmentIndex: nextIndex })
    // 연속 구간이면 seek 없이 재생 유지 (0.5초 이내 차이)
    if (Math.abs(nextStart - currentEnd) > 0.5) {
      get().seekTo(nextStart)
      return 'seeked'
    }
    return 'continuous'
  },

  reset: () => set({ ...initialState })
}))
