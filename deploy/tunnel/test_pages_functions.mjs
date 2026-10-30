// Offline tests for the demo proxy chain: both Pages Functions (with Workers KV and fetch mocked) and the edge route
// table in edge.conf.template. No network, no Cloudflare account.   Run:  node --test deploy/tunnel/test_pages_functions.mjs
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const STUDENT_FN = join(ROOT, "student-frontend", "functions", "api", "[[path]].js");
const INSTITUTE_FN = join(ROOT, "institute-frontend", "functions", "api", "v1", "[[path]].js");
const EDGE = "https://quiet-river-1234.trycloudflare.com";
const KEY = "k".repeat(40);
const STUDENT_ORIGIN = "https://student-demo.pages.dev";
const INSTITUTE_ORIGIN = "https://institute-demo.pages.dev";

let loadCounter = 0;
async function load(file) {
  // A fresh module instance per test: the tunnel-origin cache is module state.
  return import(`${pathToFileURL(file).href}?n=${++loadCounter}`);
}

function kv(value) {
  const store = { reads: 0 };
  store.get = async (key) => { store.reads += 1; return key === "edge_origin" ? value : null; };
  return store;
}

function mockFetch(respond) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init, headers: new Headers(init.headers) });
    return respond(calls[calls.length - 1]);
  };
  return { calls, restore: () => { globalThis.fetch = original; } };
}

function request(url, { method = "GET", headers = {}, body } = {}) {
  return new Request(url, { method, headers, body });
}

test("both Functions share an identical proxy core", () => {
  const core = (f) => readFileSync(f, "utf8").split("// ---- shared proxy core")[1];
  assert.ok(core(STUDENT_FN) && core(STUDENT_FN) === core(INSTITUTE_FN));
});

test("student: forwards to the edge with query, cookies, edge key and the Cloudflare client IP only", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } }));
  try {
    const res = await onRequest({
      request: request(`${STUDENT_ORIGIN}/api/tests/scheduled?provider_id=p1&x=2`, {
        headers: {
          cookie: "session_id=abc", "cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "6.6.6.6",
          "x-real-ip": "6.6.6.6", "x-client-ip": "6.6.6.6", "x-edge-key": "client-chosen", authorization: "Bearer x",
          host: "evil.example",
        },
      }),
      env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY },
    });
    assert.equal(res.status, 200);
    assert.equal(f.calls.length, 1);
    const { url, headers, init } = f.calls[0];
    assert.equal(url, `${EDGE}/student/tests/scheduled?provider_id=p1&x=2`);
    assert.equal(headers.get("cookie"), "session_id=abc");
    assert.equal(headers.get("x-edge-key"), KEY);
    assert.equal(headers.get("x-client-ip"), "198.51.100.7");
    for (const h of ["x-forwarded-for", "x-real-ip", "authorization", "cf-connecting-ip"]) assert.equal(headers.get(h), null, h);
    assert.equal(init.redirect, "manual");
    assert.ok(init.signal instanceof AbortSignal);
    assert.equal(res.headers.get("cache-control"), "no-store");
  } finally { f.restore(); }
});

test("student: without CF-Connecting-IP no client IP is invented", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => new Response("{}", { status: 200 }));
  try {
    await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/me`, { headers: { "x-client-ip": "6.6.6.6" } }),
      env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(f.calls[0].headers.get("x-client-ip"), null);
  } finally { f.restore(); }
});

test("same-origin POST: body bytes, content type and CSRF header pass through unchanged", async () => {
  const { onRequest } = await load(INSTITUTE_FN);
  const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 10, 13]);
  const f = mockFetch(() => new Response(null, { status: 204 }));
  try {
    const res = await onRequest({
      request: request(`${INSTITUTE_ORIGIN}/api/v1/branches/b1/subjects/s1/papers/upload`, {
        method: "POST", body: bytes,
        headers: { origin: INSTITUTE_ORIGIN, "content-type": "multipart/form-data; boundary=zz", "x-csrf-token": "t1",
          "idempotency-key": "i1", cookie: "__Host-inst_sid=s" },
      }),
      env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY },
    });
    assert.equal(res.status, 204);
    const call = f.calls[0];
    assert.equal(call.url, `${EDGE}/institute/api/v1/branches/b1/subjects/s1/papers/upload`);
    assert.deepEqual(new Uint8Array(call.init.body), bytes);
    assert.equal(call.headers.get("content-type"), "multipart/form-data; boundary=zz");
    assert.equal(call.headers.get("x-csrf-token"), "t1");
    assert.equal(call.headers.get("idempotency-key"), "i1");
    assert.equal(call.headers.get("origin"), INSTITUTE_ORIGIN);
  } finally { f.restore(); }
});

test("state-changing requests from another origin (or without Origin) are refused before reaching the edge", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => new Response("{}"));
  try {
    for (const headers of [{}, { origin: "https://evil.example" }, { origin: "null" }]) {
      const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/login`, { method: "POST", body: "{}", headers }),
        env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
      assert.equal(res.status, 403);
    }
    assert.equal(f.calls.length, 0);
  } finally { f.restore(); }
});

test("every Set-Cookie header survives; server-identifying headers do not", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => {
    const h = new Headers({ "content-type": "application/json", server: "uvicorn", "x-powered-by": "x", via: "1.1 edge" });
    h.append("set-cookie", "session_id=abc; HttpOnly; Path=/; SameSite=lax; Secure");
    h.append("set-cookie", "other=1; Path=/; Secure");
    return new Response('{"message":"Login successful"}', { status: 200, headers: h });
  });
  try {
    const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/login`,
      { method: "POST", body: "{}", headers: { origin: STUDENT_ORIGIN, "content-type": "application/json" } }),
    env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.deepEqual(res.headers.getSetCookie(), ["session_id=abc; HttpOnly; Path=/; SameSite=lax; Secure", "other=1; Path=/; Secure"]);
    for (const h of ["server", "x-powered-by", "via"]) assert.equal(res.headers.get(h), null, h);
    assert.equal(await res.text(), '{"message":"Login successful"}');
  } finally { f.restore(); }
});

test("upstream redirects are never followed or passed on (no Location leak)", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => new Response(null, { status: 307, headers: { location: `${EDGE}/student/tests` } }));
  try {
    const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/tests/`), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("location"), null);
    assert.ok(!(await res.text()).includes("trycloudflare"));
  } finally { f.restore(); }
});

test("a tampered or malformed KV value never becomes a proxy target", async () => {
  const bad = ["https://evil.example", "http://quiet-river-1234.trycloudflare.com", "https://x.trycloudflare.com.evil.example",
    "https://x.trycloudflare.com/path", "https://user@x.trycloudflare.com", "https://-x.trycloudflare.com", "", null,
    "https://x.trycloudflare.com:8443", "javascript:alert(1)"];
  for (const value of bad) {
    const { onRequest } = await load(STUDENT_FN);
    const f = mockFetch(() => new Response("{}"));
    try {
      const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/me`), env: { TUNNEL_KV: kv(value), EDGE_KEY: KEY } });
      assert.equal(res.status, 503, String(value));
      assert.equal(f.calls.length, 0, String(value));
    } finally { f.restore(); }
  }
});

test("missing or weak edge secret, or missing KV binding: 503 without contacting anything", async () => {
  for (const env of [{ TUNNEL_KV: kv(EDGE) }, { TUNNEL_KV: kv(EDGE), EDGE_KEY: "short" }, { EDGE_KEY: KEY }]) {
    const { onRequest } = await load(STUDENT_FN);
    const f = mockFetch(() => new Response("{}"));
    try {
      const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/me`), env });
      assert.equal(res.status, 503);
      assert.equal(f.calls.length, 0);
    } finally { f.restore(); }
  }
});

test("student: only the Student API surface is proxied; internal, docs, health and traversal paths are 404", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const f = mockFetch(() => new Response("{}"));
  try {
    for (const path of ["/api/internal/v1/students", "/api/docs", "/api/openapi.json", "/api/health", "/api/ready", "/api/",
      "/api", "/api/tests/%2e%2e/internal/v1/x", "/api/tests/..%2Finternal", "/api/tests//x", "/api/tests/%5Cx", "/api/admin"]) {
      const res = await onRequest({ request: request(`${STUDENT_ORIGIN}${path}`), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
      assert.equal(res.status, 404, path);
    }
    assert.equal(f.calls.length, 0);
  } finally { f.restore(); }
});

test("methods outside GET/POST/PUT/PATCH/DELETE are refused", async () => {
  const { onRequest } = await load(INSTITUTE_FN);
  for (const method of ["OPTIONS", "HEAD"]) {   // (TRACE/CONNECT cannot even be constructed by a Request)
    const res = await onRequest({ request: request(`${INSTITUTE_ORIGIN}/api/v1/auth/me`, { method }),
      env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(res.status, 405, method);
  }
});

test("body limits: 1 MiB for the Student portal, 3 MiB (paper uploads) for the Institute portal", async () => {
  const f = mockFetch(() => new Response("{}"));
  try {
    const student = await load(STUDENT_FN);
    const big = new Uint8Array(1024 * 1024 + 1);
    let res = await student.onRequest({ request: request(`${STUDENT_ORIGIN}/api/profile`, { method: "PUT", body: big,
      headers: { origin: STUDENT_ORIGIN } }), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(res.status, 413);
    const institute = await load(INSTITUTE_FN);
    res = await institute.onRequest({ request: request(`${INSTITUTE_ORIGIN}/api/v1/questions`, { method: "POST",
      body: new Uint8Array(2.5 * 1024 * 1024), headers: { origin: INSTITUTE_ORIGIN } }), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(res.status, 200);
    res = await institute.onRequest({ request: request(`${INSTITUTE_ORIGIN}/api/v1/questions`, { method: "POST",
      body: new Uint8Array(3 * 1024 * 1024 + 1), headers: { origin: INSTITUTE_ORIGIN } }), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
    assert.equal(res.status, 413);
    assert.equal(f.calls.length, 1);
  } finally { f.restore(); }
});

test("timeouts answer 504 and network failures 502, without exposing the tunnel", async () => {
  for (const [error, status] of [[new DOMException("timed out", "TimeoutError"), 504], [new TypeError("fetch failed"), 502]]) {
    const { onRequest } = await load(STUDENT_FN);
    const f = mockFetch(() => { throw error; });
    try {
      const res = await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/me`), env: { TUNNEL_KV: kv(EDGE), EDGE_KEY: KEY } });
      assert.equal(res.status, status);
      assert.ok(!(await res.text()).includes("trycloudflare"));
    } finally { f.restore(); }
  }
});

test("the tunnel origin is cached briefly (one KV read for consecutive requests)", async () => {
  const { onRequest } = await load(STUDENT_FN);
  const store = kv(EDGE);
  const f = mockFetch(() => new Response("{}"));
  try {
    for (let i = 0; i < 3; i++) await onRequest({ request: request(`${STUDENT_ORIGIN}/api/auth/me`), env: { TUNNEL_KV: store, EDGE_KEY: KEY } });
    assert.equal(store.reads, 1);
  } finally { f.restore(); }
});

// ---------------------------------------------------------------------------------------------------------- edge routes
const template = readFileSync(join(ROOT, "deploy", "tunnel", "edge.conf.template"), "utf8");
const routeMap = template.split('map "$request_method $uri" $edge_upstream {')[1].split("\n}")[0];
const routes = [...routeMap.matchAll(/^\s*"~([^"]+)"\s+"([^"]+)";/gm)].map(([, re, upstream]) => ({ re: new RegExp(re), upstream }));
const upstreamFor = (method, uri) => routes.find((r) => r.re.test(`${method} ${uri}`))?.upstream ?? null;

test("edge: the route table parsed", () => assert.ok(routes.length >= 15));

test("edge: every Student API path the frontend builds is routed to its owning service", () => {
  const files = [];
  const walk = (d) => { for (const n of readdirSync(d)) { const p = join(d, n); statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(n) && files.push(p); } };
  walk(join(ROOT, "student-frontend", "src"));
  const owner = { AUTH: "student-authentication-service", PROFILE: "student-profile-service", ENROLLMENT: "student-enrollment-service",
    TEST: "student-test-service", ATTEMPT: "student-attempt-service", RESULT: "student-result-service",
    RANKING: "student-ranking-service", NOTIFICATION: "student-notification-service" };
  const found = new Set();
  for (const f of files) {
    for (const [, svc, path] of readFileSync(f, "utf8").matchAll(/\$\{(\w+)_API_URL\}(\/[^`"'?\s]*)/g)) {
      const uri = "/student" + path.replace(/\$\{[^}]+\}/g, "x1");
      const hit = ["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => upstreamFor(m, uri)).find(Boolean);
      assert.ok(hit, `frontend path ${uri} (${f}) is not routed by the edge`);
      assert.ok(hit.startsWith(owner[svc]), `${uri} routed to ${hit}, expected ${owner[svc]}`);
      found.add(uri);
    }
  }
  assert.ok(found.size >= 20, `only ${found.size} frontend API paths found`);
});

test("edge: method-specific Student routes", () => {
  const cases = [
    ["POST", "/student/auth/login", "student-authentication-service"], ["GET", "/student/auth/login", null],
    ["PUT", "/student/profile", "student-profile-service"], ["DELETE", "/student/profile", null],
    ["GET", "/student/tests/t1/start", "student-test-service"], ["POST", "/student/tests/t1/audiences", null],
    ["GET", "/student/tests/t1/audiences", null],
    ["POST", "/student/attempts/a1/questions/q1/mark-review", "student-attempt-service"],
    ["GET", "/student/attempts/a1/workspace", "student-attempt-service"],
    ["GET", "/student/attempts/tests/t1/papers", "student-attempt-service"], ["POST", "/student/attempts/tests/t1/papers", null],
    ["PUT", "/student/attempts/tests/t1/selection", "student-attempt-service"], ["GET", "/student/attempts/tests/t1/selection", null],
    ["POST", "/student/attempts/a1/integrity-events", "student-attempt-service"],
    ...["GET", "PUT", "PATCH", "DELETE"].map((m) => [m, "/student/attempts/a1/integrity-events", null]),
    ["PATCH", "/student/notifications/n1/read", "student-notification-service"], ["POST", "/student/notifications", null],
    ["POST", "/student/results/attempts/a1/evaluate", "student-result-service"],
  ];
  for (const [m, uri, expected] of cases) assert.equal(upstreamFor(m, uri)?.split(":")[0] ?? null, expected, `${m} ${uri}`);
});

test("edge: internal, documentation, health and unknown paths are never routed", () => {
  const blocked = ["/student/internal/v1/students", "/student/auth/internal/v1/x", "/student/docs", "/student/openapi.json",
    "/student/health", "/student/ready", "/student/auth/students", "/internal/v1/reference/sync", "/docs", "/openapi.json",
    "/health", "/institute/health", "/institute/internal/v1/introspect", "/institute/docs", "/institute/openapi.json",
    "/institute/api/v2/x", "/", "/student/", "/student/tests/t1/x/y"];
  for (const uri of blocked) for (const m of ["GET", "POST", "PUT", "PATCH", "DELETE"]) assert.equal(upstreamFor(m, uri), null, `${m} ${uri}`);
  assert.equal(upstreamFor("GET", "/institute/api/v1/auth/me"), "institute-gateway:80");
  assert.equal(upstreamFor("OPTIONS", "/institute/api/v1/auth/me"), null);
});

test("edge: secrets and spoofable headers are never passed to a backend", () => {
  for (const h of ["X-Edge-Key", "X-Client-IP", "CF-Connecting-IP", "Forwarded", "X-Forwarded-Host"]) {
    assert.match(template, new RegExp(`proxy_set_header ${h} "";`), h);
  }
  assert.match(template, /proxy_set_header X-Forwarded-For \$edge_client_ip;/);
  assert.match(template, /if \(\$edge_authorized = 0\) \{\s*return 404/);
});
