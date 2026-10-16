import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { AUTH_API_URL } from "../services/api";

function ProtectedRoute() {
  const [loading, setLoading] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    fetch(`${AUTH_API_URL}/auth/me`, {
      credentials: "include",
    })
      .then((response) => {
        setAuthenticated(response.ok);
      })
      .catch(() => {
        setAuthenticated(false);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  if (loading) {
    return null;
  }

  if (!authenticated) {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

export default ProtectedRoute;