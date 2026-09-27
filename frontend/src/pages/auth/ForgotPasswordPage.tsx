import { useState } from "react";
import { useNavigate } from "react-router-dom";
import "../../App.css";

function ForgotPasswordPage() {
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();

    setEmailError("");

    if (!email.trim()) {
      setEmailError("Email is required.");
      return;
    }

    setIsLoading(true);

    setTimeout(() => {
      setIsLoading(false);
      setIsSubmitted(true);
    }, 1000);
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
              />

              {emailError && (
                <p className="login-error">{emailError}</p>
              )}

              <button type="submit" disabled={isLoading}>
                {isLoading ? "Sending..." : "Send Reset Link"}
              </button>

              <button
                type="button"
                className="back-to-login"
                onClick={() => navigate("/login")}
              >
                Back to Login
              </button>
            </form>
          </>
        ) : (
          <>
            <h1>Check Your Email</h1>

            <p className="forgot-password-description">
              If the email is registered, a reset link has been sent.
            </p>

            <button type="button" onClick={() => navigate("/login")}>
              Back to Login
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default ForgotPasswordPage;