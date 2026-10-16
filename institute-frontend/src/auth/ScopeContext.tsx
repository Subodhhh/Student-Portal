/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useApi } from "../hooks/useApi";
import type { Page } from "../services/api";
import { useAuth } from "./AuthContext";

export type Provider = { provider_id: string; name: string; status: string; email: string };
export type Branch = { branch_id: string; provider_id: string; name: string; city: string | null; status: string };

type ScopeState = {
  providers: Provider[]; branches: Branch[];
  providerId: string; branchId: string;
  setProviderId: (id: string) => void; setBranchId: (id: string) => void;
  provider: Provider | undefined; branch: Branch | undefined;
  loading: boolean;
  /** Re-fetch providers and branches after one was created, renamed or deactivated. */
  reload: () => void;
};
const ScopeContext = createContext<ScopeState | null>(null);
const EMPTY: never[] = [];

const KEY = "institute.scope";
function read(): { p: string; b: string } {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? "") as { p: string; b: string }; } catch { return { p: "", b: "" }; }
}

/** The provider/branch the user is working in. This is purely a UI selection: the backend
 * re-validates provider and branch on every request, so a tampered value only produces 403s. */
export function ScopeProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const [choice, setChoice] = useState(read);
  const providersQ = useApi<Page<Provider>>(me ? "/api/v1/providers?page_size=100" : null);
  const providers: Provider[] = providersQ.data?.items ?? EMPTY;
  // Effective selection is derived: an unknown/stale saved id falls back to the first available.
  const providerId = providers.some((p) => p.provider_id === choice.p) ? choice.p : (providers[0]?.provider_id ?? "");

  const branchesQ = useApi<Page<Branch>>(providerId ? `/api/v1/providers/${providerId}/branches?page_size=100` : null);
  const branches: Branch[] = branchesQ.data?.items ?? EMPTY;
  const branchId = branches.some((b) => b.branch_id === choice.b) ? choice.b : (branches[0]?.branch_id ?? "");

  useEffect(() => {
    try { sessionStorage.setItem(KEY, JSON.stringify({ p: providerId, b: branchId })); } catch { /* storage may be unavailable */ }
  }, [providerId, branchId]);

  const value: ScopeState = {
    providers, branches, providerId, branchId,
    setProviderId: (id) => setChoice({ p: id, b: "" }),
    setBranchId: (id) => setChoice((c) => ({ ...c, p: providerId, b: id })),
    provider: providers.find((p) => p.provider_id === providerId),
    branch: branches.find((b) => b.branch_id === branchId),
    loading: providersQ.loading || branchesQ.loading,
    reload: () => { providersQ.reload(); branchesQ.reload(); },
  };
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeState {
  const ctx = useContext(ScopeContext);
  if (!ctx) throw new Error("useScope must be used inside ScopeProvider");
  return ctx;
}
