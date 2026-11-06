import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { Alert, Button, Card, DataTable, Field, PageHeader } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { api, setCsrfToken } from "../services/api";

/* Small inline icons to match the auth pages */
const ICONS = {
  users: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  ),
  lock: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  ),
  eye: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  eyeOff: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
      <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
      <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
      <line x1="2" x2="22" y1="2" y2="22" />
    </svg>
  ),
};

/* Reusable Password Input with visibility toggle */
type PasswordFieldProps = React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
};

function PasswordField({ label, hint, error, ...rest }: PasswordFieldProps) {
  const [show, setShow] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <Field 
        {...rest} 
        label={label} 
        hint={hint} 
        error={error} 
        type={show ? "text" : "password"} 
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        aria-label={show ? "Hide password" : "Show password"}
        style={{
          position: "absolute",
          top: 34,
          right: 12,
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "var(--c-text-muted)",
          padding: 4,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          opacity: 0.7,
          transition: "opacity 150ms"
        }}
        onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}
        onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.7")}
      >
        {show ? ICONS.eyeOff : ICONS.eye}
      </button>
    </div>
  );
}

export default function AccountPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const { busy, error, run } = useAction();

  if (!me) return null;

  // Validation logic
  const hasMinLength = next.length >= 12;
  const matches = next === confirm && next.length > 0;
  const canSubmit = !!current && hasMinLength && matches;

  const submit = () =>
    run(async () => {
      await api("/api/v1/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setCsrfToken(null);
      navigate("/login", { replace: true });
      window.location.reload(); // the server ended every session
    });

  return (
    <>
      <PageHeader
        title="My account"
        subtitle={`${me.full_name} · ${me.staff_code} · ${me.email}`}
      />

      <div className="grid cols-2">
        {/* ----- Roles ----- */}
        <Card>
          <div className="card-section-title">
            <span className="card-section-badge">{ICONS.users}</span>
            <h2>Roles & assignments</h2>
          </div>
          <p className="muted small" style={{ marginTop: -8, marginBottom: 16 }}>
            The scope of access granted to your account.
          </p>
          <DataTable
            rows={me.assignments}
            rowKey={(a) => a.assignment_id}
            columns={[
              {
                header: "Role",
                render: (a) => (
                  <span className="badge info">{ROLE_LABEL[a.role]}</span>
                ),
              },
              {
                header: "Provider",
                render: (a) => a.provider_id?.slice(0, 8) ?? "All",
              },
              {
                header: "Branch",
                render: (a) => a.branch_id?.slice(0, 8) ?? "All",
              },
            ]}
          />
        </Card>

        {/* ----- Password ----- */}
        <Card>
          <div className="card-section-title">
            <span className="card-section-badge">{ICONS.lock}</span>
            <h2>Change password</h2>
          </div>
          <p className="muted small" style={{ marginTop: -8, marginBottom: 16 }}>
            Changing your password signs you out on every device.
          </p>

          {error && <Alert kind="error">{error}</Alert>}

          <PasswordField
            label="Current password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
          />
          
          <PasswordField
            label="New password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            hint="At least 12 characters. A passphrase works well."
          />

          <PasswordField
            label="Confirm new password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            hint={
              confirm.length > 0 && !matches 
                ? "Passwords do not match." 
                : "Re-type your new password."
            }
            error={
              confirm.length > 0 && !matches 
                ? "Passwords do not match." 
                : undefined
            }
          />

          <div className="row between">
            <span />
            <Button
              busy={busy}
              disabled={!canSubmit}
              onClick={() => void submit()}
            >
              Change password
            </Button>
          </div>
        </Card>
      </div>
    </>
  );
}