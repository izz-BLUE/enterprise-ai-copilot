import test from 'node:test'
import assert from 'node:assert/strict'
import { isRetryableServerError } from './httpErrorPolicy.js'

test('retryable server errors share the gateway transient status set', () => {
  assert.equal(isRetryableServerError(502), true)
  assert.equal(isRetryableServerError(503), true)
  assert.equal(isRetryableServerError(504), true)
  assert.equal(isRetryableServerError('504'), true)
  assert.equal(isRetryableServerError(500), false)
  assert.equal(isRetryableServerError(400), false)
  assert.equal(isRetryableServerError(null), false)
})
