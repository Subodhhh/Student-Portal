import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Spinner } from "./ui";

/** Route guard. `perm` is a usability gate only; the API enforces the real rule. */
export default function ProtectedRoute({ perm, anyPerm }: { perm?: string; anyPerm?: string[] }) {
  const { me, loading, expired, can } = useAuth();
  const location = useLocation();
  if (loading) return <div className="auth-wrap"><Spinner /></div>;
  if (!me) return <Navigate to="/login" replace state={{ expired, from: location.pathname }} />;
  const allowed = (!perm && !anyPerm) || (perm && can(perm)) || (anyPerm && anyPerm.some(can));
  if (!allowed) return <Navigate to="/forbidden" replace />;
  return <Outlet />;
}
