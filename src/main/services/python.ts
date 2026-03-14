import { spawn, ChildProcess } from 'child_process'
import { join, dirname } from 'path'
import { existsSync } from 'fs'
import { StringDecoder } from 'string_decoder'
import { is } from '@electron-toolkit/utils'

export type ModuleName = 'autocut' | 'subtitle' | 'bgm'

function getPythonPath(module: ModuleName): string {
  if (is.dev) {
    const venvName = module === 'bgm' ? 'bgm-venv' : 'shared-venv'
    return join(__dirname, '..', '..', 'python', venvName, 'bin', 'python')
  }
  return 'python3'
}

function getScriptPath(module: ModuleName, script: string): string {
  if (is.dev) {
    return join(__dirname, '..', '..', 'python', module, script)
  }
  return join(process.resourcesPath, 'python', module, script)
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

  console.log(`[python:${module}] spawn: ${pythonPath} ${scriptPath}`)
  console.log(`[python:${module}] args: ${args.join(' ')}`)

  if (is.dev && !existsSync(pythonPath)) {
    console.error(`[python:${module}] ERROR: python not found at ${pythonPath}`)
    // onError는 spawn 'error' 이벤트에서 호출됨 — 여기서는 로그만 남김
  }
  if (!existsSync(scriptPath)) {
    console.error(`[python:${module}] ERROR: script not found at ${scriptPath}`)
  }

  const proc = spawn(pythonPath, [scriptPath, ...args], {
    cwd: dirname(scriptPath),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    detached: true
  })

  console.log(`[python:${module}] process started, pid=${proc.pid}`)

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
        console.log(`[python:${module}:stdout] ${data.type}`, data.type === 'progress' ? `${data.stage} ${data.percent}%` : '')
        onData?.(data)
      } catch {
        console.log(`[python:${module}:stdout:raw] ${line.slice(0, 200)}`)
      }
    }
  })

  proc.stderr?.on('data', (chunk: Buffer) => {
    const msg = chunk.toString().trim()
    if (msg) {
      console.log(`[python:${module}:stderr] ${msg.slice(0, 500)}`)
      onError?.(msg)
    }
  })

  proc.on('error', (err) => {
    console.error(`[python:${module}] spawn error:`, err.message)
    onError?.(err.message)
  })

  proc.on('close', (code) => {
    console.log(`[python:${module}] process exited, code=${code}`)
  })

  return proc
}
