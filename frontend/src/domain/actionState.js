export const AUTHORITATIVE_TERMINAL_STATUSES = new Set([
  'SUCCEEDED',
  'CANCELLED',
  'EXPIRED',
  'FAILED',
])

export const RETRYABLE_ACTION_ERRORS = new Set([
  'ADMIN_REQUIRED',
  'ACTION_IN_PROGRESS',
  'ACTION_INTERNAL_ERROR',
  'NETWORK_ERROR',
])

export function phaseForTerminalStatus(status) {
  switch (status) {
    case 'SUCCEEDED': return 'succeeded'
    case 'CANCELLED': return 'cancelled'
    case 'EXPIRED': return 'expired'
    case 'FAILED': return 'error'
    default: return null
  }
}
