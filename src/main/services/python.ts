import { spawn, ChildProcess } from 'child_process'
import { join, dirname } from 'path'
import { existsSync } from 'fs'
import { StringDecoder } from 'string_decoder'
import { is } from '@electron-toolkit/utils'

export type ModuleName = 'autocut' | 'subtitle' | 'bgm'

// 빌드 앱은 본인 머신의 프로젝트 venv/스크립트를 절대경로로 참조한다.
// venv는 절대경로 의존성이 있어 .app 내부로 옮기면 깨지기 때문에 패키징하지 않는다.
const PROJECT_PYTHON_BASE = '/Users/jeniel/Works/video-studio/python'

function getPythonBase(): string {
  return is.dev ? join(__dirname, '..', '..', 'python') : PROJECT_PYTHON_BASE
}

function getPythonPath(module: ModuleName): string {
  const venvName = module === 'bgm' ? 'bgm-venv' : 'shared-venv'
  return join(getPythonBase(), venvName, 'bin', 'python')
}

function getScriptPath(module: ModuleName, script: string): string {
  return join(getPythonBase(), module, script)
}

export function runPythonScript(
  module: ModuleName,
  script: string,
  args: string[] = [],
  onData?: (data: Record<string, unknown>) => void,
  onError?: (error: string) => void
): ChildProcess {
  const pythonPath = getPythonPath(module)
  const scriptPath = getScriptPath(module, script)

  if (!existsSync(pythonPath)) {
    console.error(`[python:${module}] python not found: ${pythonPath}`)
    onError?.(`Python interpreter not found: ${pythonPath}`)
  }
  if (!existsSync(scriptPath)) {
    console.error(`[python:${module}] script not found: ${scriptPath}`)
    onError?.(`Script not found: ${scriptPath}`)
  }

  const proc = spawn(pythonPath, [scriptPath, ...args], {
    cwd: dirname(scriptPath),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    detached: true
  })

  const decoder = new StringDecoder('utf8')
  let buffer = ''

  proc.stdout?.on('data', (chunk: Buffer) => {
    buffer += decoder.write(chunk)
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const data = JSON.parse(line)
        onData?.(data)
      } catch {
        // non-JSON stdout 무시
      }
    }
  })

  proc.stderr?.on('data', (chunk: Buffer) => {
    const msg = chunk.toString().trim()
    if (msg) {
      onError?.(msg)
    }
  })

  proc.on('error', (err) => {
    console.error(`[python:${module}] spawn error:`, err.message)
    onError?.(err.message)
  })

  proc.on('close', (code) => {
    if (code !== 0) {
      console.error(`[python:${module}] exited with code ${code}`)
    }
  })

  return proc
}
