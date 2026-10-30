import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import NotificationsPage from "./NotificationsPage";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

describe("Notifications", () => {
  it("opens the details page for a test that has started, so a Global Test's papers are reviewed before starting", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(200, [{
      notification_id: "n1", event_type: "TEST_STARTED", title: "NEET UG - 05 has started", message: "Open it to begin.",
      created_at: new Date().toISOString(), metadata: { test_id: "t1" },
      recipient: { is_read: true, notification_recipient_id: "r1" },
    }])));
    render(<MemoryRouter initialEntries={["/notifications"]}><Routes>
      <Route path="/notifications" element={<NotificationsPage />} />
      <Route path="/tests/:testId" element={<p>details page</p>} />
      <Route path="/tests/:testId/attempt" element={<p>attempt page</p>} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByText("NEET UG - 05 has started"));
    expect(await screen.findByText("details page")).toBeTruthy();
    expect(screen.queryByText("attempt page")).toBeNull();
  });
});
