// Cloudflare Pages Function: same-origin proxy from https://<student project>.pages.dev/api/* to the demo backend edge.
//
// The browser only ever talks to the Pages origin, so the Student session cookie stays first-party. The edge origin (the
// current Quick Tunnel URL) is read from Workers KV, so restarting the tunnel never requires a rebuild. Setup and the
// security model: docs/demo/CLOUDFLARE_DEMO.md and deploy/tunnel/README.md.
//
// Bindings (Pages project -> Settings -> Bindings / Variables and Secrets):
//   TUNNEL_KV  Workers KV namespace; key "edge_origin" = https://<random>.trycloudflare.com
//   EDGE_KEY   secret shared with the edge (deploy/tunnel/edge.env); at least 32 characters

const CONFIG = {
  mount: "/api", // path this Function is mounted on (functions/api/[[path]].js)
  upstreamPrefix: "/student", // edge route prefix for this portal
  // First path segments that exist on the Student services; the edge enforces the exact route and method list.
  allowedFirstSegments: ["auth", "profile", "enrollments", "tests", "attempts", "results", "ranking", "notifications"],
  maxBodyBytes: 1024 * 1024,
  timeoutMs: 30_000,
};

// ---- shared proxy core: keep identical in student-frontend and institute-frontend (checked by deploy/tunnel/test_pages_functions.mjs)
const EDGE_ORIGIN_KEY = "edge_origin";
const ORIGIN_CACHE_MS = 30_000;
// Only a Quick Tunnel hostname is an acceptable target: a tampered KV value can never turn this into an open proxy.
const EDGE_ORIGIN_PATTERN = /^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com$/;
const METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const FORWARD_REQUEST_HEADERS = [
  "accept", "accept-language", "content-type", "cookie", "origin", "user-agent",
  "x-csrf-token", "idempotency-key", "x-idempotency-key",
];
const DROP_RESPONSE_HEADERS = new Set([
  "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate", "proxy-connection",
  "server", "x-powered-by", "via", "alt-svc", "set-cookie", "content-length",
]);

let cachedOrigin = { value: null, at: 0 };

function jsonError(status, message, extraHeaders = {}) {
  return new Response(JSON.stringify({ detail: message }), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...extraHeaders },
  });
}

async function edgeOrigin(env) {
  const now = Date.now();
  if (cachedOrigin.value && now - cachedOrigin.at < ORIGIN_CACHE_MS) return cachedOrigin.value;
  const raw = env.TUNNEL_KV ? await env.TUNNEL_KV.get(EDGE_ORIGIN_KEY) : null;
  const value = typeof raw === "string" ? raw.trim().replace(/\/+$/, "") : "";
  if (!EDGE_ORIGIN_PATTERN.test(value)) {
    console.error("proxy: KV edge_origin is missing or not a trycloudflare.com https origin");
    return null;
  }
  cachedOrigin = { value, at: now };
  return value;
}

// Returns the part after the mount ("/tests/1"), or null for anything malformed or outside the allowed surface.
function routedPath(pathname) {
  if (!pathname.startsWith(CONFIG.mount + "/") || pathname.length > 2048) return null;
  // Encoded separators/dots and empty segments are never produced by the apps; refuse them instead of normalising.
  if (/%2f|%5c|%2e|\\|\/\/|\/\.\.?(\/|$)/i.test(pathname)) return null;
  const rest = pathname.slice(CONFIG.mount.length);
  const first = rest.split("/")[1] ?? "";
  if (CONFIG.allowedFirstSegments && !CONFIG.allowedFirstSegments.includes(first)) return null;
  return rest;
}

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  if (!METHODS.has(method)) return jsonError(405, "Method not allowed.", { allow: [...METHODS].join(", ") });

  const rest = routedPath(url.pathname);
  if (rest === null || url.search.length > 4096) return jsonError(404, "Not found.");

  // Browsers send Origin on every state-changing fetch; anything else is a cross-site or scripted request.
  if (UNSAFE_METHODS.has(method) && request.headers.get("origin") !== url.origin) {
    return jsonError(403, "Cross-origin request refused.");
  }

  const edgeKey = typeof env.EDGE_KEY === "string" ? env.EDGE_KEY : "";
  const origin = await edgeOrigin(env);
  if (edgeKey.length < 32 || !origin) return jsonError(503, "The demo backend is not available right now.");

  const target = new URL(`${origin}${CONFIG.upstreamPrefix}${rest}${url.search}`);
  if (target.origin !== origin) return jsonError(404, "Not found.");

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("x-edge-key", edgeKey);
  // CF-Connecting-IP is set by Cloudflare's edge for every request to a pages.dev host (a client cannot supply it);
  // client-sent X-Forwarded-For / X-Real-IP / X-Client-IP are never forwarded.
  const clientIp = request.headers.get("cf-connecting-ip");
  if (clientIp) headers.set("x-client-ip", clientIp);

  let body;
  if (method !== "GET") {
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (declared > CONFIG.maxBodyBytes) return jsonError(413, "Request body is too large.");
    body = await request.arrayBuffer();
    if (body.byteLength > CONFIG.maxBodyBytes) return jsonError(413, "Request body is too large.");
    if (body.byteLength === 0) body = undefined;
  }

  let upstream;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      redirect: "manual", // never follow a redirect with the edge key and session cookie attached
      signal: AbortSignal.timeout(CONFIG.timeoutMs),
    });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    console.error(`proxy: upstream ${timedOut ? "timeout" : "fetch failed"} for ${method} ${CONFIG.mount}${rest}`);
    return jsonError(timedOut ? 504 : 502, "The demo backend did not respond.");
  }

  if (upstream.status >= 300 && upstream.status < 400 && upstream.status !== 304) {
    console.error(`proxy: refused upstream redirect ${upstream.status} for ${method} ${CONFIG.mount}${rest}`);
    return jsonError(502, "Unexpected response from the demo backend.");
  }

  const out = new Headers();
  for (const [name, value] of upstream.headers) {
    if (!DROP_RESPONSE_HEADERS.has(name.toLowerCase())) out.append(name, value);
  }
  for (const cookie of upstream.headers.getSetCookie()) out.append("set-cookie", cookie);
  out.set("cache-control", "no-store");
  out.set("x-content-type-options", "nosniff");
  const noBody = upstream.status === 204 || upstream.status === 304;
  return new Response(noBody ? null : upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}
