import { spawn, ChildProcess } from 'child_process'
import { join, dirname } from 'path'
import { existsSync } from 'fs'
import { StringDecoder } from 'string_decoder'
import { is } from '@electron-toolkit/utils'

export type ModuleName = 'autocut' | 'subtitle' | 'bgm' | 'thumbnail'

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

/**
 * Python 은 detached(자기 프로세스 그룹)로 뜬다. 그룹 전체에 보내야 Python 이 띄운 `claude -p` 까지 멈춘다
 * (Python 에만 보내면 Claude 호출이 끝까지 돌며 비용이 나간다).
 */
export function killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals = 'SIGTERM'): void {
  if (proc.pid) {
    try {
      process.kill(-proc.pid, signal)
      return
    } catch { /* 그룹 kill 실패 시 단일 프로세스 kill 시도 */ }
  }
  try { proc.kill(signal) } catch { /* ignore */ }
}

export type PythonJson = Record<string, unknown>

/**
 * Python 스크립트를 한 번 실행하고, doneTypes 중 하나가 나오면 그 메시지로 끝낸다.
 * progress 메시지는 onProgress 로 넘기고, error 메시지·비정상 종료는 { type: 'error' } 로 돌려준다.
 */
export function runPythonOnce(
  module: ModuleName,
  script: string,
  args: string[],
  doneTypes: string[],
  onProgress?: (data: PythonJson) => void,
  track?: (proc: ChildProcess) => void
): Promise<PythonJson> {
  return new Promise((resolve) => {
    let result: PythonJson | null = null
    let stderrText = ''
    const proc = runPythonScript(
      module,
      script,
      args,
      (data) => {
        if (data.type === 'progress') onProgress?.(data)
        else if (doneTypes.includes(String(data.type)) || data.type === 'error') result = data
      },
      (stderr) => {
        stderrText += stderr + '\n'
      }
    )
    track?.(proc)
    proc.on('close', (code, signal) => {
      if (result) resolve(result)
      else if (signal) resolve({ type: 'error', message: '취소되었습니다' })
      else {
        // stderr 에는 경고도 섞여 나오므로 마지막 몇 줄만 보여 준다
        const tail = stderrText.trim().split('\n').slice(-6).join('\n')
        resolve({ type: 'error', message: tail || `프로세스가 비정상 종료되었습니다 (code: ${code})` })
      }
    })
    proc.on('error', (err) => {
      if (!result) resolve({ type: 'error', message: err.message })
    })
  })
}
