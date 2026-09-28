import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "../../App.css";
import { resetStudentPassword } from "../../services/authService";

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();

    setError("");
    setMessage("");

    if (!token) {
      setError("Invalid or missing reset link.");
      return;
    }

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    try {
      setLoading(true);

      await resetStudentPassword(token, password);

      setMessage("Password reset successful. You can now log in.");
      setPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Password reset failed.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>Reset Password</h1>

        <p className="forgot-password-description">
          Enter your new password.
        </p>

        <form onSubmit={handleSubmit}>
          <label htmlFor="newPassword">New Password</label>

          <input
            id="newPassword"
            type="password"
            placeholder="Enter your new password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setError("");
            }}
            disabled={loading}
          />

          <label htmlFor="confirmPassword">Confirm Password</label>

          <input
            id="confirmPassword"
            type="password"
            placeholder="Confirm your new password"
            value={confirmPassword}
            onChange={(event) => {
              setConfirmPassword(event.target.value);
              setError("");
            }}
            disabled={loading}
          />

          {error && <p className="login-error">{error}</p>}

          {message && (
            <p className="login-success">{message}</p>
          )}

          <button type="submit" disabled={loading}>
            {loading ? "Resetting..." : "Reset Password"}
          </button>

          <Link to="/login" className="back-to-login-link">
            Back to Login
          </Link>
        </form>
      </div>
    </div>
  );
}