import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as auth from "../auth/AuthContext";
import ProtectedRoute from "./ProtectedRoute";

afterEach(() => vi.restoreAllMocks());

function renderWith(opts: { me: Partial<auth.Me> | null; loading?: boolean; perms?: string[] }, guard: { perm?: string; anyPerm?: string[] } = {}) {
  const perms = opts.perms ?? [];
  vi.spyOn(auth, "useAuth").mockReturnValue({
    me: opts.me as auth.Me | null, loading: !!opts.loading, expired: false, can: (p: string) => perms.includes(p),
  } as unknown as ReturnType<typeof auth.useAuth>);
  render(
    <MemoryRouter initialEntries={["/secret"]}>
      <Routes>
        <Route element={<ProtectedRoute {...guard} />}><Route path="/secret" element={<div>secret page</div>} /></Route>
        <Route path="/login" element={<div>login page</div>} />
        <Route path="/forbidden" element={<div>forbidden page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  it("sends anonymous visitors to login", () => {
    renderWith({ me: null });
    expect(screen.getByText("login page")).toBeTruthy();
  });
  it("shows a spinner while the session is being checked, never the page", () => {
    renderWith({ me: null, loading: true });
    expect(screen.queryByText("secret page")).toBeNull();
    expect(screen.queryByText("login page")).toBeNull();
  });
  it("blocks signed-in users who lack the permission", () => {
    renderWith({ me: { staff_id: "1" } }, { perm: "billing.read" });
    expect(screen.getByText("forbidden page")).toBeTruthy();
  });
  it("lets users through who hold the permission", () => {
    renderWith({ me: { staff_id: "1" }, perms: ["billing.read"] }, { perm: "billing.read" });
    expect(screen.getByText("secret page")).toBeTruthy();
  });
  it("supports any-of permission gates", () => {
    renderWith({ me: { staff_id: "1" }, perms: ["settings.branch"] }, { anyPerm: ["settings.platform", "settings.branch"] });
    expect(screen.getByText("secret page")).toBeTruthy();
  });
});
