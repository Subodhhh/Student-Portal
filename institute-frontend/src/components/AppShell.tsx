
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL, visibleNav } from "../auth/nav";
import { useScope } from "../auth/ScopeContext";
import { useApi } from "../hooks/useApi";
import { Button } from "./ui";

export default function AppShell() {
  const { me, logout } = useAuth();
  const scope = useScope();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const unread = useApi<{ unread_count: number }>(
    "/api/v1/notifications/unread-count",
  );

  if (!me) return null;

  return (
    <div className="shell">
      <aside
        className={`sidebar ${open ? "open" : ""}`}
        aria-label="Primary"
      >
        <div className="brand">Institute Portal</div>

        <nav className="sidebar-nav" onClick={() => setOpen(false)}>
          {visibleNav(me.permissions, me.primary_role).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
            >
              {item.label}
              {item.to === "/notifications" &&
                (unread.data?.unread_count ?? 0) > 0
                ? ` (${unread.data?.unread_count})`
                : ""}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-account">
          <NavLink to="/account" onClick={() => setOpen(false)}>
            My Account
          </NavLink>
        </div>
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <div className="row">
            {/* Every role needs this: branch-scoped pages (students, papers, plans, exams) work in the selected branch.
                A Super Admin sees every provider; others only those their assignments allow (the backend enforces it). */}
            {scope.providers.length > 0 && (
              <>
                <label className="sr-only" htmlFor="scope-provider">
                  Provider
                </label>
                <select
                  id="scope-provider"
                  value={scope.providerId}
                  onChange={(event) =>
                    scope.setProviderId(event.target.value)
                  }
                  disabled={scope.providers.length < 2}
                >
                  {scope.providers.map((provider) => (
                    <option
                      key={provider.provider_id}
                      value={provider.provider_id}
                    >
                      {provider.name}
                    </option>
                  ))}
                </select>

                <label className="sr-only" htmlFor="scope-branch">
                  Branch
                </label>
                <select
                  id="scope-branch"
                  value={scope.branchId}
                  onChange={(event) =>
                    scope.setBranchId(event.target.value)
                  }
                  disabled={scope.branches.length < 2}
                >
                  {scope.branches.length === 0 && (
                    <option value="">No branches</option>
                  )}
                  {scope.branches.map((branch) => (
                    <option
                      key={branch.branch_id}
                      value={branch.branch_id}
                    >
                      {branch.name}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>

          <div className="row">
            <span className="small">
              <strong>{me.full_name}</strong>{" "}
              <span className="muted">
                · {me.staff_code} · {ROLE_LABEL[me.primary_role]}
              </span>
            </span>

            <Button
              variant="secondary"
              small
              onClick={async () => {
                await logout();
                navigate("/login");
              }}
            >
              Sign out
            </Button>
          </div>
        </header>

        <main className="content" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
