import http from 'k6/http';
import { fail } from 'k6';

export function loginForLoadTest(baseUrl) {
  if (__ENV.ACCESS_TOKEN) return { accessToken: __ENV.ACCESS_TOKEN };
  if (!__ENV.K6_USERNAME || !__ENV.K6_PASSWORD) {
    fail('Set ACCESS_TOKEN or both K6_USERNAME and K6_PASSWORD for authenticated load tests.');
  }
  const response = http.post(`${baseUrl}/api/auth/login`, JSON.stringify({
    username: __ENV.K6_USERNAME,
    password: __ENV.K6_PASSWORD,
  }), {
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    tags: { layer: 'setup', scenario: 'authentication' },
  });
  if (response.status !== 200 || !response.json('accessToken')) {
    fail(`Load-test login failed with HTTP ${response.status}.`);
  }
  return { accessToken: response.json('accessToken') };
}

export function authenticatedHeaders(data) {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${data.accessToken}`,
  };
}
