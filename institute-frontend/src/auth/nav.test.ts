
import { describe, expect, it } from "vitest";
import { visibleNav } from "./nav";

const labels = (permissions: string[], role?: Parameters<typeof visibleNav>[1]) =>
  visibleNav(permissions, role).map((item) => item.label);

describe("navigation", () => {
  it("shows the configured platform-wide menu to Super Admin", () => {
    expect(
      labels(
        [
          "provider.read",
          "staff.read",
          "enrollment.manage",
          "subject.manage",
          "paper.read",
          "examplan.manage",
          "audit.read",
          "settings.platform",
          "billing.read",
        ],
        "SUPER_ADMIN",
      ),
    ).toEqual([
      "Dashboard",
      "Providers & Branches",
      "Role Management",
      "Students & Enrollments",
      "Subjects",
      "Question Bank & Papers",
      "Exam Plans",
      "Composite Tests",
      "Service Health",
      "Notifications",
      "Audit Log",
      "Platform Settings",
      "Support",
      "Billing",
    ]);
  });

  it("filters Super Admin items using granted permissions", () => {
    expect(labels(["provider.read"], "SUPER_ADMIN")).toEqual([
      "Dashboard",
      "Providers & Branches",
      "Notifications",
      "Support",
    ]);
  });

  it("preserves the existing navigation for other roles", () => {
    expect(labels([])).toEqual([
      "Dashboard",
      "Notifications",
      "Support",
    ]);
  });

  it("shows teacher pages without unrelated admin pages", () => {
    const result = labels([
      "paper.read",
      "exam.read",
      "question.read",
      "provider.read",
    ]);

    expect(result).toContain("Question bank & papers");
    expect(result).toContain("Exams & results");
    expect(result).not.toContain("Staff");
    expect(result).not.toContain("Billing");
    expect(result).not.toContain("Audit log");
  });

  it("offers composite tests only to the people who decide for a provider, and service health only to the platform owner", () => {
    expect(labels(["examplan.manage"], "PROVIDER_ADMIN")).toContain("Composite tests");
    expect(labels(["paper.read", "paper.publish", "exam.read"], "TEACHER")).not.toContain("Composite tests");
    expect(labels(["exam.schedule", "exam.read"], "BRANCH_ADMIN")).not.toContain("Composite tests");
    expect(labels(["settings.platform"], "SUPER_ADMIN")).toContain("Service Health");
    expect(labels(["settings.provider"], "PROVIDER_ADMIN")).not.toContain("Service Health");
  });

  it("shows Settings when any existing settings permission is granted", () => {
    expect(labels(["settings.branch"])).toContain("Settings");
    expect(labels(["settings.platform"])).toContain("Settings");
  });
});
