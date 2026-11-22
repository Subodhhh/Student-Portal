import { useState } from "react";
import { useScope } from "../auth/ScopeContext";
import { SelectField } from "../components/ui";

/** The branch to create something in (a question, a paper, an import) while a page shows "All Branches" (`branchId` ""): a Branch
 * choice, defaulting to the branch last worked in. With one branch selected it is that branch and no field is shown. */
export function useTargetBranch(branchId: string | undefined) {
  const scope = useScope();
  const [pick, setPick] = useState(scope.branchId);
  const field = branchId ? null : (
    <SelectField label="Branch" value={pick} onChange={(e) => setPick(e.target.value)} required>
      {scope.branches.map((b) => <option key={b.branch_id} value={b.branch_id}>{b.name}</option>)}
    </SelectField>
  );
  return { target: branchId || pick, field };
}
