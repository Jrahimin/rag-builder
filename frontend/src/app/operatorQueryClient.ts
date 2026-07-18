import { QueryClient } from "@tanstack/react-query";
import { OperatorApiError } from "../api/operatorApiClient";

export function createOperatorQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        retry: (failureCount, error) => {
          if (error instanceof OperatorApiError && [400, 401, 403, 404].includes(error.status))
            return false;
          return failureCount < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
  });
}
