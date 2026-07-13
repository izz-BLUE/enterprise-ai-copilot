import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const baseUrl = (__ENV.BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');
const vus = Number(__ENV.VUS || 6);
const iterations = Number(__ENV.ITERATIONS || 12);
const question = __ENV.QUESTION || '几点上班？';
const expectRejection = (__ENV.EXPECT_REJECTION || 'true') === 'true';

const successful = new Counter('ai_successful');
const rejected = new Counter('ai_rejected');
const unexpected = new Counter('unexpected_response');

http.setResponseCallback(http.expectedStatuses(200, 429));

const thresholds = {
  unexpected_response: ['count==0'],
  http_req_duration: ['p(95)<45000'],
};
if (expectRejection) {
  thresholds.ai_rejected = ['count>0'];
}

export const options = {
  scenarios: {
    bounded_ai_overload: {
      executor: 'shared-iterations',
      vus,
      iterations,
      maxDuration: __ENV.MAX_DURATION || '2m',
    },
  },
  thresholds,
};

export default function () {
  const response = http.post(
    `${baseUrl}/api/chat`,
    JSON.stringify({ message: question }),
    {
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: __ENV.REQUEST_TIMEOUT || '50s',
      tags: { layer: 'application', scenario: 'ai_overload' },
    },
  );

  let body = {};
  try {
    body = response.json();
  } catch (_) {
    unexpected.add(1);
  }

  let passed;
  if (response.status === 200) {
    successful.add(1);
    passed = check(response, {
      'successful response is valid': (result) => (
        body.success === true
        && body.traceId === result.headers['X-Trace-Id']
      ),
    });
  } else if (response.status === 429) {
    rejected.add(1);
    passed = check(response, {
      'overload response is explicit': (result) => (
        body.success === false
        && body.traceId === result.headers['X-Trace-Id']
      ),
      'retry-after is present': (result) => result.headers['Retry-After'] === '1',
    });
  } else {
    passed = false;
  }

  if (!passed) {
    unexpected.add(1);
  }
}
