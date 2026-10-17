import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL, visibleNav } from "../auth/nav";
import { useApi } from "../hooks/useApi";
import { pageTitle } from "../routes";
import { NavIcon } from "./icons";
import { Button } from "./ui";

export default function AppShell() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const unread = useApi<{ unread_count: number }>(
    "/api/v1/notifications/unread-count",
  );

  if (!me) return null;

  const items = visibleNav(me.permissions, me.primary_role);
  const main = items.filter((item) => item.to !== "/account");
  const account = items.find((item) => item.to === "/account");
  const unreadCount = unread.data?.unread_count ?? 0;

  return (
    <div className="shell">
      <aside
        id="primary-nav"
        className={`sidebar ${open ? "open" : ""}`}
        aria-label="Primary"
      >
        <div className="brand">Institute Portal</div>

        <nav className="sidebar-nav" onClick={() => setOpen(false)}>
          {main.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
            >
              <NavIcon to={item.to} />
              {item.label}
              {item.to === "/notifications" && unreadCount > 0
                ? ` (${unreadCount})`
                : ""}
            </NavLink>
          ))}
        </nav>

        {account && (
          <nav className="sidebar-account" aria-label="Account" onClick={() => setOpen(false)}>
            <NavLink to={account.to}><NavIcon to={account.to} />{account.label}</NavLink>
          </nav>
        )}
      </aside>
      {open && <div className="sidebar-scrim" onClick={() => setOpen(false)} aria-hidden />}

      <div className="shell-main">
        <header className="topbar">
          <div className="topbar-title">
            <button
              type="button"
              className="menu-btn"
              aria-label="Open navigation"
              aria-controls="primary-nav"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              <span aria-hidden>☰</span>
            </button>
            <h1>{pageTitle(location.pathname, me.primary_role)}</h1>
          </div>

          <div className="topbar-user">
            <span className="small topbar-identity">
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
