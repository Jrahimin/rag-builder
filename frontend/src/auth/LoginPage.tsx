import { FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AdminAuthError } from "./adminAuthApi";
import { useAdminAuth } from "./AdminAuthProvider";

export function LoginPage() {
  const { admin, loading, login } = useAdminAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const destination = (location.state as { from?: string } | null)?.from ?? "/";
  if (!loading && admin) return <Navigate to={destination} replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(destination, { replace: true });
    } catch (cause) {
      setError(cause instanceof AdminAuthError ? "Invalid email or password." : "Unable to sign in.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit} noValidate>
        <div className="login-brand"><span>R</span><div><strong>AI Platform Engine</strong><small>RAG Builder</small></div></div>
        <p className="eyebrow">Super Admin</p>
        <h1>Operator sign in</h1>
        <p>Use the platform owner account to manage this deployment.</p>
        <label>Email<input autoComplete="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} disabled={submitting} /></label>
        <label>Password<input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} disabled={submitting} /></label>
        {error && <p className="login-error" role="alert">{error}</p>}
        <button type="submit" disabled={submitting}>{submitting ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}
