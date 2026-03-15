import { BrowserWindow } from 'electron'
import type { ModuleName } from './python'

export type ResourceType = 'ollama' | 'ffmpeg' | 'whisper'

interface ModuleResources {
  module: ModuleName
  resources: ResourceType[]
  ollamaGpuGB: number
}

/** 각 모듈의 Ollama GPU 메모리 요구량 (GB) */
const MODULE_GPU_REQUIREMENTS: Record<ModuleName, number> = {
  autocut: 21,  // qwen2.5vl:7b + qwen3:14b
  subtitle: 14, // qwen2.5:14b
  bgm: 25       // llama3.2-vision:11b + qwen2.5:14b
}

const GPU_MEMORY_LIMIT = 32 // Apple Silicon 통합 메모리

class ResourceManager {
  private activeModules: Map<ModuleName, ModuleResources> = new Map()

  /**
   * 리소스 점유 요청.
   * 충돌 시 { conflict: true, message, currentModules } 반환.
   * 성공 시 { conflict: false } 반환.
   */
  acquire(
    module: ModuleName,
    resources: ResourceType[],
    options?: { usesClaude?: boolean }
  ): { conflict: boolean; message?: string; currentModules?: ModuleName[] } {
    const ollamaGpuGB = resources.includes('ollama') && !options?.usesClaude
      ? MODULE_GPU_REQUIREMENTS[module]
      : 0

    // 현재 Ollama 사용 중인 모듈들의 GPU 합계
    let currentGpuTotal = 0
    const currentModules: ModuleName[] = []
    for (const [name, info] of this.activeModules) {
      if (name === module) continue
      currentGpuTotal += info.ollamaGpuGB
      if (info.ollamaGpuGB > 0) currentModules.push(name)
    }

    const totalGpu = currentGpuTotal + ollamaGpuGB
    if (totalGpu > GPU_MEMORY_LIMIT && ollamaGpuGB > 0 && currentGpuTotal > 0) {
      const moduleNames: Record<ModuleName, string> = {
        autocut: 'AI 편집',
        subtitle: '자막 추출',
        bgm: 'BGM 생성'
      }
      const runningNames = currentModules.map((m) => moduleNames[m]).join(', ')
      return {
        conflict: true,
        message: `${runningNames}이(가) Ollama를 사용 중입니다 (${currentGpuTotal}GB). ` +
          `${moduleNames[module]}까지 실행하면 GPU 메모리가 ${totalGpu}GB 필요하여 ` +
          `${GPU_MEMORY_LIMIT}GB를 초과합니다.`,
        currentModules
      }
    }

    this.activeModules.set(module, { module, resources, ollamaGpuGB })
    this.notifyRenderer()
    return { conflict: false }
  }

  /** 리소스 해제 */
  release(module: ModuleName): void {
    this.activeModules.delete(module)
    this.notifyRenderer()
  }

  /** 특정 모듈이 활성 상태인지 확인 */
  isActive(module: ModuleName): boolean {
    return this.activeModules.has(module)
  }

  /** 활성 모듈 목록 */
  getActiveModules(): ModuleName[] {
    return Array.from(this.activeModules.keys())
  }

  /** 강제 점유 (사용자가 경고 무시 시) */
  forceAcquire(module: ModuleName, resources: ResourceType[], options?: { usesClaude?: boolean }): void {
    const ollamaGpuGB = resources.includes('ollama') && !options?.usesClaude
      ? MODULE_GPU_REQUIREMENTS[module]
      : 0
    this.activeModules.set(module, { module, resources, ollamaGpuGB })
    this.notifyRenderer()
  }

  /** Renderer에 모듈 상태 알림 */
  private notifyRenderer(): void {
    const win = BrowserWindow.getAllWindows()[0]
    if (win && !win.isDestroyed()) {
      const status: Record<ModuleName, boolean> = {
        autocut: this.activeModules.has('autocut'),
        subtitle: this.activeModules.has('subtitle'),
        bgm: this.activeModules.has('bgm')
      }
      win.webContents.send('resource-status', status)
    }
  }
}

export const resourceManager = new ResourceManager()
