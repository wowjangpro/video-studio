import { writeFile, readFile } from 'fs/promises'

export interface SrtSegment {
  id: number
  start: number
  end: number
  text: string
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.round((seconds % 1) * 1000)
  return (
    String(h).padStart(2, '0') +
    ':' +
    String(m).padStart(2, '0') +
    ':' +
    String(s).padStart(2, '0') +
    ',' +
    String(ms).padStart(3, '0')
  )
}

function parseTime(timeStr: string): number {
  const [hms, msStr] = timeStr.split(',')
  const [h, m, s] = hms.split(':').map(Number)
  return h * 3600 + m * 60 + s + parseInt(msStr) / 1000
}

export function segmentsToSrt(segments: SrtSegment[]): string {
  const filtered = segments.filter((seg) => seg.text.trim().length > 0)
  const lines: string[] = []
  let num = 1

  if (filtered.length > 0 && filtered[0].start > 0.5) {
    lines.push(`${num}\n${formatTime(0)} --> ${formatTime(0.5)}\n \n`)
    num++
  }

  for (const seg of filtered) {
    const startTime = formatTime(seg.start)
    const endTime = formatTime(seg.end)
    const text = seg.text.replace(/\.+$/, '')
    lines.push(`${num}\n${startTime} --> ${endTime}\n${text}\n`)
    num++
  }

  return lines.join('\n')
}

export function parseSrt(content: string): SrtSegment[] {
  const blocks = content.trim().split(/\n\n+/)
  const segments: SrtSegment[] = []

  for (const block of blocks) {
    const lines = block.split('\n')
    if (lines.length < 3) continue

    const timeMatch = lines[1].match(
      /(\d+:\d{2}:\d{2},\d{3})\s*-->\s*(\d+:\d{2}:\d{2},\d{3})/
    )
    if (!timeMatch) continue

    segments.push({
      id: segments.length,
      start: parseTime(timeMatch[1]),
      end: parseTime(timeMatch[2]),
      text: lines.slice(2).join('\n')
    })
  }

  return segments
}

export async function saveSrtFile(filePath: string, segments: SrtSegment[]): Promise<void> {
  const content = segmentsToSrt(segments)
  await writeFile(filePath, content, 'utf-8')
}

export async function loadSrtFile(filePath: string): Promise<SrtSegment[]> {
  const content = await readFile(filePath, 'utf-8')
  return parseSrt(content)
}
