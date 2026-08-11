import { createContext } from "react";
import type { CurrentAdmin } from "./adminAuthApi";

export type AdminAuthContextValue = {
  admin: CurrentAdmin | null;
  loading: boolean;
  login(email: string, password: string): Promise<CurrentAdmin>;
  logout(): Promise<void>;
};

export const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);
