import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, stubApi } from "../test/stubApi";
import { StudentCount } from "./StudentCount";

const rows = (from: number, n: number) => Array.from({ length: n }, (_, i) => ({ student_id: `STU${String(from + i).padStart(6, "0")}`, name: `Student ${from + i}`, status: "ASSIGNED" }));
afterEach(() => vi.unstubAllGlobals());

describe("Student counts", () => {
  it("opens the students behind the count, paged from the server, and closes with Escape", async () => {
    const calls = stubApi({ "/api/v1/exams/e1/assignments": (u: URL) => ({ items: u.searchParams.get("page") === "2" ? rows(26, 5) : rows(1, 25), total: 30 }) });
    render(<StudentCount count={30} url="/api/v1/exams/e1/assignments" title="Students assigned to Daily Physics" context="The students this exam is assigned to." />);
    expect(calls).toHaveLength(0);                                                      // nothing is fetched until it is opened
    fireEvent.click(screen.getByRole("button", { name: /30 students: Students assigned to Daily Physics/ }));
    const dialog = await screen.findByRole("dialog", { name: "Students assigned to Daily Physics" });
    expect(within(dialog).getByText("The students this exam is assigned to.")).toBeTruthy();
    expect(await within(dialog).findByText("30 students")).toBeTruthy();               // the list's total is the count
    expect(within(dialog).getByText("STU000001")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /next/i }));
    expect(await within(dialog).findByText("STU000030")).toBeTruthy();
    expect(calls.map((c) => c.search.get("page"))).toEqual(["1", "2"]);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows errors from the server (e.g. outside the caller's scope) and leaves a zero count plain", async () => {
    stubApi({ "/api/v1/exams/e2/assignments": fail(403, { code: "FORBIDDEN", message: "You do not have permission for this action." }) });
    render(<><StudentCount count={0} url="/api/v1/exams/e0/assignments" title="None" context="-" />
      <StudentCount count={2} url="/api/v1/exams/e2/assignments" title="Assigned" context="-" /></>);
    expect(screen.getAllByRole("button")).toHaveLength(1);                               // 0 opens nothing
    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByText(/do not have permission/)).toBeTruthy();
  });
});
