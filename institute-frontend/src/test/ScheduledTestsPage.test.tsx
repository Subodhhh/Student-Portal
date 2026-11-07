import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "./renderApp";
import { me, page, stubApi } from "./stubApi";

const row = (over: Record<string, unknown> = {}) => ({ plan_id: "pl1", test_type: "DAILY", series_id: null, global_test_id: null, exam_id: "e1",
  exam_version_id: "v1", name: "Physics Daily", state: "PUBLISHED", plan_status: "ACTIVE", exam_status: "PUBLISHED", delivery_status: "SENT", error: null,
  provider_name: "Alpha", home_branch: "Pune", start_at: "2030-01-05T04:30:00Z", end_at: "2030-01-05T07:30:00Z", duration_minutes: 60, subjects: ["Physics"],
  audience: { type: "TARGETED", branches: ["Pune"], batches: [], students: 0 }, assigned_students: 3, question_count: 10, total_marks: "40",
  marking: { mode: "UNIFORM", correct: 4, wrong: 1 }, created_at: "", ...over });
// a published Global Test date whose exam is not generated yet (no plan): listed by its occurrence
const date = row({ plan_id: null, occurrence_id: "o1", test_type: "GLOBAL", global_test_id: "gt1", exam_id: null, exam_version_id: null, name: "National Mock",
  state: "SCHEDULED", plan_status: null, exam_status: null, delivery_status: null, provider_name: "Global Test", home_branch: null, subjects: ["Physics", "Chemistry"],
  audience: { type: "GLOBAL", branches: [], batches: [], students: 0 }, assigned_students: 0, question_count: null, total_marks: null,
  submission_deadline: "2030-01-05T02:30:00Z" });
afterEach(() => vi.unstubAllGlobals());

describe("Scheduled Tests", () => {
  it("lists a published Global Test date before its exam exists, linked to the date and without plan actions", async () => {
    stubApi({ "/api/v1/notifications/unread-count": { unread_count: 0 },
      "/api/v1/auth/me": me("SUPER_ADMIN", ["test.create", "exam.read", "examplan.manage", "exam.generate"]), "/api/v1/tests": page([date, row()]) });
    renderApp("/tests");
    const link = await screen.findByRole("link", { name: "National Mock" });
    expect(link.getAttribute("href")).toBe("/tests/global/occurrences/o1");
    expect(screen.getByText("Published; the exam is generated at the paper deadline")).toBeTruthy();
    expect(screen.getByText(/Papers accepted until/)).toBeTruthy();
    expect(screen.getByText("Physics, Chemistry")).toBeTruthy();
    expect(screen.getByText("Eligible students of every contributing institute")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Physics Daily" }).getAttribute("href")).toBe("/exams/e1");     // plan rows unchanged
    expect(screen.queryByRole("button", { name: "Generate now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Activate" })).toBeNull();
  });
});
