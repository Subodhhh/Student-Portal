// Network noise floor: one VU on a route with no database work. Run first; its latency is the floor under every other number.
import http from "k6/http";
import { check, sleep } from "k6";

const HOST = __ENV.LT_HOST;
export const options = { vus: 1, duration: __ENV.LT_HOLD || "1m", summaryTrendStats: ["avg", "min", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
  thresholds: { http_req_failed: ["rate<0.01"] } };

export default function () {
  check(http.get(`http://${HOST}:18005/health`, { tags: { name: "GET /health" }, timeout: "10s" }), { "health 200": (r) => r.status === 200 });
  sleep(0.2);
}
