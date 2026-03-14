import { spawn } from 'child_process'
import { join, basename } from 'path'
import { tmpdir } from 'os'
import { randomUUID } from 'crypto'

export function getVideoDuration(filePath: string): Promise<number> {
  return new Promise((resolve) => {
    const proc = spawn('ffprobe', [
      '-v', 'quiet',
      '-show_entries', 'format=duration',
      '-of', 'csv=p=0',
      filePath
    ])
    let output = ''
    proc.stdout.on('data', (d) => { output += d.toString() })
    proc.on('close', () => {
      const dur = parseFloat(output.trim()) || 0
      console.log(`[ffmpeg] duration: ${basename(filePath)} → ${dur.toFixed(1)}s`)
      resolve(dur)
    })
    proc.on('error', (err) => {
      console.error(`[ffmpeg] ffprobe error: ${err.message}`)
      resolve(0)
    })
  })
}

export function generateThumbnail(filePath: string): Promise<string | null> {
  const outPath = join(tmpdir(), `video-studio-thumb-${randomUUID()}.jpg`)
  return generateThumbnailTo(filePath, outPath)
}

export function generateThumbnailTo(filePath: string, outPath: string): Promise<string | null> {
  return new Promise((resolve) => {
    const proc = spawn('ffmpeg', [
      '-i', filePath,
      '-ss', '1',
      '-vframes', '1',
      '-vf', 'scale=160:-1',
      '-q:v', '5',
      '-y', outPath
    ])
    proc.on('close', (code) => {
      resolve(code === 0 ? 'file://' + outPath : null)
    })
    proc.on('error', () => resolve(null))
  })
}

export function extractWaveformPeaks(filePath: string, peaksPerSecond = 10): Promise<number[]> {
  return new Promise((resolve) => {
    const proc = spawn('ffmpeg', [
      '-i', filePath,
      '-ac', '1',
      '-ar', '8000',
      '-f', 's16le',
      '-acodec', 'pcm_s16le',
      'pipe:1'
    ], { stdio: ['ignore', 'pipe', 'ignore'] })

    const chunks: Buffer[] = []
    proc.stdout.on('data', (d: Buffer) => chunks.push(d))

    proc.on('close', (code) => {
      if (code !== 0 || chunks.length === 0) {
        resolve([])
        return
      }
      const raw = Buffer.concat(chunks)
      const samples = new Int16Array(raw.buffer, raw.byteOffset, Math.floor(raw.byteLength / 2))
      const samplesPerPeak = Math.floor(8000 / peaksPerSecond)
      const peaks: number[] = []
      for (let i = 0; i < samples.length; i += samplesPerPeak) {
        let max = 0
        const end = Math.min(i + samplesPerPeak, samples.length)
        for (let j = i; j < end; j++) {
          const abs = Math.abs(samples[j])
          if (abs > max) max = abs
        }
        peaks.push(max / 32768)
      }
      resolve(peaks)
    })

    proc.on('error', () => resolve([]))
  })
}
