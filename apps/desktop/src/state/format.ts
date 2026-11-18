const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const year = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })

export function shortTime(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) return time.format(date)
  if (date.getFullYear() === now.getFullYear()) return `${day.format(date)}, ${time.format(date)}`
  return year.format(date)
}

export function fileDate(iso: string): string {
  const date = new Date(iso)
  return date.getFullYear() === new Date().getFullYear() ? day.format(date) : year.format(date)
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes
  let unit = -1
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`
}

export function zonedTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return 'Not scheduled'
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZone,
    }).format(new Date(iso))
  } catch {
    return shortTime(iso)
  }
}

export function fileKind(mime: string, name: string): string {
  if (mime.startsWith('image/')) return 'Image'
  if (mime.startsWith('audio/')) return 'Audio'
  if (mime.startsWith('video/')) return 'Video'
  if (mime === 'application/pdf') return 'PDF'
  if (mime === 'text/markdown') return 'Markdown'
  const extension = name.includes('.') ? name.split('.').pop()!.toUpperCase() : ''
  if (mime.startsWith('text/')) return extension && extension.length <= 5 ? extension : 'Text'
  return extension && extension.length <= 5 ? extension : 'File'
}

const CONNECTION_STATES: Record<string, string> = {
  connected: 'Connected',
  disconnected: 'Offline',
  error: 'Needs attention',
  unknown: 'Not tested',
}

export function connectionState(state: string): string {
  return CONNECTION_STATES[state] ?? state
}
