import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const baseUrl = (__ENV.BASE_URL || 'https://copilot.jintianchi.cn').replace(/\/$/, '');
const rate = Number(__ENV.RATE || 10);
const duration = __ENV.DURATION || '5s';

const accepted = new Counter('accepted');
const rateLimited = new Counter('rate_limited');
const unexpectedStatus = new Counter('unexpected_status');

http.setResponseCallback(http.expectedStatuses(200, 429));

export const options = {
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
    unexpected_status: ['count==0'],
    accepted: ['count>0'],
    rate_limited: ['count>0'],
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
  } else {
    unexpectedStatus.add(1);
  }

  check(response, {
    'status is 200 or 429': (result) => result.status === 200 || result.status === 429,
  });
}
