import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ImportTemplateDownloads } from "./importTemplates";

const saved: string[] = [];
const fetched: string[] = [];

beforeEach(() => {
  saved.length = 0;
  fetched.length = 0;
  URL.createObjectURL = vi.fn(() => "blob:template");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
});
afterEach(() => vi.restoreAllMocks());

function serve(status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    fetched.push(new URL(input).pathname);
    expect(init?.credentials).toBe("include");                       // the session cookie authorises the download
    if (status !== 200) return { ok: false, status, json: async () => ({ detail: { code: "FORBIDDEN", message: "Not allowed." } }) } as Response;
    const name = new URL(input).pathname.endsWith("xlsx") ? "question-import-template.xlsx" : "question-import-template.docx";
    return { ok: true, status, headers: new Headers({ "Content-Disposition": `attachment; filename="${name}"` }), blob: async () => new Blob(["PK"]) } as Response;
  }));
}

describe("ImportTemplateDownloads", () => {
  it("downloads the Excel and the Word template from the server", async () => {
    serve();
    render(<ImportTemplateDownloads />);
    fireEvent.click(screen.getByRole("button", { name: "Download Excel template (.xlsx)" }));
    await waitFor(() => expect(saved).toEqual(["question-import-template.xlsx"]));
    fireEvent.click(screen.getByRole("button", { name: "Download Word template (.docx)" }));
    await waitFor(() => expect(saved).toEqual(["question-import-template.xlsx", "question-import-template.docx"]));
    expect(fetched).toEqual(["/api/v1/bulk-import-templates/xlsx", "/api/v1/bulk-import-templates/docx"]);
  });

  it("shows the server's refusal instead of saving anything", async () => {
    serve(403);
    render(<ImportTemplateDownloads />);
    fireEvent.click(screen.getByRole("button", { name: "Download Excel template (.xlsx)" }));
    expect(await screen.findByText("Not allowed.")).toBeTruthy();
    expect(saved).toEqual([]);
  });
});
