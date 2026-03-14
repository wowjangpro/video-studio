interface TimelineRulerProps {
  totalDuration: number
  zoom: number
  labelOffset: number
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function TimelineRuler({ totalDuration, zoom, labelOffset }: TimelineRulerProps): JSX.Element {
  let interval: number
  if (zoom >= 30) interval = 5
  else if (zoom >= 15) interval = 10
  else if (zoom >= 8) interval = 30
  else if (zoom >= 4) interval = 60
  else interval = 120

  const marks: number[] = []
  for (let t = 0; t <= totalDuration; t += interval) {
    marks.push(t)
  }

  return (
    <div className="timeline-ruler">
      {marks.map((time) => (
        <div
          key={time}
          className="timeline-ruler__mark"
          style={{ left: labelOffset + time * zoom }}
        >
          <div className="timeline-ruler__tick" />
          <span className="timeline-ruler__label">{formatTime(time)}</span>
        </div>
      ))}
    </div>
  )
}
