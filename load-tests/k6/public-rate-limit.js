import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const baseUrl = (__ENV.BASE_URL || 'https://copilot.jintianchi.cn').replace(/\/$/, '');
const rate = Number(__ENV.RATE || 10);
const duration = __ENV.DURATION || '5s';

const accepted = new Counter('accepted');
const rateLimited = new Counter('rate_limited');
const unexpectedStatus = new Counter('unexpected_status');
const invalidRateLimitResponse = new Counter('invalid_rate_limit_response');

http.setResponseCallback(http.expectedStatuses(200, 429));

function headerValue(headers, name) {
  const expected = name.toLowerCase();
  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === expected);
  return entry ? entry[1] : '';
}

export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  scenarios: {
    nginx_burst: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs: 20,
      maxVUs: 50,
    },
  },
  thresholds: {
    checks: ['rate==1'],
    unexpected_status: ['count==0'],
    accepted: ['count>0'],
    rate_limited: ['count>0'],
    invalid_rate_limit_response: ['count==0'],
  },
};

export default function () {
  const response = http.get(`${baseUrl}/api/health`, {
    tags: { layer: 'nginx', scenario: 'public_rate_limit' },
  });

  if (response.status === 200) {
    accepted.add(1);
  } else if (response.status === 429) {
    rateLimited.add(1);

    const contentType = headerValue(response.headers, 'Content-Type');
    const retryAfter = headerValue(response.headers, 'Retry-After');
    const server = headerValue(response.headers, 'Server');
    let body = null;

    try {
      body = response.json();
    } catch {
      // The checks below report a stable contract failure instead of aborting the VU.
    }

    const hasValidContract = contentType.toLowerCase().includes('application/json')
      && retryAfter === '1'
      && body?.success === false
      && typeof body?.answer === 'string'
      && body.answer.length > 0
      && typeof body?.traceId === 'string'
      && body.traceId.length > 0
      && !/nginx\/[0-9.]+/i.test(server)
      && !/nginx\/[0-9.]+/i.test(response.body || '');

    if (!hasValidContract) {
      invalidRateLimitResponse.add(1);
    }

    check(response, {
      '429 uses JSON': () => contentType.toLowerCase().includes('application/json'),
      '429 includes Retry-After': () => retryAfter === '1',
      '429 has compatible error body': () => body?.success === false
        && typeof body?.answer === 'string'
        && body.answer.length > 0
        && typeof body?.traceId === 'string'
        && body.traceId.length > 0,
      '429 does not expose nginx version': () => !/nginx\/[0-9.]+/i.test(server)
        && !/nginx\/[0-9.]+/i.test(response.body || ''),
    });
  } else {
    unexpectedStatus.add(1);
  }

  check(response, {
    'status is 200 or 429': (result) => result.status === 200 || result.status === 429,
  });
}
