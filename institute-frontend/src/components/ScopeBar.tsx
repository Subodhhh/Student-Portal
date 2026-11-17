import { useId } from "react";
import { useScope } from "../auth/ScopeContext";

const ALL = "__all__";

export type AllBranches = { on: boolean; set: (on: boolean) => void };

/** The provider (and branch) a page works in, chosen inside the page that needs it. The choice is shared between pages
 * for the session. It only selects what to show: every request is still authorized for that provider/branch by the API. */
export default function ScopeBar({ branch = true, allBranches }: { branch?: boolean; allBranches?: AllBranches }) {
  const scope = useScope();
  const providerSelect = useId();
  const branchSelect = useId();
  if (scope.providers.length === 0) return null;
  return (
    <div className="scope-bar" role="group" aria-label="Working in">
      <span className="scope-bar-label">Working in</span>
      {scope.providers.length === 1 ? (
        <span className="scope-bar-value">{scope.providers[0].name}</span>
      ) : (
        <>
          <label className="sr-only" htmlFor={providerSelect}>Provider</label>
          <select id={providerSelect} value={scope.providerId} onChange={(e) => scope.setProviderId(e.target.value)}>
            {scope.providers.map((p) => <option key={p.provider_id} value={p.provider_id}>{p.name}</option>)}
          </select>
        </>
      )}
      {branch && (
        scope.branches.length === 0 || (scope.branches.length === 1 && !allBranches) ? (
          <span className="scope-bar-value">{scope.branches[0]?.name ?? "No branches"}</span>
        ) : (
          <>
            <label className="sr-only" htmlFor={branchSelect}>Branch</label>
            <select id={branchSelect} value={allBranches?.on ? ALL : scope.branchId} onChange={(e) => {
              allBranches?.set(e.target.value === ALL);
              if (e.target.value !== ALL) scope.setBranchId(e.target.value);
            }}>
              {allBranches && <option value={ALL}>All Branches</option>}
              {scope.branches.map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}
            </select>
          </>
        )
      )}
    </div>
  );
}
