import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAdminAuth } from "./useAdminAuth";

export function ProtectedAdminRoute() {
  const { admin, loading } = useAdminAuth();
  const location = useLocation();
  if (loading) return <main className="auth-loading">Checking secure operator session…</main>;
  if (!admin) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}
