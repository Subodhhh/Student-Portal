// Renders nginx.conf for the production image: every "__CSP__" placeholder becomes the Content-Security-Policy below, whose
// connect-src lists exactly the API origins this bundle was built with (the VITE_*_API_URL values Vite inlines). Run in the image
// build stage:  node scripts/render-nginx-conf.mjs nginx.conf /app/nginx.rendered.conf
// A malformed or non-http(s) API URL fails the build instead of producing a weaker policy.
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const PLACEHOLDER = "__CSP__";

/** Origins of every VITE_*_API_URL in `env` (sorted, unique). */
export function apiOrigins(env) {
  const out = new Set();
  for (const [name, value] of Object.entries(env)) {
    if (!/^VITE_[A-Z_]*API_URL$/.test(name) || !value) continue;
    let url;
    try { url = new URL(value); } catch { throw new Error(`${name} is not a valid URL`); }
    if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error(`${name} must be an http(s) URL`);
    out.add(url.origin);
  }
  return [...out].sort();
}

/** The policy. KaTeX needs inline style attributes (style-src 'unsafe-inline'); it never needs inline scripts, eval or remote
 *  fonts (its fonts are bundled). Images may come from data:/blob: previews, the API and https question images. */
export function contentSecurityPolicy(origins) {
  const api = origins.join(" ");
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    `img-src 'self' data: blob: https: ${api}`.trim(),
    `connect-src 'self' ${api}`.trim(),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function renderNginxConf(template, env) {
  if (!template.includes(PLACEHOLDER)) throw new Error("nginx.conf has no CSP placeholder");
  return template.split(PLACEHOLDER).join(contentSecurityPolicy(apiOrigins(env)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [, , source, target] = process.argv;
  writeFileSync(target, renderNginxConf(readFileSync(source, "utf8"), process.env));
  console.log(`wrote ${target}`);
}
