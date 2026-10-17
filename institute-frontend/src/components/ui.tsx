import { Link } from "react-router-dom";
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export function Button({ variant = "primary", small, busy, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger"; small?: boolean; busy?: boolean }) {
  const cls = ["btn", variant === "primary" ? "" : variant, small ? "small" : ""].join(" ").trim();
  return (
    <button {...rest} className={cls} disabled={rest.disabled || busy} aria-busy={busy || undefined}>
      {busy && <span className="spinner" aria-hidden style={{ width: 14, height: 14, borderWidth: 2 }} />}
      {children}
    </button>
  );
}

export function Card({ children, className = "", hover }: { children: ReactNode; className?: string; hover?: boolean }) {
  return <section className={`card ${hover ? "hover" : ""} ${className}`}>{children}</section>;
}

export function Alert({ kind = "info", children }: { kind?: "error" | "success" | "warn" | "info"; children: ReactNode }) {
  return <div className={`alert ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function Badge({ children, tone }: { children: ReactNode; tone?: "ok" | "warn" | "danger" | "info" }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | "info"> = {
  ACTIVE: "ok", PUBLISHED: "ok", PAID: "ok", SUCCEEDED: "ok", SENT: "ok", COMPLETED: "ok", SUCCESS: "ok",
  APPROVED: "info", VALIDATED: "info", SCHEDULED: "info", ASSIGNED: "info", STARTED: "info", DRAFT: "info", GLOBAL: "info",
  ARCHIVED: "warn", FAILURE: "danger",
  OPEN: "warn", IN_PROGRESS: "warn", PENDING: "warn", PARTIAL: "warn", PARTIALLY_PAID: "warn", ISSUED: "warn", SUPERSEDED: "warn",
  INACTIVE: "danger", FAILED: "danger", REJECTED: "danger", CANCELLED: "danger", WITHDRAWN: "danger", DENIED: "danger", MISSED: "danger", VOID: "danger",
};
export const StatusBadge = ({ status }: { status: string }) => <Badge tone={STATUS_TONE[status]}>{status.replace(/_/g, " ")}</Badge>;

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span role="status" className="row"><span className="spinner" aria-hidden /><span className="sr-only">{label}</span></span>;
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {hint && <p className="small">{hint}</p>}
      {action}
    </div>
  );
}

/** Intro row under the shared header (which already shows the page title as the only <h1>): an optional entity title for
 * detail pages (h2), a one-line description and the page's actions. */
export function PageHeader({ title, subtitle, actions, crumbs }: { title?: string; subtitle?: string; actions?: ReactNode; crumbs?: ReactNode[] }) {
  if (!title && !subtitle && !actions && !crumbs) return null;
  return (
    <div className="page-intro">
      {crumbs && (
        <nav className="breadcrumbs" aria-label="Breadcrumb">
          {crumbs.map((c, i) => <span key={i}>{i > 0 && " / "}{c}</span>)}
        </nav>
      )}
      <div className="row between">
        <div className="page-intro-text">
          {title && <h2>{title}</h2>}
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
        {actions && <div className="row">{actions}</div>}
      </div>
    </div>
  );
}

export function StatCard({ label, value, tone, hint, to }: { label: string; value: ReactNode; tone?: "danger" | "warn"; hint?: ReactNode; to?: string }) {
  const body = (
    <>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
      {hint && <div className="hint">{hint}</div>}
    </>
  );
  const style = tone ? { borderColor: tone === "danger" ? "var(--c-danger)" : "var(--c-warn)" } : undefined;
  return to ? <Link className="stat stat-link" style={style} to={to}>{body}</Link> : <div className="stat" style={style}>{body}</div>;
}

/** Search box for list pages: a labelled input with the search role. */
export function SearchField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  const id = useId();
  return (
    <div className="field search-field" role="search">
      <label htmlFor={id}>{label}</label>
      <input id={id} type="search" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

type FieldProps = { label: string; error?: string; hint?: string };

// The <label> holds only the text (not the control), so the accessible name of an input or
// select is exactly its label rather than label + options.
export function Field({ label, error, hint, ...rest }: FieldProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-invalid={!!error} aria-describedby={error ? `${id}-e` : undefined} {...rest} />
      {hint && <small className="muted">{hint}</small>}
      {error && <div className="err" id={`${id}-e`}>{error}</div>}
    </div>
  );
}
export function SelectField({ label, error, children, ...rest }: FieldProps & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} aria-invalid={!!error} {...rest}>{children}</select>
      {error && <div className="err">{error}</div>}
    </div>
  );
}
export function TextAreaField({ label, error, ...rest }: FieldProps & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={3} aria-invalid={!!error} {...rest} />
      {error && <div className="err">{error}</div>}
    </div>
  );
}

const OPEN_MODALS: string[] = [];

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });

  useEffect(() => {
    const previousActiveElement = document.activeElement as HTMLElement | null;
    OPEN_MODALS.push(titleId);
    ref.current?.focus({ preventScroll: true });   // keyboard users land in the dialog
    const handleKeyDown = (event: KeyboardEvent) => {
      // Only the top-most dialog reacts, so Escape in a confirmation does not also close the dialog underneath.
      if (event.key === "Escape" && OPEN_MODALS[OPEN_MODALS.length - 1] === titleId) {
        event.preventDefault();
        closeRef.current();
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      OPEN_MODALS.splice(OPEN_MODALS.indexOf(titleId), 1);
      previousActiveElement?.focus();
    };
  }, [titleId]);

  return (
    <div className="modal-backdrop">
      <div
        ref={ref}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-header">
          <h2 id={titleId}>{title}</h2>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, message, confirmLabel = "Confirm", danger, onConfirm, onCancel, busy, children }:
  { title: string; message: string; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onCancel: () => void; busy?: boolean; children?: ReactNode }) {
  return (
    <Modal title={title} onClose={onCancel}>
      <p>{message}</p>
      {children}
      <div className="modal-actions">
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} busy={busy}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}

export type Column<T> = { header: string; render: (row: T) => ReactNode; key?: string };
export function DataTable<T>({ columns, rows, rowKey, empty }: { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string; empty?: ReactNode }) {
  if (rows.length === 0) return <div className="table-wrap">{empty ?? <EmptyState title="Nothing here yet" />}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map((c) => <th key={c.header} scope="col">{c.header}</th>)}</tr></thead>
        <tbody>{rows.map((r) => <tr key={rowKey(r)}>{columns.map((c) => <td key={c.header}>{c.render(r)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="muted small">Page {page} of {pages} ({total} total)</span>
      <Button variant="secondary" small disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</Button>
      <Button variant="secondary" small disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</Button>
    </nav>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: string }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => <button key={t.id} role="tab" aria-selected={active === t.id} onClick={() => onChange(t.id)}>{t.label}</button>)}
    </div>
  );
}
