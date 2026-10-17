import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_API_URL } from "../services/api";
import ProtectedRoute from "./ProtectedRoute";
import Header from "./Header";

const LOGOUT_URL = `${AUTH_API_URL}/auth/logout`;
const ME_URL = `${AUTH_API_URL}/auth/me`;

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A fake backend with one student session; a successful logout ends it, exactly like the auth service. */
function fakeBackend(logout: "ok" | "server-error" | "network-error" = "ok") {
  let sessionValid = true;
  const fetchMock = vi.fn(async (url: string) => {
    if (url === LOGOUT_URL) {
      if (logout === "network-error") throw new TypeError("Failed to fetch");
      if (logout === "server-error") return json(500, { detail: "Internal Server Error" });
      sessionValid = false;
      return json(200, { message: "Logout successful" });
    }
    if (url === ME_URL) return sessionValid ? json(200, { student_id: "S1" }) : json(401, { detail: "Not authenticated" });
    if (url.endsWith("/profile")) return json(200, { first_name: "Asha", last_name: "Rao" });
    if (url.endsWith("/notifications/unread-count")) return json(200, { unread_count: 0 });
    return json(404, { detail: "Not found." });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Same guard nesting as AppRoutes: the student was on /dashboard, then opened /profile. */
function renderPortal() {
  const router = createMemoryRouter(
    [
      { path: "/login", element: <div>login page</div> },
      {
        element: <ProtectedRoute />,
        children: [
          { path: "/dashboard", element: <><Header /><div>dashboard page</div></> },
          { path: "/profile", element: <><Header /><div>profile page</div></> },
        ],
      },
    ],
    { initialEntries: ["/dashboard", "/profile"], initialIndex: 1 },
  );
  render(<RouterProvider router={router} />);
  return router;
}

async function clickLogout() {
  fireEvent.click(await screen.findByLabelText("Student menu"));
  fireEvent.click(screen.getByRole("button", { name: "Logout" }));
}

const logoutCalls = (fetchMock: ReturnType<typeof fakeBackend>) => fetchMock.mock.calls.filter(([url]) => url === LOGOUT_URL);
const meCalls = (fetchMock: ReturnType<typeof fakeBackend>) => fetchMock.mock.calls.filter(([url]) => url === ME_URL);

beforeEach(() => {
  sessionStorage.setItem("student_name", "Asha Rao");
  localStorage.setItem("student_test_attempts", JSON.stringify([{ testId: "T1", startedAt: 1, expiresAt: 2, status: "IN_PROGRESS" }]));
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
});

describe("Header logout", () => {
  it("ends the session on the server, clears stored student data and replaces the history entry", async () => {
    const fetchMock = fakeBackend("ok");
    const router = renderPortal();
    await screen.findByText("profile page");

    await clickLogout();
    await screen.findByText("login page");

    expect(logoutCalls(fetchMock)).toEqual([[LOGOUT_URL, { method: "POST", credentials: "include" }]]);
    expect(sessionStorage.getItem("student_name")).toBeNull();
    expect(localStorage.getItem("student_test_attempts")).toBeNull();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("going Back after logout re-checks the session and never shows protected content", async () => {
    const fetchMock = fakeBackend("ok");
    const router = renderPortal();
    await screen.findByText("profile page");
    await clickLogout();
    await screen.findByText("login page");
    const checksBeforeBack = meCalls(fetchMock).length;

    await act(() => router.navigate(-1));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(meCalls(fetchMock).length).toBeGreaterThan(checksBeforeBack);
    expect(screen.queryByText("dashboard page")).toBeNull();
    expect(screen.queryByText("profile page")).toBeNull();
    expect(screen.getByText("login page")).toBeTruthy();
  });

  it.each(["server-error", "network-error"] as const)(
    "on a %s it says the student is still signed in and keeps them on the page",
    async (failure) => {
      const fetchMock = fakeBackend(failure);
      const router = renderPortal();
      await screen.findByText("profile page");

      await clickLogout();

      expect((await screen.findByRole("alert")).textContent).toMatch(/still signed in/i);
      expect(logoutCalls(fetchMock)).toHaveLength(1);
      expect(router.state.location.pathname).toBe("/profile");
      expect(screen.getByText("profile page")).toBeTruthy();
      expect(sessionStorage.getItem("student_name")).toBe("Asha Rao");
      expect(localStorage.getItem("student_test_attempts")).not.toBeNull();
      expect((screen.getByRole("button", { name: "Logout" }) as HTMLButtonElement).disabled).toBe(false);
    },
  );
});
