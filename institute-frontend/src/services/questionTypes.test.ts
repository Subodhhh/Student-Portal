import { describe, expect, it } from "vitest";
import { describeKey, numberProblem } from "./questions";

// Mirrors of the server rulebook (institute_common/core/question_types.py); the server checks everything again.
describe("NAT numbers", () => {
  it("accepts plain numbers within the limits", () => {
    for (const ok of ["0", "-3", "9.8", "12.50", "0.000001", "123456789012", "2.0"]) expect(numberProblem(ok)).toBeNull();
    expect(numberProblem("2.0", 6, true)).toBeNull();
  });
  it("refuses exponents, separators, units and too many digits", () => {
    for (const bad of ["1e3", "1,000", "9.8 m", "", "NaN", "Infinity", "+5", ".5", "0.1234567", "1234567890123"]) expect(numberProblem(bad)).not.toBeNull();
    expect(numberProblem("2.5", 6, true)).toMatch(/whole number/);
    expect(numberProblem("9.81", 1)).toMatch(/1 decimal places/);
  });
});

describe("keys in words", () => {
  it("names each type's key like the server", () => {
    expect(describeKey("MCQ_SINGLE", { correct_option: "B" })).toBe("B");
    expect(describeKey("MCQ_MULTI", { answer_key: { correct: ["A", "C"] } })).toBe("A, C");
    expect(describeKey("NUMERICAL", { answer_key: { value: "9.8", tolerance: { kind: "ABS", amount: "0.05" } } })).toBe("9.8 ± 0.05");
    expect(describeKey("NUMERICAL", { answer_key: { value: "14.7", tolerance: { kind: "RANGE", min: "14.5", max: "15" } } }))
      .toBe("14.7 (accepted 14.5 to 15)");
  });
});
