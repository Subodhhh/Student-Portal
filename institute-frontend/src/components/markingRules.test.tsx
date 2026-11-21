import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarkingRulesEditor } from "./markingRules";
import {
  describeRules, emptyRules, fromHundredths, rulesPayload, rulesProblem, rulesTotal, snapshotSummary, subjectTotal, toHundredths, type RuleRow,
} from "./markingRulesModel";

const PHY = "phy-id";
const CHEM = "chem-id";

describe("marking rules helpers", () => {
  it("reads decimal marks exactly in hundredths", () => {
    expect(toHundredths("1.25")).toBe(125);
    expect(toHundredths("0.1")).toBe(10);
    expect(toHundredths("4")).toBe(400);
    for (const bad of ["", "1.255", "-1", "1e2", "abc", "1,5", "1000"]) expect(toHundredths(bad)).toBeNull();
    expect(fromHundredths(125)).toBe("1.25");
    expect(fromHundredths(150)).toBe("1.5");
    expect(fromHundredths(400)).toBe("4");
  });

  it("totals never suffer floating-point rounding", () => {
    const rows: RuleRow[] = [{ subject_id: null, correct: "0.1", wrong: "0" }, { subject_id: CHEM, correct: "2.5", wrong: "0.5" }];
    expect(rulesTotal(rows, [{ subject_id: PHY, questions: 3 }])).toBe("0.3");        // 0.1 * 3 in floats is 0.30000000000000004
    expect(rulesTotal(rows, [{ subject_id: PHY, questions: 3 }, { subject_id: CHEM, questions: 2 }])).toBe("5.3");
    expect(subjectTotal(rows, CHEM, 2)).toBe("5");
  });

  it("requires marks for every subject and valid numbers, with no defaults", () => {
    expect(rulesProblem(emptyRules(), [PHY])).toMatch(/Enter the marks/);
    expect(rulesProblem([{ subject_id: null, correct: "0", wrong: "0" }], [PHY])).toMatch(/more than 0/);
    expect(rulesProblem([{ subject_id: null, correct: "1.255", wrong: "0" }], [PHY])).toMatch(/2 decimal places/);
    expect(rulesProblem([{ subject_id: null, correct: "", wrong: "" }, { subject_id: PHY, correct: "4", wrong: "1" }], [PHY, CHEM]))
      .toMatch(/Every subject/);
    expect(rulesProblem([{ subject_id: null, correct: "1.25", wrong: "0.25" }], [PHY, CHEM])).toBeNull();
  });

  it("sends the marks exactly as typed and skips an unused general row", () => {
    expect(rulesPayload([{ subject_id: null, correct: "", wrong: "" }, { subject_id: PHY, correct: " 1.50 ", wrong: "0.25" }])).toEqual({
      rules: [{ subject_id: PHY, question_type: "MCQ_SINGLE", correct: "1.50", wrong: "0.25" }],
    });
    expect(describeRules([{ subject_id: null, correct: "4", wrong: "" }], () => "x")).toEqual(["All subjects: +4 correct, −0 wrong"]);
    // summaries of a frozen exam's marking: RULES lists the rules, older modes read as before
    expect(snapshotSummary({ mode: "RULES", rules: [{ subject_id: null, question_type: "MCQ_SINGLE", correct: "1.25", wrong: "0.25" }] }))
      .toBe("All subjects: +1.25 correct, −0.25 wrong; 0 unanswered");
    expect(snapshotSummary({ mode: "UNIFORM", correct: 4, wrong: 1 })).toBe("+4 correct, −1 wrong, 0 unanswered");
    expect(snapshotSummary(undefined)).toBe("Each question's own marks, 0 unanswered");
  });
});

describe("MarkingRulesEditor", () => {
  it("starts empty, edits a rule and adds a subject rule", () => {
    const onChange = vi.fn();
    render(<MarkingRulesEditor rows={emptyRules()} onChange={onChange}
      subjects={[{ subject_id: PHY, name: "Physics" }, { subject_id: CHEM, name: "Chemistry" }]} />);
    const correct = screen.getByLabelText("All subjects: marks for a correct answer") as HTMLInputElement;
    expect(correct.value).toBe("");
    fireEvent.change(correct, { target: { value: "1.25" } });
    expect(onChange).toHaveBeenLastCalledWith([{ subject_id: null, correct: "1.25", wrong: "" }]);
    fireEvent.change(screen.getByLabelText("Different marks for one subject (optional)"), { target: { value: CHEM } });
    expect(onChange).toHaveBeenLastCalledWith([{ subject_id: null, correct: "", wrong: "" }, { subject_id: CHEM, correct: "", wrong: "" }]);
  });
});
