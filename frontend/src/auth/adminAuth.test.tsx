import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { AdminAuthProvider } from "./AdminAuthProvider";
import { adminAuthApi } from "./adminAuthApi";
import { LoginPage } from "./LoginPage";
import { ProtectedAdminRoute } from "./ProtectedAdminRoute";
import { useAdminAuth } from "./useAdminAuth";

const admin = {
  id: "admin-1",
  email: "owner@example.com",
  role: "SUPER_ADMIN" as const,
  last_login_at: null,
};

test("validates required login credentials before submitting", async () => {
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AdminAuthProvider initialAdmin={null}>
        <LoginPage />
      </AdminAuthProvider>
    </MemoryRouter>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Enter your email and password.");
});

test("logs in and navigates into the protected console", async () => {
  vi.spyOn(adminAuthApi, "login").mockResolvedValue(admin);
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AdminAuthProvider initialAdmin={null}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>Protected console</p>} />
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );

  fireEvent.change(screen.getByLabelText("Email"), { target: { value: admin.email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByText("Protected console")).toBeInTheDocument();
});

test("redirects an unauthenticated visitor from a protected route", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AdminAuthProvider initialAdmin={null}>
        <Routes>
          <Route element={<ProtectedAdminRoute />}>
            <Route path="/" element={<p>Protected console</p>} />
          </Route>
          <Route path="/login" element={<p>Login screen</p>} />
        </Routes>
      </AdminAuthProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText("Login screen")).toBeInTheDocument());
});

function LogoutControl() {
  const auth = useAdminAuth();
  return (
    <>
      <p>{auth.admin?.email ?? "Signed out"}</p>
      <button type="button" onClick={() => void auth.logout()}>
        Log out
      </button>
    </>
  );
}

test("clears current-admin state even when server logout succeeds", async () => {
  vi.spyOn(adminAuthApi, "logout").mockResolvedValue(null);
  render(
    <AdminAuthProvider initialAdmin={admin}>
      <LogoutControl />
    </AdminAuthProvider>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Log out" }));
  await waitFor(() => expect(screen.getByText("Signed out")).toBeInTheDocument());
});
