import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { adminAuthApi, type CurrentAdmin } from "./adminAuthApi";
import { AdminAuthContext } from "./adminAuthContext";

export function AdminAuthProvider({
  children,
  initialAdmin,
}: {
  children: ReactNode;
  initialAdmin?: CurrentAdmin | null;
}) {
  const [admin, setAdmin] = useState<CurrentAdmin | null>(initialAdmin ?? null);
  const [loading, setLoading] = useState(initialAdmin === undefined);

  useEffect(() => {
    if (initialAdmin !== undefined) return;
    let active = true;
    async function bootstrap() {
      try {
        let current = await adminAuthApi.me();
        if (!current) {
          await adminAuthApi.refresh();
          current = await adminAuthApi.me();
        }
        if (active) setAdmin(current);
      } catch {
        if (active) setAdmin(null);
      } finally {
        if (active) setLoading(false);
      }
    }
    void bootstrap();
    return () => {
      active = false;
    };
  }, [initialAdmin]);

  const login = useCallback(async (email: string, password: string) => {
    const current = await adminAuthApi.login(email, password);
    if (!current) throw new Error("The login response did not include an administrator.");
    setAdmin(current);
    return current;
  }, []);
  const logout = useCallback(async () => {
    try {
      await adminAuthApi.logout();
    } finally {
      setAdmin(null);
    }
  }, []);
  const value = useMemo(() => ({ admin, loading, login, logout }), [admin, loading, login, logout]);
  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}
