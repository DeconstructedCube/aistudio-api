export function formatDate(
  date?: string | Date | null,
  opts: { withSeconds?: boolean } = {}
): string {
  if (!date) return '-'
  const d = typeof date === 'string' ? new Date(date) : date
  if (Number.isNaN(d.getTime())) {
    return typeof date === 'string' ? date : '-'
  }
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    ...(opts.withSeconds ? { second: '2-digit' } : {}),
  })
}

export function formatTokenCount(tokens?: number | null): string {
  if (tokens === null || tokens === undefined || Number.isNaN(tokens)) return '-'
  if (tokens >= 10_000) {
    const wan = tokens / 10_000
    return `${Number.isInteger(wan) ? wan : wan.toFixed(1)}万`
  }
  return tokens.toLocaleString()
}
