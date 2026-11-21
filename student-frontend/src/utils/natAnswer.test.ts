import { describe, expect, it } from "vitest";
import { natAnswerProblem } from "./natAnswer";

describe("NAT answers (the server's rule, checked again there)", () => {
  it("accepts plain numbers", () => {
    for (const ok of ["0", "-3", "9.8", "0.000001", "123456789012", "2.0"]) expect(natAnswerProblem(ok, false)).toBeNull();
    expect(natAnswerProblem("2.0", true)).toBeNull();
  });
  it("refuses anything else", () => {
    for (const bad of ["1e3", "1,000", "9.8 m", "", "NaN", "Infinity", ".5", "0.1234567", "1234567890123"]) expect(natAnswerProblem(bad, false)).not.toBeNull();
    expect(natAnswerProblem("2.5", true)).toMatch(/whole number/);
  });
});
