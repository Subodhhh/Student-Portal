/* Question text with mathematics, physics and chemistry notation typeset by KaTeX.

   Stored question text is ordinary text with LaTeX segments in \( ... \) (inline), \[ ... \] or $$ ... $$ (display), or $ ... $
   (the same rule as the import engine: "$5" is a price, not mathematics). Text outside the segments is rendered as plain React text.
   KaTeX runs in strict mode with trust off (no links, no HTML, no macros that expand without limit). A segment KaTeX refuses is shown
   exactly as stored, marked, never dropped or guessed. */
import "katex/dist/katex.min.css";

import { renderMath, splitMath } from "./mathRender";

/** Two or more fractions, or a fraction under a root: the expression needs display size to stay readable inline. */
function nestedFraction(latex: string): boolean {
  return (latex.match(/\\frac/g) ?? []).length >= 2 || /\\sqrt(\[[^\]]*\])?\{[^}]*\\frac/.test(latex);
}

export function MathText({ text, className, as: Tag = "span" }: { text: string | null | undefined; className?: string; as?: "span" | "div" | "p" | "strong" }) {
  if (!text) return null;
  const parts = splitMath(text);
  if (!parts.some((p) => p.kind === "math")) return <Tag className={className} style={text.includes("\n") ? { whiteSpace: "pre-line" } : undefined}>{text}</Tag>;
  // A field that is exactly one inline expression (an answer option such as h/p) is the formula itself: display style, so a fraction's
  // numerator and denominator are full size instead of script size; it stays in line with the option label. Maths in a sentence stays inline.
  const whole = parts.filter((p) => p.kind === "math" || p.value.trim()).length === 1;
  // a stored line break separates lines of working (each line was recognised on its own): keep it visible
  const lines = text.includes("\n") ? { whiteSpace: "pre-line" as const } : undefined;
  return (
    <Tag className={className} style={lines}>
      {parts.map((p, k) => {
        if (p.kind === "text") return <span key={k}>{p.value}</span>;
        // display style also for a fraction inside a fraction or a root (a complex fraction, a cube root of a fraction, d²y/dx² + 3 dy/dx):
        // at inline size its inner parts shrink to script size and become unreadable. Its own box makes the line grow, never overlap.
        if (!p.display && (whole || nestedFraction(p.value))) {
          // a fraction of fractions: every level full size (\dfrac), as a textbook sets it; the structure is unchanged
          const full = renderMath("\\displaystyle " + (whole ? p.value : p.value.replace(/\\frac(?=\{)/g, "\\dfrac")));
          if (!(full instanceof Error)) {
            return <span key={k} className="math-inline math-whole" style={{ display: "inline-block", margin: "0.2em 0", verticalAlign: "middle" }}
              dangerouslySetInnerHTML={{ __html: full }} />;
          }
        }
        const html = renderMath(p.value, p.display);
        if (html instanceof Error) {
          return <code key={k} className="math-fallback" title="This expression could not be displayed; it is shown exactly as stored.">{p.raw}</code>;
        }
        // KaTeX output (trust: false, strict) is escaped markup generated from the expression, never raw HTML from the text.
        return <span key={k} className={p.display ? "math-display" : "math-inline"} dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </Tag>
  );
}
