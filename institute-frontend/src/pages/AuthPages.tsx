import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Card, Field } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { api } from "../services/api";

/* ---------- Shared Icons ---------- */

const ICONS = {
  shield: (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2 4 6v6c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V6l-8-4z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  ),
  mail: (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  ),
  key: (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="15" r="4" />
      <path d="m10.8 12.2 8.2-8.2M16 4l4 4M14 6l4 4" />
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

function AuthLayout({ title, icon = ICONS.shield, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="auth-wrap">
      <Card className="auth-card">
        <h1 className="auth-title">
          <span className="auth-badge">{icon}</span>
          {title}
        </h1>
        {children}
      </Card>
    </div>
  );
}

/* ---------- Reusable Password Input ---------- */

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
          top: 34, // Adjust this if your Field label height changes
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

/* ---------- Sign in ---------- */

export function LoginPage() {
  const { me, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as { expired?: boolean };
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useAction();

  if (me) return <Navigate to="/" replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await login(code.trim(), password);
      if (r.status === "MFA_REQUIRED") navigate("/mfa", { state: { challengeId: r.challenge_id } });
      else navigate("/");
    });
  };

  return (
    <AuthLayout title="Welcome back" icon={ICONS.shield}>
      <p className="muted small" style={{ marginTop: -8, marginBottom: 20 }}>
        Sign in to the Institute Portal with your staff credentials.
      </p>
      <form onSubmit={submit} noValidate>
        {state.expired && <Alert kind="warn">Your session expired. Please sign in again.</Alert>}
        {error && <Alert kind="error">{error}</Alert>}
        <Field label="Staff ID" value={code} onChange={(e) => setCode(e.target.value)}
          autoComplete="username" required autoFocus placeholder="STF000001" />
        
        <PasswordField 
          label="Password" 
          value={password} 
          onChange={(e) => setPassword(e.target.value)} 
          autoComplete="current-password" 
          required 
        />
        
        <div className="row between">
          <Button type="submit" busy={busy} disabled={!code || !password}>Sign in</Button>
          <Link to="/forgot-password" className="small">Forgot password?</Link>
        </div>
      </form>
    </AuthLayout>
  );
}

/* ---------- MFA ---------- */

export function MfaPage() {
  const { verifyMfa, me } = useAuth();
  const navigate = useNavigate();
  const challengeId = ((useLocation().state ?? {}) as { challengeId?: string }).challengeId;
  const [otp, setOtp] = useState("");
  const [cooldown, setCooldown] = useState(60);
  const [notice, setNotice] = useState("");
  const { busy, error, run } = useAction();
  const resend = useAction();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => { inputRef.current?.focus(); }, []);

  if (me) return <Navigate to="/" replace />;
  if (!challengeId) return <Navigate to="/login" replace />;

  const digits = useMemo(() => otp.padEnd(6, " ").slice(0, 6).split(""), [otp]);

  return (
    <AuthLayout title="Verify it's you" icon={ICONS.mail}>
      <p className="muted small" style={{ marginTop: -8, marginBottom: 20 }}>
        We emailed a 6-digit code to the address on your account. It expires in 5 minutes.
      </p>
      <form onSubmit={(e) => {
        e.preventDefault();
        void run(async () => { await verifyMfa(challengeId, otp); navigate("/"); });
      }}>
        {error && <Alert kind="error">{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}

        <div className="otp-grid" onClick={() => inputRef.current?.focus()}>
          {digits.map((d, i) => (
            <div key={i} className="field-input"
              style={{
                padding: "14px 0", textAlign: "center", fontSize: "1.4rem",
                fontWeight: 700, cursor: "text",
                borderColor: i === otp.length ? "var(--c-primary)" : undefined,
                boxShadow: i === otp.length ? "0 0 0 3px rgba(79, 70, 229, 0.15)" : undefined,
              }}>
              {d.trim() || <span style={{ opacity: 0.25 }}>•</span>}
            </div>
          ))}
        </div>
        <input
          ref={inputRef}
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          autoComplete="one-time-code"
          value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
          required
          aria-label="Verification code"
          style={{ position: "absolute", opacity: 0, pointerEvents: "none", width: 0, height: 0 }}
        />

        <div className="row between">
          <Button type="submit" busy={busy} disabled={otp.length !== 6}>Verify</Button>
          <Button type="button" variant="secondary" disabled={cooldown > 0} busy={resend.busy}
            onClick={() => void resend.run(async () => {
              await api("/api/v1/auth/mfa/resend", { method: "POST", body: { challenge_id: challengeId } });
              setNotice("A new code was sent."); setCooldown(60);
            })}>
            {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
          </Button>
        </div>
        {resend.error && <Alert kind="error">{resend.error}</Alert>}
        <p className="small" style={{ marginTop: 20 }}><Link to="/login">← Back to sign in</Link></p>
      </form>
    </AuthLayout>
  );
}

/* ---------- Verify email ---------- */

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();

  if (!token)
    return <AuthLayout title="Verify email" icon={ICONS.mail}>
      <Alert kind="error">This link is missing its token. Open the link from your email again.</Alert>
    </AuthLayout>;

  if (done)
    return <AuthLayout title="Email verified" icon={ICONS.mail}>
      <Alert kind="success">Your email is verified.</Alert>
      <p><Link to="/login">Go to sign in →</Link></p>
    </AuthLayout>;

  return (
    <AuthLayout title="Verify email & set password" icon={ICONS.mail}>
      <form onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await api("/api/v1/auth/verify-email", { method: "POST", body: { token, new_password: password || null } });
          setDone(true);
        });
      }}>
        {error && <Alert kind="error">{error}</Alert>}
        
        <PasswordField 
          label="Choose a password" 
          value={password}
          onChange={(e) => setPassword(e.target.value)} 
          autoComplete="new-password"
          hint="At least 12 characters. A passphrase works well. Needed when you are activating a new account." 
        />
        
        <Button type="submit" busy={busy}>Verify email</Button>
      </form>
    </AuthLayout>
  );
}

/* ---------- Forgot password ---------- */

export function ForgotPasswordPage() {
  const [identifier, setIdentifier] = useState("");
  const [sent, setSent] = useState(false);
  const { busy, error, run } = useAction();

  return (
    <AuthLayout title="Reset your password" icon={ICONS.key}>
      {sent ? (
        <Alert kind="success">
          If the account exists and its email is verified, a reset link has been sent. It expires in 15 minutes.
        </Alert>
      ) : (
        <form onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await api("/api/v1/auth/forgot-password", { method: "POST", body: { identifier } });
            setSent(true);
          });
        }}>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Staff ID or email" value={identifier}
            onChange={(e) => setIdentifier(e.target.value)} required autoFocus />
          <div className="row between">
            <span />
            <Button type="submit" busy={busy} disabled={identifier.length < 3}>Send reset link</Button>
          </div>
        </form>
      )}
      <p className="small" style={{ marginTop: 20 }}><Link to="/login">← Back to sign in</Link></p>
    </AuthLayout>
  );
}

/* ---------- Reset password ---------- */

export function ResetPasswordPage() {
  const token = useSearchParams()[0].get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();

  if (done)
    return <AuthLayout title="Password updated" icon={ICONS.key}>
      <Alert kind="success">Your password was changed. Sign in with the new one.</Alert>
      <p><Link to="/login">Sign in →</Link></p>
    </AuthLayout>;

  return (
    <AuthLayout title="Choose a new password" icon={ICONS.key}>
      <form onSubmit={(e) => {
        e.preventDefault();
        void run(async () => {
          await api("/api/v1/auth/reset-password", { method: "POST", body: { token, new_password: password } });
          setDone(true);
        });
      }}>
        {!token && <Alert kind="error">This link is missing its token.</Alert>}
        {error && <Alert kind="error">{error}</Alert>}
        
        <PasswordField 
          label="New password" 
          value={password}
          onChange={(e) => setPassword(e.target.value)} 
          autoComplete="new-password"
          hint="At least 12 characters." 
          required 
          autoFocus 
        />

        <div className="row between">
          <span />
          <Button type="submit" busy={busy} disabled={!token || password.length < 12}>Update password</Button>
        </div>
      </form>
    </AuthLayout>
  );
}