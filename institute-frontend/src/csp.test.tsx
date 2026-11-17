// Content-Security-Policy of the production image (security workstream S): the policy itself, the nginx template, and
// compatibility with what the app really does - KaTeX output (inline style attributes, bundled fonts), the entry page (no inline
// scripts, no third-party resources) and the API origins the bundle calls.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PLACEHOLDER, apiOrigins, contentSecurityPolicy, renderNginxConf } from "../scripts/render-nginx-conf.mjs";
import { MathText } from "./components/MathText";
import nginxConf from "../nginx.conf?raw";
import indexHtml from "../index.html?raw";

const directives = (policy: string) => Object.fromEntries(policy.split("; ").map((d) => { const [k, ...v] = d.split(" "); return [k, v]; }));

describe("Content-Security-Policy", () => {
  const env = { VITE_X_API_URL: "http://localhost:8010/", VITE_Y_API_URL: "https://api.example.org/v1", OTHER: "http://ignored" };
  const policy = directives(contentSecurityPolicy(apiOrigins(env)));

  it("allows only this site's scripts and the API origins the bundle calls", () => {
    expect(policy["script-src"]).toEqual(["'self'"]);                                   // no inline scripts, no eval
    expect(policy["connect-src"]).toEqual(["'self'", "http://localhost:8010", "https://api.example.org"]);
    expect(policy["object-src"]).toEqual(["'none'"]);
    expect(policy["frame-ancestors"]).toEqual(["'none'"]);
    expect(policy["base-uri"]).toEqual(["'self'"]);
  });

  it("refuses API URLs that are not http(s)", () => {
    expect(() => apiOrigins({ VITE_X_API_URL: "javascript:alert(1)" })).toThrow();
    expect(() => apiOrigins({ VITE_X_API_URL: "not a url" })).toThrow();
  });

  it("is applied to the server and to every location of nginx.conf", () => {
    const conf = nginxConf;
    const locations = conf.match(/location [^{]+\{/g) ?? [];
    expect(conf.split(PLACEHOLDER).length - 1).toBe(locations.length + 1);
    const rendered = renderNginxConf(conf, env);
    expect(rendered).not.toContain(PLACEHOLDER);
    expect(rendered).toContain(`add_header Content-Security-Policy "${contentSecurityPolicy(apiOrigins(env))}" always;`);
  });

  it("is compatible with KaTeX: inline style attributes only, bundled fonts, no scripts", async () => {
    expect(policy["style-src"]).toContain("'unsafe-inline'");
    const { container } = render(<MathText text={"\\(\\frac{a}{b} + \\sqrt{x^{2}}\\)"} />);
    expect(container.querySelectorAll("script").length).toBe(0);
    expect(container.querySelector("[style]")).not.toBeNull();                         // why style-src needs 'unsafe-inline'
    // Vitest stubs CSS imports, so the stylesheet is read from disk (node API, loaded dynamically: the app has no Node types)
    const fs: { readFileSync(path: string, encoding: "utf8"): string } = await import("node:" + "fs");
    const cwd = (globalThis as unknown as { process: { cwd(): string } }).process.cwd();     // Vitest runs in the app directory
    const katexCss = fs.readFileSync(`${cwd}/node_modules/katex/dist/katex.min.css`, "utf8");
    const urls = [...katexCss.matchAll(/url\(([^)]+)\)/g)].map((m) => m[1]);
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((u) => u.startsWith("fonts/"))).toBe(true);                       // same-origin fonts after bundling
  });

  it("is compatible with the entry page: no inline scripts and no third-party resources", () => {
    const html = indexHtml;
    expect([...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].every((m) => /\bsrc=/.test(m[1]) && !m[2].trim())).toBe(true);
    expect(/(src|href)="https?:\/\//.test(html)).toBe(false);
  });
});
