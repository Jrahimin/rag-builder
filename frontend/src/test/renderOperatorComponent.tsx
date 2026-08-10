import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { AdminAuthProvider } from "../auth/AdminAuthProvider";

export function renderOperatorComponent(node: ReactNode, route = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <AdminAuthProvider initialAdmin={{ id: "test-admin", email: "owner@example.com", role: "SUPER_ADMIN", last_login_at: null }}>
            {node}
          </AdminAuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}
