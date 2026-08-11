import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { OperatorConsoleApp } from "./app/OperatorConsoleApp";
import { AdminAuthProvider } from "./auth/AdminAuthProvider";
import { createOperatorQueryClient } from "./app/operatorQueryClient";
import "./styles/operatorConsole.css";

const queryClient = createOperatorQueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/operator">
        <AdminAuthProvider>
          <OperatorConsoleApp />
        </AdminAuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
