export type TimeRangePreset = '' | 'THIS_WEEK' | 'LAST_WEEK' | 'TODAY' | 'YESTERDAY'

const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000
const DAY_MS = 24 * 60 * 60 * 1000
const INPUT_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

const pad = (value: number) => String(value).padStart(2, '0')

function formatUtc8CivilTime(timestamp: number): string {
  const date = new Date(timestamp)
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
}

export function getUtc8PresetRange(
  preset: Exclude<TimeRangePreset, ''>,
  now: Date = new Date(),
): { startTime: string; endTime: string } {
  const utc8Now = new Date(now.getTime() + UTC8_OFFSET_MS)
  const today = Date.UTC(
    utc8Now.getUTCFullYear(),
    utc8Now.getUTCMonth(),
    utc8Now.getUTCDate(),
  )
  const sundayOffset = utc8Now.getUTCDay()

  let start = today
  let durationDays = 1
  if (preset === 'YESTERDAY') start -= DAY_MS
  if (preset === 'THIS_WEEK') {
    start -= sundayOffset * DAY_MS
    durationDays = 7
  }
  if (preset === 'LAST_WEEK') {
    start -= (sundayOffset + 7) * DAY_MS
    durationDays = 7
  }

  return {
    startTime: formatUtc8CivilTime(start),
    endTime: formatUtc8CivilTime(start + durationDays * DAY_MS - 1000),
  }
}

/** Convert a timezone-less form value displayed as UTC+8 into a UTC DB value. */
export function utc8InputToUtcDatabase(value: string): string | undefined {
  if (!value) return undefined
  const match = INPUT_RE.exec(value.trim())
  if (!match) return undefined
  const [, year, month, day, hour, minute, second = '00'] = match
  const utcTimestamp = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  ) - UTC8_OFFSET_MS
  const date = new Date(utcTimestamp)
  if (Number.isNaN(date.getTime())) return undefined
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
}
