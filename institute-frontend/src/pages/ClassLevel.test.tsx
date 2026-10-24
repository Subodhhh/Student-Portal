import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClassTargetSelect } from "../components/classLevels";
import { renderApp } from "../test/renderApp";
import { me, page, stubApi } from "../test/stubApi";

const PHY = { subject_id: "phy", name: "Physics" };
const A = { batch_id: "A", name: "NEET 11", status: "ACTIVE", default_class_level: "11TH", default_academic_year: "2026-2027", default_other_description: null };
const B = { batch_id: "B", name: "NEET 12", status: "ACTIVE", default_class_level: "12TH", default_academic_year: "2026-2027", default_other_description: null };
const C = { batch_id: "C", name: "Open batch", status: "ACTIVE", default_class_level: null, default_academic_year: null, default_other_description: null };
const OLD = { enrollment_id: "e1", student_id: "STU000001", student_name: "Riya Patil", status: "ACTIVE", batch_ids: ["A"], batch_subjects: { A: ["phy"] },
  enrolled_at: "2026-10-01", academic: null };
const SET = { ...OLD, enrollment_id: "e2", student_id: "STU000002", student_name: "Aman Shah", batch_subjects: {},
  academic: { class_level: "12TH", class_label: "12th", academic_year: "2026-2027", other_class_description: null } };
afterEach(() => vi.unstubAllGlobals());

const stub = (perms = ["enrollment.manage", "batch.manage"], extra: Record<string, unknown> = {}) => stubApi({
  "/api/v1/auth/me": me("PROVIDER_ADMIN", perms), "/api/v1/notifications/unread-count": { unread_count: 0 },
  "/api/v1/providers": page([{ provider_id: "p1", name: "Alpha", status: "ACTIVE" }]),
  "/api/v1/providers/p1/branches": page([{ branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" }]),
  "/api/v1/branches/b1/enrollments": page([OLD, SET]), "/api/v1/branches/b1/batches": page([A, B, C]),
  "/api/v1/providers/p1/subjects": [PHY], "/api/v1/batches/A/subjects": [PHY], "/api/v1/batches/B/subjects": [PHY], "/api/v1/batches/C/subjects": [PHY],
  "POST /api/v1/branches/b1/enrollments": { ...SET, enrollment_id: "e9" },
  "PATCH /api/v1/enrollments/e1/academic-profile": { class_level: "OTHER", class_label: "Other", academic_year: "2027-2028", other_class_description: "NIOS bridge" },
  "POST /api/v1/branches/b1/batches": { ...C, batch_id: "N" }, "PATCH /api/v1/batches/A": A,
  ...extra,
});

async function openEnroll() {
  renderApp("/students?tab=branch");
  fireEvent.click(await screen.findByRole("button", { name: "Enroll student" }));
  fireEvent.click(await screen.findByRole("button", { name: "New student" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("First name"), { target: { value: "Zoya" } });
  fireEvent.change(within(dialog).getByLabelText("Last name"), { target: { value: "Khan" } });
  fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "z@example.com" } });
  return dialog;
}
const cls = (d: HTMLElement) => within(d).getByLabelText(/Class \/ level/) as HTMLSelectElement;
const year = (d: HTMLElement) => within(d).getByLabelText(/Academic year/) as HTMLInputElement;
const posts = (calls: { method: string; path: string; body: unknown }[], path: string) => calls.filter((c) => c.method === "POST" && c.path === path);

describe("Class and academic year on enrollment", () => {
  it("shows each enrollment's class in the roster and flags enrollments without one", async () => {
    stub();
    renderApp("/students?tab=branch");
    const old = (await screen.findByText("Riya Patil")).closest("tr")!;
    expect(within(old).getByText("Not set")).toBeTruthy();
    expect(within(screen.getByText("Aman Shah").closest("tr")!).getByText("12th · 2026-2027")).toBeTruthy();
  });

  it("requires a class and a valid academic year before enrolling, and a description for Other", async () => {
    const calls = stub();
    const d = await openEnroll();
    fireEvent.click(within(d).getByLabelText("Open batch"));                 // a batch without defaults: nothing prefilled
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    expect(await within(d).findByText("Choose the class.")).toBeTruthy();
    expect(within(d).getByText("Enter the academic year.")).toBeTruthy();
    fireEvent.change(cls(d), { target: { value: "OTHER" } });
    fireEvent.change(year(d), { target: { value: "2026-2028" } });
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    expect(await within(d).findByText("Describe the class.")).toBeTruthy();
    expect(within(d).getByText(/two consecutive years/)).toBeTruthy();
    expect(posts(calls, "/api/v1/branches/b1/enrollments")).toHaveLength(0);  // nothing sent while invalid
    fireEvent.change(within(d).getByLabelText(/Describe the class/), { target: { value: "NIOS bridge" } });
    fireEvent.change(year(d), { target: { value: "2026-2027" } });
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    await waitFor(() => expect(posts(calls, "/api/v1/branches/b1/enrollments")).toHaveLength(1));
    expect((posts(calls, "/api/v1/branches/b1/enrollments")[0].body as Record<string, unknown>).academic)
      .toEqual({ class_level: "OTHER", academic_year: "2026-2027", other_class_description: "NIOS bridge" });
  });

  it("prefills from the batch defaults and lets staff override them", async () => {
    const calls = stub();
    const d = await openEnroll();
    fireEvent.click(within(d).getByLabelText("NEET 11"));
    await waitFor(() => expect(cls(d).value).toBe("11TH"));
    expect(year(d).value).toBe("2026-2027");
    expect(within(d).getByText(/Prefilled from the batch defaults/)).toBeTruthy();
    fireEvent.change(cls(d), { target: { value: "DROPPER" } });              // this student differs
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    await waitFor(() => expect(posts(calls, "/api/v1/branches/b1/enrollments")).toHaveLength(1));
    expect((posts(calls, "/api/v1/branches/b1/enrollments")[0].body as Record<string, unknown>).academic)
      .toEqual({ class_level: "DROPPER", academic_year: "2026-2027", other_class_description: null });
  });

  it("never picks one of conflicting batch defaults: staff must choose", async () => {
    const calls = stub();
    const d = await openEnroll();
    fireEvent.click(within(d).getByLabelText("NEET 11"));
    fireEvent.click(within(d).getByLabelText("NEET 12"));
    expect(await within(d).findByText(/suggest different classes/)).toBeTruthy();
    expect(cls(d).value).toBe("");
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    expect(await within(d).findByText("Choose the class.")).toBeTruthy();
    expect(posts(calls, "/api/v1/branches/b1/enrollments")).toHaveLength(0);
    fireEvent.change(cls(d), { target: { value: "12TH" } });
    fireEvent.change(year(d), { target: { value: "2026-2027" } });
    fireEvent.click(within(d).getByRole("button", { name: "Enroll" }));
    await waitFor(() => expect(posts(calls, "/api/v1/branches/b1/enrollments")).toHaveLength(1));
    const body = posts(calls, "/api/v1/branches/b1/enrollments")[0].body as Record<string, unknown>;
    expect(body.batch_ids).toEqual(["A", "B"]);
    expect((body.academic as Record<string, unknown>).class_level).toBe("12TH");
  });

  it("edits the class in Manage without touching batches or subject selections, and warns while it is missing", async () => {
    const calls = stub();
    renderApp("/students?tab=branch");
    fireEvent.click(within((await screen.findByText("Riya Patil")).closest("tr")!).getByRole("button", { name: "Manage" }));
    const d = await screen.findByRole("dialog");
    expect(within(d).getByText(/No class \/ academic year yet/)).toBeTruthy();
    fireEvent.change(cls(d), { target: { value: "OTHER" } });
    fireEvent.change(year(d), { target: { value: "2027-2028" } });
    fireEvent.click(within(d).getByRole("button", { name: "Save class" }));
    expect(await within(d).findByText("Describe the class.")).toBeTruthy();
    fireEvent.change(within(d).getByLabelText(/Describe the class/), { target: { value: "NIOS bridge" } });
    fireEvent.click(within(d).getByRole("button", { name: "Save class" }));
    expect(await within(d).findByText("Class and academic year saved.")).toBeTruthy();
    const patch = calls.filter((c) => c.method === "PATCH");
    expect(patch.map((c) => [c.path, c.body])).toEqual([["/api/v1/enrollments/e1/academic-profile",
      { class_level: "OTHER", academic_year: "2027-2028", other_class_description: "NIOS bridge" }]]);
    expect(calls.some((c) => c.method === "PUT" || c.method === "DELETE")).toBe(false);   // batches and subject selections untouched
    expect(within(d).getByText(/Other \(NIOS bridge\) · 2027-2028/)).toBeTruthy();
  });
});

describe("Batch class defaults", () => {
  it("creates a batch with optional defaults and edits them later", async () => {
    const calls = stub();
    renderApp("/batches");
    fireEvent.click(await screen.findByRole("button", { name: "New batch" }));
    let d = screen.getByRole("dialog");
    fireEvent.change(within(d).getByLabelText("Name"), { target: { value: "JEE 2028" } });
    fireEvent.change(within(d).getByLabelText(/Default class/), { target: { value: "12TH" } });
    fireEvent.change(within(d).getByLabelText(/Default academic year/), { target: { value: "2027" } });
    fireEvent.click(within(d).getByRole("button", { name: "Create" }));
    expect(await within(d).findByText(/Use the form 2026-2027/)).toBeTruthy();
    expect(posts(calls, "/api/v1/branches/b1/batches")).toHaveLength(0);
    fireEvent.change(within(d).getByLabelText(/Default academic year/), { target: { value: "2027-2028" } });
    fireEvent.click(within(d).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(posts(calls, "/api/v1/branches/b1/batches")).toHaveLength(1));
    expect(posts(calls, "/api/v1/branches/b1/batches")[0].body).toEqual({ name: "JEE 2028", start_date: null, end_date: null,
      default_class_level: "12TH", default_academic_year: "2027-2028", default_other_description: null });
    expect(within(screen.getByText("NEET 11").closest("tr")!).getByText("11th · 2026-2027")).toBeTruthy();
    fireEvent.click(within(screen.getByText("NEET 11").closest("tr")!).getByRole("button", { name: "Class defaults" }));
    d = await screen.findByRole("dialog");
    expect((within(d).getByLabelText(/Default class/) as HTMLSelectElement).value).toBe("11TH");
    fireEvent.change(within(d).getByLabelText(/Default class/), { target: { value: "" } });   // remove the class default, keep the year
    fireEvent.click(within(d).getByRole("button", { name: "Save defaults" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ default_class_level: null, default_academic_year: "2026-2027", default_other_description: null });
  });

  it("keeps batch defaults away from staff who cannot manage batches (the Batches page itself is denied)", async () => {
    stub(["enrollment.manage"]);
    renderApp("/batches");
    expect(await screen.findByText(/Access denied/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Class defaults" })).toBeNull();
    expect(screen.queryByText("NEET 11")).toBeNull();
  });
});

describe("A test's class", () => {
  it("offers only controlled classes (never Other) and shows a stored legacy label as not recognised", () => {
    const onChange = vi.fn();
    render(<ClassTargetSelect value="Class 12" onChange={onChange} />);
    const select = screen.getByLabelText(/Class \/ level/) as HTMLSelectElement;
    const labels = [...select.options].map((o) => o.textContent);
    expect(labels).toEqual(["Any class", "Class 12 (not recognised)", "6th", "7th", "8th", "9th", "10th", "11th", "12th", "Dropper"]);
    expect(screen.getByText(/Not a recognised class/)).toBeTruthy();
    fireEvent.change(select, { target: { value: "12th" } });
    expect(onChange).toHaveBeenCalledWith("12th");
  });
});
