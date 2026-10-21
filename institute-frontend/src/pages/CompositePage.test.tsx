import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { ScopeProvider } from "../auth/ScopeContext";
import CompositePage from "./CompositePage";

const ME = { staff_id: "s1", staff_code: "STF000001", full_name: "Pat Provider", email: "p@x.io", primary_role: "PROVIDER_ADMIN", assignments: [], permissions: ["examplan.manage", "paper.publish"], csrf_token: "c" };
const PROVIDER = { provider_id: "p1", name: "Alpha Institute", status: "ACTIVE", email: "a@x.io" };
const BRANCH = { branch_id: "b1", provider_id: "p1", name: "Main", city: null, status: "ACTIVE" };

function stubApi(extra: Record<string, unknown>) {
  const routes: Record<string, unknown> = {
    "/api/v1/auth/me": ME,
    "/api/v1/providers": { items: [PROVIDER], total: 1, page: 1, page_size: 100 },
    "/api/v1/providers/p1/branches": { items: [BRANCH], total: 1, page: 1, page_size: 100 },
    "/api/v1/subjects": { items: [{ subject_id: "s-phy", name: "Physics" }], total: 1, page: 1, page_size: 100 },
    ...extra,
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = routes[new URL(url).pathname];
    return { ok: body !== undefined, status: body !== undefined ? 200 : 404, json: async () => body ?? { detail: { code: "NOPE", message: "unhandled" } } } as Response;
  }));
}
afterEach(() => vi.unstubAllGlobals());

const mount = () => render(
  <MemoryRouter><AuthProvider><ScopeProvider><CompositePage /></ScopeProvider></AuthProvider></MemoryRouter>,
);

describe("composite tests page", () => {
  it("lists the requests of the selected branch with their real contributors and statuses", async () => {
    stubApi({ "/api/v1/branches/b1/composite-requests": { items: [{ request_id: "r1", name: "Joint NEET Mock", note: null, status: "OPEN", created_at: "2026-10-01T10:00:00Z", initiator_provider_id: "p1",
      slots: [{ slot_id: "sl1", subject_id: "s-phy", provider_id: "p1", status: "PENDING", paper_version_id: null, responded_at: null, decline_reason: null }], missing: [{ slot_id: "sl1" }] }], total: 1, page: 1, page_size: 10 } });
    mount();
    expect(await screen.findByText("Joint NEET Mock")).toBeTruthy();
    expect(await screen.findByText(/Physics · Alpha Institute/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New request" })).toBeNull();                 // retired: history only
    expect(screen.getByText(/Composite Tests are retired/)).toBeTruthy();
  });

  it("shows an honest empty state instead of invented requests", async () => {
    stubApi({ "/api/v1/branches/b1/composite-requests": { items: [], total: 0, page: 1, page_size: 10 } });
    mount();
    expect(await screen.findByText("No composite requests")).toBeTruthy();
  });

  it("shows pending invitations with contribute and decline actions, and nothing to do for answered ones", async () => {
    stubApi({
      "/api/v1/branches/b1/composite-requests": { items: [], total: 0, page: 1, page_size: 10 },
      "/api/v1/composite-invitations": [
        { request: { request_id: "r9", name: "Open one", note: "Need physics", status: "OPEN", created_at: "2026-10-02T10:00:00Z", initiator_provider_name: "Beta Institute" },
          slot: { slot_id: "x1", subject_id: "s-phy", provider_id: "p1", status: "PENDING", paper_version_id: null, responded_at: null, decline_reason: null } },
        { request: { request_id: "r8", name: "Done one", note: null, status: "OPEN", created_at: "2026-10-01T10:00:00Z", initiator_provider_name: "Beta Institute" },
          slot: { slot_id: "x2", subject_id: "s-phy", provider_id: "p1", status: "CONTRIBUTED", paper_version_id: "pv", responded_at: "2026-10-01T11:00:00Z", decline_reason: null } },
      ],
    });
    mount();
    (await screen.findByRole("tab", { name: "Invitations to contribute" })).click();
    expect(await screen.findByText("Open one")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Contribute a paper" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Decline" })).toHaveLength(1);
    expect(screen.getByText("Done one")).toBeTruthy();
  });
});
