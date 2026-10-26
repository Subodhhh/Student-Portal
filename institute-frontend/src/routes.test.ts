import { describe, expect, it } from "vitest";
import { pageTitle } from "./routes";

describe("route titles", () => {
  it.each([
    ["/", "Dashboard"],
    ["/students", "Students & Enrollments"],
    ["/staff", "Role Management"],
    ["/staff/teachers", "Role Management"],
    ["/providers", "Providers & Branches"],
    ["/providers/p1", "Providers & Branches"],
    ["/providers/p1/branches/b1", "Providers & Branches"],
    ["/subjects", "Subjects"],
    ["/batches", "Batches"],
    ["/papers", "Question Bank & Papers"],
    ["/papers/v1", "Question Bank & Papers"],
    ["/exam-plans", "Scheduled Tests"],
    ["/composite", "Composite Tests"],
    ["/exams", "Exams"],
    ["/exams/e1", "Exams"],
    ["/results", "Results"],
    ["/results/e1", "Results"],
    ["/audit", "Audit Log"],
    ["/support", "Support"],
    ["/billing", "Billing"],
    ["/notifications", "Notifications"],
    ["/account", "My Account"],
    ["/system-health", "Service Health"],
  ])("%s -> %s", (path, title) => {
    expect(pageTitle(path, "PROVIDER_ADMIN")).toBe(title);
  });

  it("names the settings page after the viewer's scope", () => {
    expect(pageTitle("/settings", "SUPER_ADMIN")).toBe("Platform Settings");
    expect(pageTitle("/settings", "BRANCH_ADMIN")).toBe("Settings");
  });

  it("does not match deeper unknown paths or trailing segments", () => {
    expect(pageTitle("/providers/p1/unknown/x")).toBe("Institute Portal");
    expect(pageTitle("/nope")).toBe("Institute Portal");
  });
});
