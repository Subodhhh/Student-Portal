import { describe, expect, it } from "vitest";
import { mathProblems, renderMath } from "../mathRender";

// Cycle 2.3: Word bold-italic letters (a bold vector) arrive as \boldsymbol{...}. It must typeset with the installed KaTeX under the
// same strict, trust-refusing options every stored question uses.
describe("\\boldsymbol", () => {
  it.each(["\\boldsymbol{F}=m\\boldsymbol{a}", "\\boldsymbol{AB}", "\\boldsymbol{v}=2", "\\vec{F} = \\boldsymbol{F}"])("typesets %s", (latex) => {
    const html = renderMath(latex);
    expect(html).not.toBeInstanceOf(Error);
    expect(html as string).toContain("boldsymbol");                                     // KaTeX's bold-italic class
  });

  it("keeps bold upright and bold italic distinct", () => {
    expect(renderMath("\\mathbf{F}")).not.toEqual(renderMath("\\boldsymbol{F}"));
  });

  it("still refuses what strict mode refuses", () => {
    expect(mathProblems("\\(\\boldsymbol{F}\\href{https://example.com}{x}\\)")).toHaveLength(1);
    expect(mathProblems("\\(\\boldsymbol{\\)")).toHaveLength(1);
  });
});
