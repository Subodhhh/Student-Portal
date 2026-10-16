import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, Card, Field } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { api } from "../services/api";

function AuthLayout({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="auth-wrap">
      <Card className="auth-card">
        <h1 style={{ fontSize: "1.4rem", marginBottom: "var(--space-4)" }}>{title}</h1>
        {children}
      </Card>
    </div>
  );
}

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
    <AuthLayout title="Institute Portal sign in">
      <form onSubmit={submit} noValidate>
        {state.expired && <Alert kind="warn">Your session expired. Please sign in again.</Alert>}
        {error && <Alert kind="error">{error}</Alert>}
        <div style={{ height: "var(--space-3)" }} />
        <Field label="Staff ID" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="username" required autoFocus placeholder="STF000001" />
        <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        <div className="row between">
          <Button type="submit" busy={busy} disabled={!code || !password}>Sign in</Button>
          <Link to="/forgot-password" className="small">Forgot password?</Link>
        </div>
      </form>
    </AuthLayout>
  );
}

export function MfaPage() {
  const { verifyMfa, me } = useAuth();
  const navigate = useNavigate();
  const challengeId = ((useLocation().state ?? {}) as { challengeId?: string }).challengeId;
  const [otp, setOtp] = useState("");
  const [cooldown, setCooldown] = useState(60);
  const [notice, setNotice] = useState("");
  const { busy, error, run } = useAction();
  const resend = useAction();

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  if (me) return <Navigate to="/" replace />;
  if (!challengeId) return <Navigate to="/login" replace />;

  return (
    <AuthLayout title="Enter your verification code">
      <p className="muted small">We emailed a 6-digit code to the address on your account. It expires in 5 minutes.</p>
      <form onSubmit={(e) => { e.preventDefault(); void run(async () => { await verifyMfa(challengeId, otp); navigate("/"); }); }}>
        {error && <Alert kind="error">{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
        <div style={{ height: "var(--space-3)" }} />
        <Field label="Verification code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} required autoFocus />
        <div className="row between">
          <Button type="submit" busy={busy} disabled={otp.length !== 6}>Verify</Button>
          <Button type="button" variant="secondary" disabled={cooldown > 0} busy={resend.busy}
            onClick={() => void resend.run(async () => { await api("/api/v1/auth/mfa/resend", { method: "POST", body: { challenge_id: challengeId } }); setNotice("A new code was sent."); setCooldown(60); })}>
            {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
          </Button>
        </div>
        {resend.error && <Alert kind="error">{resend.error}</Alert>}
        <p className="small"><Link to="/login">Back to sign in</Link></p>
      </form>
    </AuthLayout>
  );
}

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();
  if (!token) return <AuthLayout title="Verify email"><Alert kind="error">This link is missing its token. Open the link from your email again.</Alert></AuthLayout>;
  if (done) return <AuthLayout title="Email verified"><Alert kind="success">Your email is verified.</Alert><p><Link to="/login">Go to sign in</Link></p></AuthLayout>;
  return (
    <AuthLayout title="Verify email & set password">
      <form onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/api/v1/auth/verify-email", { method: "POST", body: { token, new_password: password || null } }); setDone(true); }); }}>
        {error && <Alert kind="error">{error}</Alert>}
        <div style={{ height: "var(--space-3)" }} />
        <Field label="Choose a password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
          hint="At least 12 characters. A passphrase works well. Needed when you are activating a new account." />
        <Button type="submit" busy={busy}>Verify email</Button>
      </form>
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const [identifier, setIdentifier] = useState("");
  const [sent, setSent] = useState(false);
  const { busy, error, run } = useAction();
  return (
    <AuthLayout title="Reset your password">
      {sent ? <Alert kind="success">If the account exists and its email is verified, a reset link has been sent. It expires in 15 minutes.</Alert> : (
        <form onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/api/v1/auth/forgot-password", { method: "POST", body: { identifier } }); setSent(true); }); }}>
          {error && <Alert kind="error">{error}</Alert>}
          <div style={{ height: "var(--space-3)" }} />
          <Field label="Staff ID or email" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
          <Button type="submit" busy={busy} disabled={identifier.length < 3}>Send reset link</Button>
        </form>
      )}
      <p className="small"><Link to="/login">Back to sign in</Link></p>
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const token = useSearchParams()[0].get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const { busy, error, run } = useAction();
  if (done) return <AuthLayout title="Password updated"><Alert kind="success">Your password was changed. Sign in with the new one.</Alert><p><Link to="/login">Sign in</Link></p></AuthLayout>;
  return (
    <AuthLayout title="Choose a new password">
      <form onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/api/v1/auth/reset-password", { method: "POST", body: { token, new_password: password } }); setDone(true); }); }}>
        {!token && <Alert kind="error">This link is missing its token.</Alert>}
        {error && <Alert kind="error">{error}</Alert>}
        <div style={{ height: "var(--space-3)" }} />
        <Field label="New password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" hint="At least 12 characters." required />
        <Button type="submit" busy={busy} disabled={!token || password.length < 12}>Update password</Button>
      </form>
    </AuthLayout>
  );
}
