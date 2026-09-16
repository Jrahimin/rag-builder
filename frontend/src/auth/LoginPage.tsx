import { FormEvent, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AdminAuthError } from "./adminAuthApi";
import { useAdminAuth } from "./useAdminAuth";

export function LoginPage() {
  const auth = useAdminAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const destination = (location.state as { from?: string } | null)?.from ?? "/";
  if (!auth.loading && auth.admin) return <Navigate to={destination} replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setSubmitting(true);
    try {
      await auth.login(email.trim(), password);
      void navigate(destination, { replace: true });
    } catch (cause) {
      setError(
        cause instanceof AdminAuthError ? "Invalid email or password." : "Unable to sign in.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={(event) => void submit(event)} noValidate>
        <div className="login-brand">
          <span>R</span>
          <div>
            <strong>AI Platform Engine</strong>
            <small>RAG Builder</small>
          </div>
        </div>
        <p className="eyebrow">Operator</p>
        <h1>Operator sign in</h1>
        <p>Use an operator account to manage this deployment.</p>
        <label>
          Email
          <input
            autoComplete="email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={submitting}
          />
        </label>
        <label>
          Password
          <div className="login-password-field">
            <input
              autoComplete="current-password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={submitting}
            />
            <button
              type="button"
              className="login-password-toggle"
              aria-label={showPassword ? "Hide password" : "Show password"}
              onClick={() => setShowPassword((visible) => !visible)}
              disabled={submitting}
            >
              {showPassword ? (
                <EyeOff size={18} aria-hidden="true" />
              ) : (
                <Eye size={18} aria-hidden="true" />
              )}
            </button>
          </div>
        </label>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
