import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MathText } from "./MathText";
import { mathProblems, splitMath } from "./mathRender";

describe("MathText", () => {
  it("typesets stored LaTeX segments and keeps the surrounding text", () => {
    const { container } = render(<MathText text={"Evaluate \\(\\int_{0}^{\\pi} \\sin x\\,dx\\) and \\(\\mathrm{SO_{4}^{2-}}\\)."} />);
    expect(container.querySelectorAll(".katex").length).toBe(2);
    expect(container.textContent).toContain("Evaluate");
    expect(container.textContent).not.toContain("\\int");                             // typeset, not shown as source
  });

  it("leaves prices and plain text alone", () => {
    expect(splitMath("A pen costs $5 and a book $10.")).toEqual([{ kind: "text", value: "A pen costs $5 and a book $10." }]);
    const { container } = render(<MathText text="What is the SI unit of force?" />);
    expect(container.querySelector(".katex")).toBeNull();
  });

  it("shows a field that is exactly one expression (an answer option such as a fraction) at full size, text-embedded maths inline", () => {
    const whole = render(<MathText text={"\\(\\frac{h}{p}\\)"} />).container;
    expect(whole.querySelector(".math-whole")).toBeTruthy();                           // the option is the formula: display style
    expect(whole.querySelector(".katex .mfrac .sizing.reset-size6")).toBeNull();        // numerator not shrunk to script size
    const inline = render(<MathText text={"Here \\(\\frac{h}{p}\\) is the wavelength."} />).container;
    expect(inline.querySelector(".math-whole")).toBeNull();                             // in a sentence it stays inline-sized
    expect(inline.querySelector(".katex .mfrac")).toBeTruthy();
  });

  it("shows a complex fraction or a root of a fraction in a sentence at a readable size, in its own box (no overlap)", () => {
    const { container } = render(<MathText text={"Simplify \\(\\frac{\\frac{a}{b}+\\frac{c}{d}}{x}\\) now and \\(\\sqrt[3]{\\frac{a}{b}}\\) too, but \\(\\frac{1}{2}\\) inline."} />);
    const whole = container.querySelectorAll(".math-whole");
    expect(whole.length).toBe(2);                                                          // the nested ones, not the simple 1/2
    expect((whole[0] as HTMLElement).style.display).toBe("inline-block");
    expect(container.querySelectorAll(".katex").length).toBe(3);
  });

  it("keeps the lines of multi-line working on their own lines", () => {
    const { container } = render(<MathText text={"Solve:\n\\(x + y = 5\\)\n\\(2x - y = 1\\)"} />);
    expect((container.firstChild as HTMLElement).style.whiteSpace).toBe("pre-line");
    expect(container.querySelectorAll(".katex").length).toBe(2);
    expect((render(<MathText text={"One line \\(x\\)"} />).container.firstChild as HTMLElement).style.whiteSpace).toBe("");
  });

  it("shows an expression KaTeX refuses exactly as stored, never drops it", () => {
    const { container } = render(<MathText text={"See \\(\\frac{1}{\\) here"} />);
    expect(container.querySelector(".math-fallback")?.textContent).toBe("\\(\\frac{1}{\\)");
    expect(mathProblems("\\(\\href{http://x}{y}\\)").length).toBe(1);                 // strict, untrusted: links are refused
    expect(mathProblems("\\(x^{2}\\) and $y$")).toEqual([]);
  });
});
