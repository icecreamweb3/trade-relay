const UTC_DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/
const UTC_DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/

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
  return parsed ? parsed.toLocaleString() : (value || '-')
}