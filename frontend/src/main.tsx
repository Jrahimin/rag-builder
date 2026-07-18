import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { OperatorConsoleApp } from "./app/OperatorConsoleApp";
import { createOperatorQueryClient } from "./app/operatorQueryClient";
import "./styles/operatorConsole.css";

const queryClient = createOperatorQueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/operator">
        <OperatorConsoleApp />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
