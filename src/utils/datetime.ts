import { getPreferredLocale } from '../store/uiPreferencesStore'

const UTC_DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/
const UTC_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/

const LOCAL_DATE_TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
}

export function parseUtcTimestamp(value?: string | null): Date | null {
  if (!value) return null

  const trimmed = value.trim()
  if (!trimmed) return null

  const normalized = trimmed.includes('T') ? trimmed : trimmed.replace(' ', 'T')

  if (UTC_DATE_ONLY_RE.test(normalized)) {
    const parsed = new Date(`${normalized}T00:00:00Z`)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }

  if (UTC_DATETIME_RE.test(normalized)) {
    const parsed = new Date(`${normalized}Z`)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }

  const parsed = new Date(trimmed)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatUtcTimestampToLocalString(value?: string | null): string {
  const parsed = parseUtcTimestamp(value)
  return parsed ? parsed.toLocaleString(getPreferredLocale() === 'en' ? 'en-US' : 'zh-CN', LOCAL_DATE_TIME_OPTIONS) : (value || '-')
}

/** Format a database UTC timestamp as a stable UTC+8 value for CSV exports. */
export function formatUtcTimestampToUtc8String(value?: string | null): string {
  const parsed = parseUtcTimestamp(value)
  if (!parsed) return value || ''
  const utc8 = new Date(parsed.getTime() + 8 * 60 * 60 * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${utc8.getUTCFullYear()}/${pad(utc8.getUTCMonth() + 1)}/${pad(utc8.getUTCDate())} ` +
    `${pad(utc8.getUTCHours())}:${pad(utc8.getUTCMinutes())}:${pad(utc8.getUTCSeconds())}`
}
