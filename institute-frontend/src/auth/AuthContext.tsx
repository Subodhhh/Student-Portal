/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { SESSION_EXPIRED_EVENT, api, setCsrfToken } from "../services/api";

export type Role = "SUPER_ADMIN" | "ADMIN" | "PROVIDER_ADMIN" | "BRANCH_ADMIN" | "TEACHER";
export type Assignment = { assignment_id: string; role: Role; provider_id: string | null; branch_id: string | null };
export type Me = {
  staff_id: string; staff_code: string; full_name: string; email: string; primary_role: Role;
  assignments: Assignment[]; permissions: string[]; csrf_token: string | null;
};
type AuthResult = { status: "AUTHENTICATED" | "MFA_REQUIRED"; challenge_id: string | null; me: Me | null };

type AuthState = {
  me: Me | null;
  loading: boolean;
  expired: boolean;
  can: (permission: string) => boolean;
  login: (staffCode: string, password: string) => Promise<AuthResult>;
  verifyMfa: (challengeId: string, otp: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);

  const accept = useCallback((m: Me) => { setCsrfToken(m.csrf_token); setMe(m); setExpired(false); }, []);

  useEffect(() => {
    api<Me>("/api/v1/auth/me").then(accept).catch(() => setMe(null)).finally(() => setLoading(false));
  }, [accept]);

  useEffect(() => {
    const onExpired = () => { setCsrfToken(null); setMe((cur) => { if (cur) setExpired(true); return null; }); };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  const value = useMemo<AuthState>(() => ({
    me, loading, expired,
    can: (permission) => !!me?.permissions.includes(permission),
    login: async (staffCode, password) => {
      const r = await api<AuthResult>("/api/v1/auth/login", { method: "POST", body: { staff_code: staffCode, password } });
      if (r.status === "AUTHENTICATED" && r.me) accept(r.me);
      return r;
    },
    verifyMfa: async (challengeId, otp) => {
      const r = await api<AuthResult>("/api/v1/auth/mfa/verify", { method: "POST", body: { challenge_id: challengeId, otp } });
      if (r.me) accept(r.me);
    },
    logout: async () => {
      await api("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
      setCsrfToken(null);
      setMe(null);
    },
  }), [me, loading, expired, accept]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
