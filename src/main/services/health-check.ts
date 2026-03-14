import { existsSync } from 'fs'
import { join } from 'path'
import { execSync } from 'child_process'
import { is } from '@electron-toolkit/utils'
import { BrowserWindow } from 'electron'

export interface HealthStatus {
  sharedVenv: boolean
  bgmVenv: boolean
  ffmpeg: boolean
  ollama: boolean
  missingItems: string[]
}

export async function checkHealth(): Promise<HealthStatus> {
  const missing: string[] = []

  // Python venv 확인
  let sharedVenv = false
  let bgmVenv = false
  if (is.dev) {
    const sharedPath = join(__dirname, '..', '..', 'python', 'shared-venv', 'bin', 'python')
    const bgmPath = join(__dirname, '..', '..', 'python', 'bgm-venv', 'bin', 'python')
    sharedVenv = existsSync(sharedPath)
    bgmVenv = existsSync(bgmPath)
    if (!sharedVenv) missing.push('Python 공유 가상환경 (npm run setup 실행 필요)')
    if (!bgmVenv) missing.push('Python BGM 가상환경 (npm run setup 실행 필요)')
  } else {
    sharedVenv = true
    bgmVenv = true
  }

  // FFmpeg 확인
  let ffmpeg = false
  try {
    execSync('ffmpeg -version', { stdio: 'ignore' })
    ffmpeg = true
  } catch {
    missing.push('FFmpeg (brew install ffmpeg)')
  }

  // Ollama 확인
  let ollama = false
  try {
    const ollamaUrl = process.env.OLLAMA_HOST || 'http://localhost:11434'
    const response = await fetch(`${ollamaUrl}/api/tags`)
    ollama = response.ok
  } catch {
    missing.push('Ollama 서버 (brew install ollama && ollama serve)')
  }

  const status: HealthStatus = { sharedVenv, bgmVenv, ffmpeg, ollama, missingItems: missing }

  if (missing.length > 0) {
    console.log(`[health] 누락 항목: ${missing.join(', ')}`)
    const win = BrowserWindow.getAllWindows()[0]
    if (win && !win.isDestroyed()) {
      win.webContents.send('health-check', status)
    }
  } else {
    console.log('[health] 모든 환경 정상')
  }

  return status
}
