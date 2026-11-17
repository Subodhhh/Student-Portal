import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubApi } from "../test/stubApi";
import { RecognitionPreview } from "./RecognitionPreview";

afterEach(() => vi.unstubAllGlobals());
const URL_ = "/api/v1/paper-versions/v1/recognition-preview";

describe("Live recognition preview of a correction", () => {
  it("typesets what the server would save, and updates as the text changes", async () => {
    const calls = stubApi({ [`POST ${URL_}`]: (_u: URL, init: RequestInit) => {
      const { text } = JSON.parse(String(init.body)) as { text: string };
      return { original: text, text: text === "x^2" ? "\\(x^{2}\\)" : "\\(\\frac{x}{\\ln x}\\)", outcome: "CONVERTED", reasons: [], spans: [], suggestions: [] };
    } });
    const { rerender, container } = render(<RecognitionPreview target={{ versionId: "v1" }} text="x^2" field="option_a" delay={0} />);
    expect(await screen.findByText("Formatted")).toBeTruthy();
    expect(container.querySelector(".katex .msupsub")).toBeTruthy();                     // typeset by MathText, as on the Review screen
    rerender(<RecognitionPreview target={{ versionId: "v1" }} text="x/ln x" field="option_a" delay={0} />);
    await screen.findByText("Formatted");
    await vi.waitFor(() => expect(container.querySelector(".katex .mfrac")).toBeTruthy());
    expect(calls.map((c) => c.body)).toEqual([{ text: "x^2", field: "option_a" }, { text: "x/ln x", field: "option_a" }]);
  });

  it("explains a doubt and offers each reading typeset; choosing one fills the text", async () => {
    stubApi({ [`POST ${URL_}`]: { original: "ex / x + 4", text: "ex / x + 4", outcome: "NEEDS_REVIEW", reasons: ["FLATTENED_POWER"],
      spans: [{ original: "ex / x + 4", status: "flagged", code: "FLATTENED_POWER", reason: "'e' written against a letter is a power or a product." }],
      suggestions: [{ expression: "ex / x + 4", label: "e to the power x", text: "\\(\\frac{e^{x}}{x} + 4\\)" },
        { expression: "ex / x + 4", label: "e times x", text: "\\(\\frac{e \\cdot x}{x} + 4\\)" }] } });
    const pick = vi.fn();
    render(<RecognitionPreview target={{ versionId: "v1" }} text="ex / x + 4" field="question" onPick={pick} delay={0} />);
    expect(await screen.findByText("Needs review")).toBeTruthy();
    expect(screen.getByText(/is a power or a product/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use the reading e to the power x" }));
    expect(pick).toHaveBeenCalledWith("\\(\\frac{e^{x}}{x} + 4\\)");
  });

  it("shows nothing for empty text and reports a refused request", async () => {
    const calls = stubApi({});
    const { container, rerender } = render(<RecognitionPreview target={{ versionId: "v1" }} text="  " field="question" delay={0} />);
    expect(container.textContent).toBe("");
    rerender(<RecognitionPreview target={{ versionId: "v1" }} text="x" field="question" delay={0} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(calls).toHaveLength(1);
  });
});
