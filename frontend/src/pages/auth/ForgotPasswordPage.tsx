import { useState } from "react";
import { Link } from "react-router-dom";
import "../../App.css";
import { forgotStudentPassword } from "../../services/authService";

function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    setEmailError("");

    if (!email.trim()) {
      setEmailError("Email is required.");
      return;
    }

    try {
      setIsLoading(true);

      await forgotStudentPassword(email);

      setIsSubmitted(true);
    } catch (error) {
      setEmailError(
        error instanceof Error
          ? error.message
          : "Password reset request failed.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        {!isSubmitted ? (
          <>
            <h1>Forgot Password</h1>

            <p className="forgot-password-description">
              Enter your registered email address.
            </p>

            <form onSubmit={handleSubmit}>
              <label htmlFor="email">Email Address</label>

              <input
                id="email"
                type="email"
                placeholder="Enter your email address"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                  setEmailError("");
                }}
                disabled={isLoading}
              />

              {emailError && (
                <p className="login-error">{emailError}</p>
              )}

              <button type="submit" disabled={isLoading}>
                {isLoading ? "Sending..." : "Send Reset Link"}
              </button>

              <Link to="/login" className="back-to-login-link">
                Back to Login
              </Link>
            </form>
          </>
        ) : (
          <>
            <h1>Check Your Email</h1>

            <p className="forgot-password-description">
              A reset link has been sent.
            </p>

            <Link to="/login" className="back-to-login-link">
              Back to Login
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

export default ForgotPasswordPage;