import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/renderApp";
import { me, stubApi } from "../test/stubApi";

const PERMS = ["paper.read", "question.read", "paper.create", "paper.approve", "paper.publish"];
const version = (status: string) => ({ paper_version_id: "pv1", paper_id: "pp", version_no: 1, status, question_count: 1, total_marks: "4.00", created_at: "2026-10-01T00:00:00Z" });
const q = { question_version_id: "qv", version_no: 1, stem: "What is g?", option_a: "9.8", option_b: "1", option_c: "2", option_d: "3", correct_option: null,
  marks_positive: "4", marks_negative: "1", explanation: null, topic: null, answers_hidden: true };
const detail = (scope: string, status: string) => ({ paper_id: "pp", scope, subject_id: "phy", title: "Daily paper", latest_version: version(status), questions: [q], history: [] });

function open(who: ReturnType<typeof me>, scope: string, status: string) {
  stubApi({ "/api/v1/auth/me": who, "/api/v1/notifications/unread-count": { unread_count: 0 }, "/api/v1/paper-versions/pv1": detail(scope, status),
    "/api/v1/papers/pp/versions": [version(status)] });
  renderApp("/papers/pv1");
}
const buttons = () => screen.queryAllByRole("button").map((b) => b.textContent);
afterEach(() => vi.unstubAllGlobals());

describe("paper actions follow who may change the paper", () => {
  it.each([["VALIDATED", "Approve"], ["APPROVED", "Publish"], ["PUBLISHED", "Withdraw"]])("hides %s actions on platform papers from provider staff", async (status, action) => {
    for (const role of ["PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"]) {
      open(me(role, PERMS), "GLOBAL", status);
      expect(await screen.findByText(/What is g\?/)).toBeTruthy();
      expect(buttons()).not.toContain(action);
      expect(buttons()).not.toContain("Correct");
      expect(buttons()).not.toContain("Reject draft");
      expect(buttons()).not.toContain("Override status");
      document.body.innerHTML = "";
      vi.unstubAllGlobals();
    }
  });

  it("explains withheld Global Daily Test answers", async () => {
    open(me("TEACHER", PERMS), "GLOBAL", "PUBLISHED");
    expect(await screen.findByText(/hidden until your students' review window opens/)).toBeTruthy();
  });

  it("shows platform paper actions to platform administrators", async () => {
    open(me("SUPER_ADMIN", PERMS), "GLOBAL", "VALIDATED");
    expect(await screen.findByText(/What is g\?/)).toBeTruthy();
    expect(buttons()).toEqual(expect.arrayContaining(["Approve", "Reject draft", "Correct", "Override status"]));
  });

  it("keeps provider paper actions for provider staff with the permissions", async () => {
    open(me("PROVIDER_ADMIN", PERMS), "PROVIDER", "APPROVED");
    expect(await screen.findByText(/What is g\?/)).toBeTruthy();
    expect(buttons()).toEqual(expect.arrayContaining(["Publish", "Withdraw", "Correct"]));
    expect(buttons()).not.toContain("Override status");
  });

  it("hides actions the user has no permission for, even on provider papers", async () => {
    open(me("TEACHER", ["paper.read", "question.read"]), "PROVIDER", "VALIDATED");
    expect(await screen.findByText(/What is g\?/)).toBeTruthy();
    for (const action of ["Approve", "Reject draft", "Correct"]) expect(buttons()).not.toContain(action);
  });
});
