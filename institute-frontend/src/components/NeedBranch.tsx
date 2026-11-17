import type { ReactNode } from "react";
import { useScope } from "../auth/ScopeContext";
import ScopeBar, { type AllBranches } from "./ScopeBar";
import { EmptyState, Spinner } from "./ui";

/** Branch-scoped pages: shows the in-page provider/branch choice, then renders children once both are selected.
 * With `allBranches`, the branch list also offers "All Branches"; while it is on, children get branchId "". */
export default function NeedBranch({ children, allBranches }: { children: (ids: { providerId: string; branchId: string }) => ReactNode; allBranches?: AllBranches }) {
  const { providerId, branchId, loading, providers, branches } = useScope();
  if (loading) return <Spinner />;
  return (
    <>
      <ScopeBar allBranches={allBranches} />
      {!providerId || !branchId ? (
        <EmptyState
          title={providers.length === 0 ? "No providers available" : "No branch available"}
          hint={providers.length === 0 ? "There is no provider you can access yet." : branches.length === 0 ? "This provider has no branches you can access yet." : "Choose a branch above."}
        />
      ) : children({ providerId, branchId: allBranches?.on ? "" : branchId })}
    </>
  );
}
