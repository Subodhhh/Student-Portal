// Stage 2 baseline: shared by the k6 scripts run from Computer 2. Everything goes through the service APIs, never PostgreSQL.
import http from "k6/http";
import { check, fail } from "k6";
import { Rate } from "k6/metrics";
import { SharedArray } from "k6/data";

const HOST = __ENV.LT_HOST || fail("set LT_HOST (Computer 1's address)");
const PORTS = { auth: 18002, profile: 18003, enrollment: 18004, test: 18005, attempt: 18006, result: 18007, ranking: 18008, notification: 18009 };
export const url = (service, path) => `http://${HOST}:${PORTS[service]}${path}`;
export const users = new SharedArray("users", () => JSON.parse(open("./data/users.json")));

export const timeouts = new Rate("timeouts");
export const nonEmpty = new Rate("non_empty");     // proves a list had rows: an empty list is fast and must not pass as a good result
const TIMEOUT = "30s";

// Logins run one at a time, so the 5-per-IP limiter (cleared by each success) is never hit by this setup. Sessions live 30 minutes, not renewed.
export function loginAll(count) {
  if (!__ENV.LT_PASSWORD) fail("set LT_PASSWORD");
  const sessions = [];
  for (let i = 0; i < count; i++) {
    const r = http.post(url("auth", "/auth/login"), JSON.stringify({ login: users[i].id, password: __ENV.LT_PASSWORD }),
      { headers: { "Content-Type": "application/json" }, tags: { name: "POST /auth/login (setup)" }, timeout: TIMEOUT });
    if (r.status !== 200 || !r.cookies.session_id) fail(`login of ${users[i].id} returned ${r.status}: nothing was tested`);
    sessions.push(r.cookies.session_id[0].value);
  }
  return sessions;
}

export function get(session, service, path, name, expected = [200]) {
  const r = http.get(url(service, path), { cookies: { session_id: session }, tags: { name }, timeout: TIMEOUT,
    responseCallback: http.expectedStatuses(...expected) });
  timeouts.add(r.error_code === 1050, { name });
  check(r, { [`${name} status`]: (x) => expected.includes(x.status) });
  return r;
}

export function list(r, label) {
  const rows = r.status === 200 ? r.json() : [];
  nonEmpty.add(rows.length > 0, { list: label });
  return rows;
}

// Named so every endpoint gets its own percentiles and error rate in summary.json. These thresholds always pass, except the stop conditions.
export function metricOptions(names, lists) {
  const t = { http_req_failed: [{ threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "1m" }], timeouts: ["rate<0.05"] };
  for (const n of names) {
    t[`http_req_duration{name:${n}}`] = ["max>=0"];
    t[`http_req_failed{name:${n}}`] = ["rate>=0"];
    t[`http_reqs{name:${n}}`] = ["count>=0"];
  }
  for (const l of lists) t[`non_empty{list:${l}}`] = ["rate>=0"];
  return t;
}
export const TREND_STATS = ["avg", "min", "p(50)", "p(90)", "p(95)", "p(99)", "max"];
