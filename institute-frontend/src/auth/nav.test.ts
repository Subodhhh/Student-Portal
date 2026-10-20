import { describe, expect, it } from "vitest";
import { visibleNav } from "./nav";

const labels = (permissions: string[], role?: Parameters<typeof visibleNav>[1]) =>
  visibleNav(permissions, role).map((item) => item.label);

const ALL = [
  "provider.read", "staff.read", "enrollment.manage", "subject.manage", "batch.manage", "teaching.assign", "paper.read",
  "examplan.manage", "exam.read", "audit.read", "settings.platform", "billing.read",
];

describe("navigation", () => {
  it("gives the Super Admin exactly the platform menu, in order", () => {
    expect(labels(ALL, "SUPER_ADMIN")).toEqual([
      "Dashboard",
      "Providers & Branches",
      "Role Management",
      "Students & Enrollments",
      "Subjects",
      "Question Bank & Papers",
      "Global Tests",
      "Scheduled Tests",
      "Exam Plans",
      "Exams & Results",
      "Audit Log",
      "Platform Settings",
      "Support",
      "Billing",
      "My Account",
    ]);
  });

  it("never shows Service Health, Batches or a separate Notifications item to the Super Admin", () => {
    const items = visibleNav(ALL, "SUPER_ADMIN");
    expect(items.map((i) => i.to)).not.toEqual(expect.arrayContaining(["/system-health"]));
    expect(items.map((i) => i.to)).not.toContain("/batches");
    expect(items.map((i) => i.to)).not.toContain("/notifications");
    expect(items.map((i) => i.to)).not.toContain("/academics");
  });

  it("filters Super Admin items using granted permissions", () => {
    expect(labels(["provider.read"], "SUPER_ADMIN")).toEqual(["Dashboard", "Providers & Branches", "Support", "My Account"]);
  });

  it("keeps the permission-based menu for other roles, with Subjects and Batches as separate pages", () => {
    expect(labels([])).toEqual(["Dashboard", "Notifications", "Support", "My Account"]);
    const branchAdmin = labels(["enrollment.manage", "subject.manage", "batch.manage", "teaching.assign", "settings.branch"], "BRANCH_ADMIN");
    expect(branchAdmin).toContain("Subjects");
    expect(branchAdmin).toContain("Batches");
    expect(branchAdmin).toContain("Settings");
    expect(labels(["batch.manage"], "BRANCH_ADMIN")).not.toContain("Subjects");
    expect(labels(["teaching.assign"], "BRANCH_ADMIN")).toContain("Subjects");
  });

  it("shows teacher pages without unrelated admin pages", () => {
    const result = labels(["paper.read", "exam.read", "question.read", "provider.read"], "TEACHER");
    expect(result).toContain("Question Bank & Papers");
    expect(result).toContain("Exams & Results");
    expect(result).not.toContain("Role Management");
    expect(result).not.toContain("Billing");
    expect(result).not.toContain("Audit Log");
  });

  it("lists the three test types and no longer offers Composite Tests", () => {
    const testItems = (perms: string[], role: Parameters<typeof visibleNav>[1]) => visibleNav(perms, role).filter((i) => i.section === "Test").map((i) => i.label);
    expect(testItems(["test.create", "examplan.manage", "paper.publish", "exam.read"], "PROVIDER_ADMIN")).toEqual(["Daily Test", "Weekly Test", "Global Tests", "Scheduled Tests"]);
    expect(testItems(["test.create", "paper.publish", "exam.read"], "BRANCH_ADMIN")).toEqual(["Daily Test", "Weekly Test", "Global Tests", "Scheduled Tests"]);
    expect(testItems(["paper.read", "paper.publish", "exam.read"], "TEACHER")).toEqual(["Global Tests", "Scheduled Tests"]);   // no test creation
    for (const role of ["SUPER_ADMIN", "PROVIDER_ADMIN", "BRANCH_ADMIN", "TEACHER"] as const) {
      expect(labels(["examplan.manage", "test.create", "paper.publish", "exam.read"], role)).not.toContain("Composite Tests");
    }
    expect(labels(["settings.provider"], "PROVIDER_ADMIN")).not.toContain("Service Health");
  });

  it("names Settings after its scope", () => {
    expect(labels(["settings.branch"])).toContain("Settings");
    expect(labels(["settings.platform"], "SUPER_ADMIN")).toContain("Platform Settings");
  });
});
