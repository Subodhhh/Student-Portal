/* Splitting stored question text into text and LaTeX segments, and strict KaTeX rendering (used by MathText and the import review). */
import katex from "katex";

export type MathPart = { kind: "text"; value: string } | { kind: "math"; value: string; raw: string; display: boolean };

const OPTIONS = { strict: "error", throwOnError: true, maxExpand: 1000, maxSize: 20, output: "html" } as const;
const PAIRS: [string, string][] = [["$$", "$$"], ["\\[", "\\]"], ["\\(", "\\)"]];

export function splitMath(text: string): MathPart[] {
  const out: MathPart[] = [];
  const n = text.length;
  let last = 0;
  let i = 0;
  while (i < n) {
    if (text.startsWith("\\$", i)) { i += 2; continue; }
    let end = -1;
    let width = 0;
    let display = false;
    const pair = PAIRS.find(([open]) => text.startsWith(open, i));
    if (pair) {
      const j = text.indexOf(pair[1], i + 2);
      if (j > i + 2) { end = j + 2; width = 2; display = pair[0] !== "\\("; }
    } else if (text[i] === "$" && i + 1 < n && !/\s/.test(text[i + 1])) {
      let j = i + 1;
      while ((j = text.indexOf("$", j)) !== -1) {
        if (!/\s/.test(text[j - 1]) && text[j - 1] !== "\\" && !(j + 1 < n && /\d/.test(text[j + 1]))) { end = j + 1; width = 1; break; }
        j += 1;
      }
    }
    if (end > 0) {
      if (i > last) out.push({ kind: "text", value: text.slice(last, i) });
      const raw = text.slice(i, end);
      out.push({ kind: "math", raw, value: raw.slice(width, raw.length - width), display });
      i = last = end;
    } else {
      i += 1;
    }
  }
  if (last < n) out.push({ kind: "text", value: text.slice(last) });
  return out;
}

const cache = new Map<string, string | Error>();

/** KaTeX HTML for one segment, or the error KaTeX raised (strict mode). */
export function renderMath(latex: string, display = false): string | Error {
  const key = `${display ? "D" : "I"}${latex}`;
  let hit = cache.get(key);
  if (hit === undefined) {
    let untrusted = "";
    try {
      // trust is refused for every command that would need it (\href, \url, \includegraphics, \htmlClass ...); KaTeX then draws the
      // command as red source text instead of throwing, so the attempt is recorded and the segment treated as not displayable.
      const html = katex.renderToString(latex, { ...OPTIONS, displayMode: display, trust: (ctx : any) => { untrusted = ctx.command; return false; } });
      hit = untrusted ? new Error(`${untrusted} is not allowed`) : html;
    } catch (e) {
      hit = e instanceof Error ? e : new Error(String(e));
    }
    if (cache.size > 2000) cache.clear();
    cache.set(key, hit);
  }
  return hit as string | Error;
}

/** Problems that would stop a text from rendering (used to validate a provider's correction before it can be accepted). */
export function mathProblems(text: string): string[] {
  return splitMath(text).flatMap((p) => {
    if (p.kind !== "math") return [];
    const r = renderMath(p.value, p.display);
    return r instanceof Error ? [`${p.raw}: ${r.message.replace(/^KaTeX parse error: /, "")}`] : [];
  });
}

