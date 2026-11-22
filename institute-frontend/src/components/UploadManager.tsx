import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { SENDING, UploadsContext, useUploads, type UploadItem, type Uploads } from "../hooks/uploads";
import { ApiError } from "../services/api";
import { completeUpload, sendPart, startUpload, type UploadWhere } from "../services/bulkImports";
import { Button } from "./ui";

/* Background uploads: a question file is sent in pieces (resumable, routes/bulk_uploads.py) by this provider, which lives above the
 * pages, so the transfer keeps going while the user moves around the portal. A network drop is retried automatically; a closed tab
 * stops the transfer, and choosing the same file again later continues from where it stopped. Once the file has arrived, everything
 * else (reading, checking, importing) runs on the server, and the bell tells the uploader when the preview is ready. */

const RETRIES = 6;                                  // per piece, on a network failure: 1 s, 2 s, 4 s ... then the item shows an error
const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const sizeText = (bytes: number) => (bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB`
  : bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

type Job = { file: File; where: UploadWhere; subjectId: string; stop: boolean };

export function UploadsProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [version, setVersion] = useState(0);
  const jobs = useRef(new Map<string, Job>());
  const update = useCallback((key: string, change: Partial<UploadItem>) =>
    setItems((all) => all.map((i) => (i.key === key ? { ...i, ...change } : i))), []);

  const run = useCallback(async (key: string) => {
    const job = jobs.current.get(key);
    if (!job) return;
    job.stop = false;
    try {
      update(key, { status: "starting", error: undefined });
      const s = await startUpload(job.where, job.subjectId, job.file);            // the server says how much it already has
      update(key, { uploadId: s.upload_id, sent: s.received_bytes, resumedFrom: s.resumed && s.received_bytes ? s.received_bytes : null,
        status: "sending" });
      for (let n = s.received_parts; n * s.chunk_size < job.file.size; n++) {
        if (job.stop) { update(key, { status: "stopped" }); return; }
        const from = n * s.chunk_size;
        const piece = job.file.slice(from, Math.min(job.file.size, from + s.chunk_size));
        for (let attempt = 0; ; attempt++) {
          try {
            await sendPart(s.upload_id, n, piece, (f) => update(key, { sent: from + f * piece.size }));
            break;
          } catch (e) {
            if (!(e instanceof ApiError) || e.status !== 0 || attempt >= RETRIES) throw e;     // only a lost connection is retried
            await sleep(1000 * 2 ** attempt);
          }
        }
      }
      update(key, { status: "finishing", sent: job.file.size });
      const created = await completeUpload(s.upload_id);
      update(key, { status: "done", jobId: created.job_id });
      jobs.current.delete(key);
      setVersion((v) => v + 1);
    } catch (e) {
      update(key, { status: "error", error: e instanceof Error ? e.message : "The upload stopped." });
    }
  }, [update]);

  const start = useCallback((file: File, where: UploadWhere, subjectId: string, target: string) => {
    const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    jobs.current.set(key, { file, where, subjectId, stop: false });
    setItems((all) => [...all, { key, fileName: file.name, target, size: file.size, sent: 0, resumedFrom: null, status: "starting" }]);
    void run(key);
  }, [run]);
  const retry = useCallback((key: string) => void run(key), [run]);
  const stop = useCallback((key: string) => { const job = jobs.current.get(key); if (job) job.stop = true; }, []);
  const dismiss = useCallback((key: string) => {
    jobs.current.delete(key);
    setItems((all) => all.filter((i) => i.key !== key));
  }, []);
  const active = items.filter((i) => SENDING.includes(i.status)).length;

  useEffect(() => {
    if (!active) return undefined;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };     // the browser asks "Leave site?"
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active]);

  const value = useMemo<Uploads>(() => ({ items, start, retry, stop, dismiss, active, version }),
    [items, start, retry, stop, dismiss, active, version]);
  return <UploadsContext.Provider value={value}>{children}</UploadsContext.Provider>;
}

const STATUS_TEXT: Record<UploadItem["status"], string> = {
  starting: "Starting…", sending: "Uploading", finishing: "Putting the file together…", done: "Upload finished",
  error: "Stopped", stopped: "Paused",
};

/** The uploads of this tab, bottom right on every page, and a green message when a file has fully arrived. */
export function UploadTray() {
  const { items, retry, stop, dismiss } = useUploads();
  const [toast, setToast] = useState<UploadItem | null>(null);
  const announced = useRef(new Set<string>());
  useEffect(() => {
    const done = items.find((i) => i.status === "done" && !announced.current.has(i.key));
    if (!done) return undefined;
    announced.current.add(done.key);
    setToast(done);
    const timer = window.setTimeout(() => setToast(null), 9000);
    return () => window.clearTimeout(timer);
  }, [items]);
  if (!items.length && !toast) return null;
  return (
    <>
      {toast && (
        <div className="upload-toast" role="status">
          <strong>Upload finished: {toast.fileName}</strong>
          <span>Your file is being checked on the server. You can keep working, or sign out: the bell will tell you when it is ready to review.</span>
          <span className="row">
            {toast.jobId && <Link to={`/papers/bulk-import/${toast.jobId}`} onClick={() => setToast(null)}>Open the import</Link>}
            <button type="button" className="link-btn" onClick={() => setToast(null)} aria-label="Close message">Close</button>
          </span>
        </div>
      )}
      {items.length > 0 && (
        <section className="upload-tray" aria-label="Uploads">
          <h2>Uploads</h2>
          <ul className="plain">
            {items.map((i) => {
              const percent = i.size ? Math.floor((100 * i.sent) / i.size) : 0;
              return (
                <li key={i.key}>
                  <div className="row between"><strong className="upload-tray-name" title={i.fileName}>{i.fileName}</strong>
                    <span className={`small ${i.status === "error" ? "error-text" : i.status === "done" ? "ok-text" : "muted"}`}>{STATUS_TEXT[i.status]}</span></div>
                  <progress max={100} value={i.status === "done" ? 100 : percent} aria-label={`${i.fileName}: ${percent}% uploaded`} />
                  <div className="small muted">
                    {sizeText(Math.min(i.sent, i.size))} of {sizeText(i.size)} · {i.target}
                    {i.resumedFrom ? ` · continued from ${sizeText(i.resumedFrom)}` : ""}
                  </div>
                  {i.error && <div className="small error-text">{i.error}</div>}
                  <div className="row">
                    {SENDING.includes(i.status) && i.status === "sending" && <Button small variant="secondary" onClick={() => stop(i.key)}>Pause</Button>}
                    {(i.status === "error" || i.status === "stopped") && <Button small onClick={() => retry(i.key)}>Continue</Button>}
                    {i.status === "done" && i.jobId && <Link className="btn small secondary" to={`/papers/bulk-import/${i.jobId}`}>Open</Link>}
                    {!SENDING.includes(i.status) && <Button small variant="secondary" onClick={() => dismiss(i.key)}>Dismiss</Button>}
                  </div>
                </li>
              );
            })}
          </ul>
          <p className="small muted">Keep this tab open while a file uploads; you can use other pages meanwhile. If the upload stops, choose the
            same file again in Bulk Upload: it continues where it stopped.</p>
        </section>
      )}
    </>
  );
}
