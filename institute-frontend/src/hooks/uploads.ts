import { createContext, useContext } from "react";
import type { UploadWhere } from "../services/bulkImports";

/** One file the browser is sending in the background (components/UploadManager.tsx). */
export type UploadItem = {
  key: string;                       // this browser tab's own id for the item
  fileName: string;
  target: string;                    // where the questions go, in words ("Orbit Academy", "Platform question bank")
  size: number;
  sent: number;                      // bytes the server has (or that are on their way)
  resumedFrom: number | null;        // bytes that had already arrived in an earlier, interrupted attempt
  status: "starting" | "sending" | "finishing" | "done" | "error" | "stopped";
  error?: string;
  uploadId?: string;
  jobId?: string;
};

export type Uploads = {
  items: UploadItem[];
  /** Start sending `file` (or continue it, when the same file was sent before and cut off). Returns at once: it runs in the background. */
  start: (file: File, where: UploadWhere, subjectId: string, target: string) => void;
  retry: (key: string) => void;
  stop: (key: string) => void;
  dismiss: (key: string) => void;
  /** Items still sending: leaving the site would stop them. */
  active: number;
  /** Bumped whenever an upload becomes an import, so lists of imports refresh. */
  version: number;
};

export const UploadsContext = createContext<Uploads | null>(null);

export function useUploads(): Uploads {
  const value = useContext(UploadsContext);
  if (!value) throw new Error("useUploads needs <UploadsProvider>");
  return value;
}

export const SENDING: UploadItem["status"][] = ["starting", "sending", "finishing"];
