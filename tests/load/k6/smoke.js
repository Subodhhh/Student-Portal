// Readiness smoke of the load-test stack (no login, no writes): every API service's public /health through the project network.
// Run: tests/load/lt.sh k6 smoke.js   (co-located on Computer 1; results are remote-written to Prometheus and summarised in results/)
import http from "k6/http";
import { check } from "k6";

const TARGETS = {
  auth: "http://student-authentication-service:8000/health",
  enrollment: "http://student-enrollment-service:8001/health",
  test: "http://student-test-service:8001/health",
  attempt: "http://student-attempt-service:8001/health",
  result: "http://student-result-service:8001/health",
  notification: "http://student-notification-service:8001/health",
  gateway: "http://institute-gateway/health",
};

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: { checks: ["rate==1"] },
};

export default function () {
  for (const [name, url] of Object.entries(TARGETS)) {
    const r = http.get(url, { tags: { service: name } });
    check(r, { [`${name} healthy`]: (res) => res.status === 200 });
  }
}

export function handleSummary(data) {
  return { "/results/smoke-summary.json": JSON.stringify(data, null, 2) };
}
