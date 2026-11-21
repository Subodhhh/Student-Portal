// Read-only browsing in frontend order. No route called here writes to PostgreSQL. Think time 3-8 s between pages.
//   LT_VUS (10), LT_RAMP (1m), LT_HOLD (3m), LT_DOWN (30s)
import { sleep } from "k6";
import { get, list, loginAll, metricOptions, TREND_STATS } from "./lib.js";

const VUS = Number(__ENV.LT_VUS || 10);
const NAMES = ["GET /auth/me", "GET /profile", "GET /notifications/unread-count", "GET /results", "GET /enrollments", "GET /tests", "GET /attempts/test/{id}",
  "GET /tests/scheduled", "GET /tests/missed", "GET /attempts/completed", "GET /tests/{id}/details", "GET /tests/{id}/start"];

export const options = {
  setupTimeout: "10m",
  summaryTrendStats: TREND_STATS,
  thresholds: metricOptions(NAMES, ["tests", "scheduled", "missed", "completed", "results", "enrollments"]),
  scenarios: { browse: { executor: "ramping-vus", startVUs: 0, gracefulRampDown: "30s", stages: [
    { duration: __ENV.LT_RAMP || "1m", target: VUS }, { duration: __ENV.LT_HOLD || "3m", target: VUS }, { duration: __ENV.LT_DOWN || "30s", target: 0 }] } },
};

export const setup = () => ({ sessions: loginAll(VUS) });
const think = () => sleep(3 + Math.random() * 5);

export default function (data) {
  const s = data.sessions[(__VU - 1) % data.sessions.length];
  // Shell and Dashboard
  get(s, "auth", "/auth/me", "GET /auth/me");
  get(s, "profile", "/profile", "GET /profile");
  get(s, "notification", "/notifications/unread-count", "GET /notifications/unread-count");
  list(get(s, "result", "/results", "GET /results"), "results");
  think();
  // Tests page: one attempt lookup per listed test, as the frontend does
  list(get(s, "enrollment", "/enrollments", "GET /enrollments"), "enrollments");
  const open = list(get(s, "test", "/tests", "GET /tests"), "tests");
  for (const t of open) get(s, "attempt", `/attempts/test/${t.test_id}`, "GET /attempts/test/{id}", [200, 404]);
  think();
  // Scheduled page
  list(get(s, "test", "/tests/scheduled", "GET /tests/scheduled"), "scheduled");
  list(get(s, "test", "/tests/missed", "GET /tests/missed"), "missed");
  const done = list(get(s, "attempt", "/attempts/completed", "GET /attempts/completed"), "completed");
  think();
  // Details of a past test; the paper of an open test is readable without starting an attempt
  if (done.length) get(s, "test", `/tests/${done[Math.floor(Math.random() * done.length)].test_id}/details`, "GET /tests/{id}/details");
  if (open.length) get(s, "test", `/tests/${open[0].test_id}/start`, "GET /tests/{id}/start");
  think();
}
