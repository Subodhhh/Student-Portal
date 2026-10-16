import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage, MfaPage } from "./AuthPages";

const ME = { staff_id: "s1", staff_code: "STF000001", full_name: "Ada Admin", email: "a@x.io", primary_role: "ADMIN", assignments: [], permissions: [], csrf_token: "csrf-1" };

function stubFetch(handlers: Record<string, { status?: number; body: unknown }>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const h = handlers[`${init?.method ?? "GET"} ${path}`] ?? { status: 404, body: { detail: { code: "NOPE", message: "unhandled" } } };
    return { ok: (h.status ?? 200) < 400, status: h.status ?? 200, json: async () => h.body } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => vi.unstubAllGlobals());

const app = (initial = "/login") => render(
  <MemoryRouter initialEntries={[initial]}>
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/mfa" element={<MfaPage />} />
        <Route path="/" element={<div>dashboard home</div>} />
      </Routes>
    </AuthProvider>
  </MemoryRouter>,
);

describe("sign-in flow", () => {
  it("privileged login goes to the MFA step and completes only after the code is verified", async () => {
    const fn = stubFetch({
      "GET /api/v1/auth/me": { status: 401, body: { detail: { code: "NOT_AUTHENTICATED", message: "x" } } },
      "POST /api/v1/auth/login": { body: { status: "MFA_REQUIRED", challenge_id: "chal-123456789", me: null } },
      "POST /api/v1/auth/mfa/verify": { body: { status: "AUTHENTICATED", challenge_id: null, me: ME } },
    });
    app();
    fireEvent.change(await screen.findByLabelText("Staff ID"), { target: { value: "STF000001" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your verification code")).toBeTruthy();
    expect(screen.queryByText("dashboard home")).toBeNull();                     // no session yet
    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("dashboard home")).toBeTruthy();
    const verifyCall = fn.mock.calls.find((c) => String(c[0]).endsWith("/mfa/verify"))!;
    expect(JSON.parse(String(verifyCall[1]?.body))).toEqual({ challenge_id: "chal-123456789", otp: "123456" });
    expect((verifyCall[1] as RequestInit).credentials).toBe("include");
  });

  it("shows the server's generic message for bad credentials and never navigates", async () => {
    stubFetch({
      "GET /api/v1/auth/me": { status: 401, body: { detail: { code: "NOT_AUTHENTICATED", message: "x" } } },
      "POST /api/v1/auth/login": { status: 401, body: { detail: { code: "INVALID_CREDENTIALS", message: "Invalid Staff ID or password." } } },
    });
    app();
    fireEvent.change(await screen.findByLabelText("Staff ID"), { target: { value: "STF000001" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Invalid Staff ID or password."));
    expect(screen.queryByText("dashboard home")).toBeNull();
  });

  it("sends direct visits to the MFA page back to login", async () => {
    stubFetch({ "GET /api/v1/auth/me": { status: 401, body: { detail: { code: "NOT_AUTHENTICATED", message: "x" } } } });
    app("/mfa");
    expect(await screen.findByText("Institute Portal sign in")).toBeTruthy();
  });
});
