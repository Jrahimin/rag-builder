export type CurrentAdmin = {
  id: string;
  email: string;
  role: "SUPER_ADMIN" | "ADMIN";
  last_login_at: string | null;
};

type Success<T> = { success: true; data: T | null; message?: string | null };
type Failure = { error: { code: string; message: string } };

export class AdminAuthError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "AdminAuthError";
  }
}

function csrfToken() {
  return document.cookie
    .split("; ")
    .find((entry) => entry.startsWith("ape_admin_csrf="))
    ?.split("=", 2)[1];
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T | null> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
  }
  const response = await fetch(apiUrl(`/api/v1/auth${path}`), {
    ...init,
    headers,
    credentials: "include",
  });
  const payload = (await response.json().catch(() => null)) as Success<T> | Failure | null;
  if (!response.ok || !payload || "error" in payload) {
    throw new AdminAuthError(
      response.status,
      payload && "error" in payload ? payload.error.message : "Authentication request failed.",
    );
  }
  return payload.data;
}

export const adminAuthApi = {
  login: (email: string, password: string) =>
    request<CurrentAdmin>("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<CurrentAdmin>("/me"),
  refresh: () => request<null>("/refresh", { method: "POST" }),
  logout: () => request<null>("/logout", { method: "POST" }),
};

export function getCsrfHeader(): Record<string, string> {
  const token = csrfToken();
  return token ? { "X-CSRF-Token": decodeURIComponent(token) } : {};
}
import { apiUrl } from "../api/apiOrigin";

export const ADMIN_AUTH_EXPIRED_EVENT = "ape-admin-auth-expired";
