import http from 'k6/http';
import { check } from 'k6';

const baseUrl = (__ENV.BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');

export const options = {
  vus: Number(__ENV.VUS || 2),
  duration: __ENV.DURATION || '2m',
  thresholds: {
    checks: ['rate==1'],
    http_req_failed: ['rate==0'],
    http_req_duration: ['p(95)<1000'],
  },
};

export default function () {
  const response = http.get(`${baseUrl}/api/health`, {
    tags: { layer: 'application', scenario: 'health_soak' },
  });
  check(response, {
    'health is UP': (result) => result.status === 200 && result.json('status') === 'UP',
  });
}
