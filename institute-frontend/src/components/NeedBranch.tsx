import type { ReactNode } from "react";
import { useScope } from "../auth/ScopeContext";
import { EmptyState, Spinner } from "./ui";

/** Renders children only once a provider and branch are selected (pages that are branch-scoped). */
export default function NeedBranch({ children }: { children: (ids: { providerId: string; branchId: string }) => ReactNode }) {
  const { providerId, branchId, loading, branches } = useScope();
  if (loading) return <Spinner />;
  if (!providerId || !branchId) {
    return <EmptyState title="No branch selected" hint={branches.length === 0 ? "This provider has no branches you can access yet." : "Choose a branch in the top bar."} />;
  }
  return <>{children({ providerId, branchId })}</>;
}
