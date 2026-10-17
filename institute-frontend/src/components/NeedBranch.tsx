import type { ReactNode } from "react";
import { useScope } from "../auth/ScopeContext";
import ScopeBar from "./ScopeBar";
import { EmptyState, Spinner } from "./ui";

/** Branch-scoped pages: shows the in-page provider/branch choice, then renders children once both are selected. */
export default function NeedBranch({ children }: { children: (ids: { providerId: string; branchId: string }) => ReactNode }) {
  const { providerId, branchId, loading, providers, branches } = useScope();
  if (loading) return <Spinner />;
  return (
    <>
      <ScopeBar />
      {!providerId || !branchId ? (
        <EmptyState
          title={providers.length === 0 ? "No providers available" : "No branch available"}
          hint={providers.length === 0 ? "There is no provider you can access yet." : branches.length === 0 ? "This provider has no branches you can access yet." : "Choose a branch above."}
        />
      ) : children({ providerId, branchId })}
    </>
  );
}
