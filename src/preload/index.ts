import { contextBridge, ipcRenderer, webUtils } from 'electron'

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

// ── BGM 음악 라이브러리 타입 ──
export interface LibraryTrack {
  id: string
  title: string
  artist: string | null
  genre: string | null
  mood: string | null
  instruments: string | null
  durationSec: number | null
  licenseType: 'attribution_required' | 'attribution_not_required' | null
  attributionText: string | null
  /** 음원 파일 절대 경로 (받지 않은 곡이거나 음원 폴더가 안 보이면 null) */
  audioPath: string | null
  bpm: number | null
  hasVocals: boolean | null
  analyzed: boolean
}

export interface LibraryInfo {
  root: string
  formatVersion: number
  createdAt: string | null
  trackCount: number
  downloadedCount: number
  analyzedCount: number
  /** 음원 폴더 절대 경로 (기본은 <root>/audio) */
  audioDir: string
  /** library.json 에 따로 지정한 음원 폴더인지 */
  audioDirCustom: boolean
  /** 음원 폴더가 지금 보이는지 (NAS 연결이 끊기면 false) */
  audioDirOk: boolean
}

export type BgmResult<T> = { ok: true; data: T } | { ok: false; message: string }

export interface MusicCriteria {
  scene_description: string
  music_queries: string[]
  energy: 'low' | 'medium' | 'high'
  bpm_min: number | null
  bpm_max: number | null
  vocals_ok: boolean
  genres: string[]
  moods: string[]
}

export interface RecommendedTrack {
  id: string
  title: string
  artist: string | null
  genre: string | null
  mood: string | null
  durationSec: number
  /** 곡 안에서 이 구간에 쓸 시작 지점 (초) */
  offsetSec: number
  score: number
  similarity: number
  bpm: number | null
  hasVocals: boolean
  licenseType: 'attribution_required' | 'attribution_not_required' | null
  attributionText: string | null
  audioPath: string | null
  reasons: string[]
}

export interface RecommendData {
  criteria: MusicCriteria
  tracks: RecommendedTrack[]
  durationSec: number
}

export interface BgmCue {
  cueId: string
  trackId: string
  title: string
  artist: string | null
  audioRel: string
  audioPath: string | null
  rangeStart: number
  rangeEnd: number
  offsetSec: number
  licenseType: string | null
  attributionText: string | null
  addedAt: string
}

export interface BgmExportData {
  fcpxmlPath: string
  creditsPath: string
  credits: string
}

export type LibraryOpenResult =
  | { status: 'ok'; info: LibraryInfo; tracks: LibraryTrack[] }
  | { status: 'not_library'; root: string; empty: boolean }
  | { status: 'canceled' }
  | { status: 'error'; message: string }

// ── 공통 API ──
const common = {
  checkHealth: (): Promise<HealthStatus> => ipcRenderer.invoke('check-health'),
  getResourceStatus: (): Promise<{ activeModules: string[] }> => ipcRenderer.invoke('get-resource-status'),
  getMediaUrl: (filePath: string): Promise<string> => ipcRenderer.invoke('get-media-url', filePath),
  /** 드래그로 놓은 파일의 실제 경로 (Electron 32 부터 File.path 가 없어졌다) */
  getPathForFile: (file: File): string => webUtils.getPathForFile(file),

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
  onAnalysisComplete: (callback: (data: { keepSegments: KeepSegmentData[]; srtPath: string; edlPath?: string | null; fcpxmlPath?: string | null; totalKeep: number; totalDuration: number; keepSpeechSec?: number; keepNonspeechSec?: number }) => void) => {
    const handler = (_: unknown, data: { keepSegments: KeepSegmentData[]; srtPath: string; edlPath?: string | null; fcpxmlPath?: string | null; totalKeep: number; totalDuration: number; keepSpeechSec?: number; keepNonspeechSec?: number }): void => callback(data)
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
  startProcess: (filePath: string, modelSize?: string, description?: string, aiEngine?: string): Promise<void> =>
    ipcRenderer.invoke('subtitle:start-process', filePath, modelSize, description, aiEngine),
  cancelProcess: (): Promise<void> => ipcRenderer.invoke('subtitle:cancel-process'),
  saveSrt: (segments: SubtitleSegmentData[], filePath?: string): Promise<string | null> =>
    ipcRenderer.invoke('subtitle:save-srt', segments, filePath),
  selectSrtFile: (): Promise<{ srtPath: string; segments: SubtitleSegmentData[] } | null> =>
    ipcRenderer.invoke('subtitle:select-srt-file'),
  getYoutubeInfo: (url: string): Promise<{ title: string; duration: number; description: string } | null> =>
    ipcRenderer.invoke('subtitle:get-youtube-info', url),
  translateDescription: (title: string, description: string, lang: string, aiEngine?: string): Promise<{ title: string; description: string } | null> =>
    ipcRenderer.invoke('subtitle:translate-description', title, description, lang, aiEngine),
  startYoutubeDownload: (url: string, savePath: string): Promise<void> =>
    ipcRenderer.invoke('subtitle:start-youtube-download', url, savePath),
  translateSubtitles: (
    segments: Array<{ id: number; start: number; end: number; text: string }>,
    lang: string,
    baseSrtPath: string,
    description?: string,
    aiEngine?: string
  ): Promise<string | null> =>
    ipcRenderer.invoke('subtitle:translate-subtitles', segments, lang, baseSrtPath, description, aiEngine),

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
  selectLibrary: (): Promise<LibraryOpenResult> => ipcRenderer.invoke('bgm:library-select'),
  openLibrary: (root: string): Promise<LibraryOpenResult> => ipcRenderer.invoke('bgm:library-open', root),
  selectAudioDir: (root: string): Promise<LibraryOpenResult> => ipcRenderer.invoke('bgm:library-audio-dir', root),

  /** 구간에 어울리는 곡 추천 (진행 상황은 onProgress) */
  recommend: (root: string, video: string, start: number, end: number, preference: string, usedTrackIds: string[] = []): Promise<BgmResult<RecommendData>> =>
    ipcRenderer.invoke('bgm:recommend', root, video, start, end, preference, usedTrackIds),
  cancel: (): Promise<void> => ipcRenderer.invoke('bgm:cancel'),

  cues: (root: string, video: string): Promise<BgmResult<BgmCue[]>> => ipcRenderer.invoke('bgm:cues', root, video),
  addCue: (root: string, video: string, cue: { trackId: string; rangeStart: number; rangeEnd: number; offsetSec: number }): Promise<BgmResult<BgmCue[]>> =>
    ipcRenderer.invoke('bgm:add-cue', root, video, cue),
  removeCue: (root: string, video: string, cueId: string): Promise<BgmResult<BgmCue[]>> =>
    ipcRenderer.invoke('bgm:remove-cue', root, video, cueId),
  exportDavinci: (root: string, video: string): Promise<BgmResult<BgmExportData>> => ipcRenderer.invoke('bgm:export', root, video),
  reveal: (path: string): Promise<void> => ipcRenderer.invoke('bgm:reveal', path),

  onProgress: (callback: (data: ProgressData) => void) => {
    const handler = (_: unknown, data: ProgressData): void => callback(data)
    ipcRenderer.on('bgm:progress', handler)
    return (): void => { ipcRenderer.removeListener('bgm:progress', handler) }
  }
}

const api = { ...common, autocut, subtitle, bgm }

contextBridge.exposeInMainWorld('electronAPI', api)

export type ElectronAPI = typeof api
