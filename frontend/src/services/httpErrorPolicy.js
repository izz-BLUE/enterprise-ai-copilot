const RETRYABLE_SERVER_STATUSES = new Set([502, 503, 504])

export function isRetryableServerError(status) {
  return RETRYABLE_SERVER_STATUSES.has(Number(status))
}
