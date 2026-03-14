import { create } from 'zustand'

export type ModuleName = 'autocut' | 'subtitle' | 'bgm'

interface AppState {
  activeModule: ModuleName
  moduleStatus: Record<ModuleName, 'idle' | 'running' | 'complete' | 'error'>
  healthOk: boolean
  healthMissing: string[]
  conflictMessage: string | null

  setActiveModule: (module: ModuleName) => void
  setModuleStatus: (module: ModuleName, status: 'idle' | 'running' | 'complete' | 'error') => void
  setHealth: (ok: boolean, missing: string[]) => void
  setConflict: (message: string | null) => void
}

export const useAppStore = create<AppState>((set) => ({
  activeModule: 'autocut',
  moduleStatus: { autocut: 'idle', subtitle: 'idle', bgm: 'idle' },
  healthOk: true,
  healthMissing: [],
  conflictMessage: null,

  setActiveModule: (module) => set({ activeModule: module }),

  setModuleStatus: (module, status) =>
    set((state) => ({
      moduleStatus: { ...state.moduleStatus, [module]: status }
    })),

  setHealth: (ok, missing) => set({ healthOk: ok, healthMissing: missing }),

  setConflict: (message) => set({ conflictMessage: message })
}))
