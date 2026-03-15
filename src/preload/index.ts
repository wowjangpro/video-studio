import { contextBridge, ipcRenderer } from 'electron'

// ── 공통 타입 ──
export interface ProgressData {
  stage: string
  percent: number
  message: string
}

export interface HealthStatus {
  sharedVenv: boolean
  bgmVenv: boolean
  ffmpeg: boolean
  ollama: boolean
  missingItems: string[]
}

// ── Autocut 타입 ──
export interface FileInfoData {
  path: string
  name: string
  duration: number
  cumulativeOffset: number
  thumbnailUrl: string | null
}

export interface WindowResultData {
  windowId: number
  fileIndex: number
  start: number
  end: number
  globalStart: number
  globalEnd: number
  decision: 'keep' | 'drop' | 'pending'
  label: string
  score: number | string
}

export interface KeepSegmentData {
  id: number
  globalStart: number
  globalEnd: number
  label: string
  score: number | string
}

export interface ResumeInfoData {
  canResume: boolean
  completedFiles: number
  totalFiles: number
  savedOptions: { score_threshold?: number; window_duration: number }
}

// ── Subtitle 타입 ──
export interface SubtitleSegmentData {
  id: number
  start: number
  end: number
  text: string
  correctedText: string
}

export interface ChunkProgressData {
  chunk: number
  totalChunks: number
  chunkStart: number
  chunkEnd: number
}

// ── 공통 API ──
const common = {
  checkHealth: (): Promise<HealthStatus> => ipcRenderer.invoke('check-health'),
  getResourceStatus: (): Promise<{ activeModules: string[] }> => ipcRenderer.invoke('get-resource-status'),
  getMediaUrl: (filePath: string): Promise<string> => ipcRenderer.invoke('get-media-url', filePath),

  onResourceStatus: (callback: (status: Record<string, boolean>) => void) => {
    const handler = (_: unknown, status: Record<string, boolean>): void => callback(status)
    ipcRenderer.on('resource-status', handler)
    return (): void => { ipcRenderer.removeListener('resource-status', handler) }
  },

  onHealthCheck: (callback: (status: HealthStatus) => void) => {
    const handler = (_: unknown, status: HealthStatus): void => callback(status)
    ipcRenderer.on('health-check', handler)
    return (): void => { ipcRenderer.removeListener('health-check', handler) }
  }
}

// ── Autocut API ──
const autocut = {
  selectFolder: (): Promise<{ folderPath: string; files: FileInfoData[]; resumeInfo: ResumeInfoData | null } | null> =>
    ipcRenderer.invoke('autocut:select-folder'),
  scanFolder: (folderPath: string): Promise<{ folderPath: string; files: FileInfoData[]; resumeInfo: ResumeInfoData | null }> =>
    ipcRenderer.invoke('autocut:scan-folder', folderPath),
  getThumbnail: (filePath: string): Promise<string | null> =>
    ipcRenderer.invoke('autocut:get-thumbnail', filePath),
  getWaveform: (filePath: string): Promise<number[]> =>
    ipcRenderer.invoke('autocut:get-waveform', filePath),
  startAnalysis: (folderPath: string, options: Record<string, unknown>): Promise<void> =>
    ipcRenderer.invoke('autocut:start-analysis', folderPath, options),
  cancelAnalysis: (): Promise<void> => ipcRenderer.invoke('autocut:cancel-analysis'),
  pauseAnalysis: (): Promise<void> => ipcRenderer.invoke('autocut:pause-analysis'),
  resumeAnalysis: (): Promise<void> => ipcRenderer.invoke('autocut:resume-analysis'),
  deleteProgress: (folderPath: string): Promise<void> =>
    ipcRenderer.invoke('autocut:delete-progress', folderPath),
  loadSrt: (srtFilePath?: string): Promise<{
    srtPath: string
    segments: { globalStart: number; globalEnd: number; label: string; score: number | string }[]
  } | null> => ipcRenderer.invoke('autocut:load-srt', srtFilePath),
  saveSrt: (segments: unknown[], filePath?: string): Promise<string | null> =>
    ipcRenderer.invoke('autocut:save-srt', segments, filePath),

  onProgress: (callback: (data: ProgressData) => void) => {
    const handler = (_: unknown, data: ProgressData): void => callback(data)
    ipcRenderer.on('autocut:progress', handler)
    return (): void => { ipcRenderer.removeListener('autocut:progress', handler) }
  },
  onWindowResult: (callback: (data: WindowResultData) => void) => {
    const handler = (_: unknown, data: WindowResultData): void => callback(data)
    ipcRenderer.on('autocut:window-result', handler)
    return (): void => { ipcRenderer.removeListener('autocut:window-result', handler) }
  },
  onFileComplete: (callback: (data: { fileIndex: number; keepCount: number; dropCount: number }) => void) => {
    const handler = (_: unknown, data: { fileIndex: number; keepCount: number; dropCount: number }): void => callback(data)
    ipcRenderer.on('autocut:file-complete', handler)
    return (): void => { ipcRenderer.removeListener('autocut:file-complete', handler) }
  },
  onAnalysisComplete: (callback: (data: { keepSegments: KeepSegmentData[]; srtPath: string; totalKeep: number; totalDuration: number }) => void) => {
    const handler = (_: unknown, data: { keepSegments: KeepSegmentData[]; srtPath: string; totalKeep: number; totalDuration: number }): void => callback(data)
    ipcRenderer.on('autocut:complete', handler)
    return (): void => { ipcRenderer.removeListener('autocut:complete', handler) }
  },
  onError: (callback: (message: string) => void) => {
    const handler = (_: unknown, message: string): void => callback(message)
    ipcRenderer.on('autocut:error', handler)
    return (): void => { ipcRenderer.removeListener('autocut:error', handler) }
  },
  onCancelled: (callback: () => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('autocut:cancelled', handler)
    return (): void => { ipcRenderer.removeListener('autocut:cancelled', handler) }
  },
  onPaused: (callback: () => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('autocut:paused', handler)
    return (): void => { ipcRenderer.removeListener('autocut:paused', handler) }
  },
  onResumed: (callback: () => void) => {
    const handler = (): void => callback()
    ipcRenderer.on('autocut:resumed', handler)
    return (): void => { ipcRenderer.removeListener('autocut:resumed', handler) }
  }
}

// ── Subtitle API ──
const subtitle = {
  selectFile: (): Promise<string | null> => ipcRenderer.invoke('subtitle:select-file'),
  startProcess: (filePath: string, modelSize?: string, description?: string): Promise<void> =>
    ipcRenderer.invoke('subtitle:start-process', filePath, modelSize, description),
  cancelProcess: (): Promise<void> => ipcRenderer.invoke('subtitle:cancel-process'),
  saveSrt: (segments: SubtitleSegmentData[], filePath?: string): Promise<string | null> =>
    ipcRenderer.invoke('subtitle:save-srt', segments, filePath),
  selectSrtFile: (): Promise<{ srtPath: string; segments: SubtitleSegmentData[] } | null> =>
    ipcRenderer.invoke('subtitle:select-srt-file'),
  getYoutubeInfo: (url: string): Promise<{ title: string; duration: number; description: string } | null> =>
    ipcRenderer.invoke('subtitle:get-youtube-info', url),
  translateDescription: (title: string, description: string, lang: string): Promise<{ title: string; description: string } | null> =>
    ipcRenderer.invoke('subtitle:translate-description', title, description, lang),
  startYoutubeDownload: (url: string, savePath: string): Promise<void> =>
    ipcRenderer.invoke('subtitle:start-youtube-download', url, savePath),
  translateSubtitles: (
    segments: Array<{ id: number; start: number; end: number; text: string }>,
    lang: string,
    baseSrtPath: string,
    description?: string
  ): Promise<string | null> =>
    ipcRenderer.invoke('subtitle:translate-subtitles', segments, lang, baseSrtPath, description),

  onProgress: (callback: (data: ProgressData) => void) => {
    const handler = (_: unknown, data: ProgressData): void => callback(data)
    ipcRenderer.on('subtitle:progress', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:progress', handler) }
  },
  onChunkProgress: (callback: (data: ChunkProgressData) => void) => {
    const handler = (_: unknown, data: ChunkProgressData): void => callback(data)
    ipcRenderer.on('subtitle:chunk-progress', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:chunk-progress', handler) }
  },
  onSegmentAdded: (callback: (segment: SubtitleSegmentData) => void) => {
    const handler = (_: unknown, segment: SubtitleSegmentData): void => callback(segment)
    ipcRenderer.on('subtitle:segment-added', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:segment-added', handler) }
  },
  onCorrectionComplete: (callback: (segments: SubtitleSegmentData[]) => void) => {
    const handler = (_: unknown, segments: SubtitleSegmentData[]): void => callback(segments)
    ipcRenderer.on('subtitle:correction-complete', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:correction-complete', handler) }
  },
  onProcessComplete: (callback: (data: { segments: SubtitleSegmentData[]; srtPath: string }) => void) => {
    const handler = (_: unknown, data: { segments: SubtitleSegmentData[]; srtPath: string }): void => callback(data)
    ipcRenderer.on('subtitle:complete', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:complete', handler) }
  },
  onError: (callback: (message: string) => void) => {
    const handler = (_: unknown, message: string): void => callback(message)
    ipcRenderer.on('subtitle:error', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:error', handler) }
  },
  onDownloadComplete: (callback: (filePath: string) => void) => {
    const handler = (_: unknown, filePath: string): void => callback(filePath)
    ipcRenderer.on('subtitle:download-complete', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:download-complete', handler) }
  },
  onTranslateProgress: (callback: (data: { percent: number; message: string }) => void) => {
    const handler = (_: unknown, data: { percent: number; message: string }): void => callback(data)
    ipcRenderer.on('subtitle:translate-progress', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:translate-progress', handler) }
  },
  onTranslateComplete: (callback: (data: { lang: string; segments: Array<{ id: number; start: number; end: number; text: string }> }) => void) => {
    const handler = (_: unknown, data: { lang: string; segments: Array<{ id: number; start: number; end: number; text: string }> }): void => callback(data)
    ipcRenderer.on('subtitle:translate-complete', handler)
    return (): void => { ipcRenderer.removeListener('subtitle:translate-complete', handler) }
  }
}

// ── BGM API ──
const bgm = {
  selectFile: (): Promise<string | null> => ipcRenderer.invoke('bgm:select-file'),
  analyzeVideo: (filePath: string, rangeStart: number, rangeEnd: number, preference: string): Promise<void> =>
    ipcRenderer.invoke('bgm:analyze-video', filePath, rangeStart, rangeEnd, preference),
  generateBgm: (filePath: string, rangeStart: number, rangeEnd: number, prompt: string, count: number): Promise<void> =>
    ipcRenderer.invoke('bgm:generate-bgm', filePath, rangeStart, rangeEnd, prompt, count),
  cancelGenerate: (): Promise<void> => ipcRenderer.invoke('bgm:cancel-generate'),

  onProgress: (callback: (data: ProgressData) => void) => {
    const handler = (_: unknown, data: ProgressData): void => callback(data)
    ipcRenderer.on('bgm:progress', handler)
    return (): void => { ipcRenderer.removeListener('bgm:progress', handler) }
  },
  onAnalyzeComplete: (callback: (sceneDescription: string, musicPrompt: string) => void) => {
    const handler = (_: unknown, desc: string, prompt: string): void => callback(desc, prompt)
    ipcRenderer.on('bgm:analyze-complete', handler)
    return (): void => { ipcRenderer.removeListener('bgm:analyze-complete', handler) }
  },
  onGenerateComplete: (callback: (bgmPaths: string[]) => void) => {
    const handler = (_: unknown, bgmPaths: string[]): void => callback(bgmPaths)
    ipcRenderer.on('bgm:generate-complete', handler)
    return (): void => { ipcRenderer.removeListener('bgm:generate-complete', handler) }
  },
  onError: (callback: (message: string) => void) => {
    const handler = (_: unknown, message: string): void => callback(message)
    ipcRenderer.on('bgm:error', handler)
    return (): void => { ipcRenderer.removeListener('bgm:error', handler) }
  }
}

const api = { ...common, autocut, subtitle, bgm }

contextBridge.exposeInMainWorld('electronAPI', api)

export type ElectronAPI = typeof api
