import { useContext } from "react";
import { AdminAuthContext } from "./adminAuthContext";

export function useAdminAuth() {
  const value = useContext(AdminAuthContext);
  if (!value) throw new Error("useAdminAuth must be used within AdminAuthProvider");
  return value;
}
