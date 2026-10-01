import { useState } from "react";
import { NavLink } from "react-router-dom";

type SidebarProps = {
  onNavigate?: () => void;
};

function Sidebar({ onNavigate }: SidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const isExpanded = !isCollapsed || isHovered;

  return (
    <aside
      className={`student-sidebar ${
        isExpanded ? "sidebar-expanded" : "sidebar-collapsed"
      }`}
      onMouseEnter={() => {
        if (isCollapsed) {
          setIsHovered(true);
        }
      }}
      onMouseLeave={() => {
        if (isCollapsed) {
          setIsHovered(false);
        }
      }}
    >
      <div className="student-sidebar-top">
        <div className="student-logo">
          {isExpanded ? "Student Portal" : "SP"}
        </div>

        <button
          className="sidebar-toggle"
          type="button"
          onClick={() => {
            setIsCollapsed((current) => !current);
            setIsHovered(false);
          }}
          aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {isCollapsed ? "›" : "‹"}
        </button>
      </div>

      <nav className="student-nav">
        <NavLink to="/dashboard" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <rect x="3" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" />
              <rect x="14" y="14" width="7" height="7" rx="1" />
            </svg>
          </span>
          {isExpanded && <span>Dashboard</span>}
        </NavLink>

        <NavLink to="/my-institutes" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <path d="M3 21h18" />
              <path d="M5 21V9l7-5 7 5v12" />
              <path d="M9 21v-6h6v6" />
              <path d="M9 10h.01" />
              <path d="M12 10h.01" />
              <path d="M15 10h.01" />
            </svg>
          </span>
          {isExpanded && <span>My Enrollments</span>}
        </NavLink>

        <NavLink to="/tests" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <rect x="5" y="3" width="14" height="18" rx="2" />
              <path d="M9 7h6" />
              <path d="M9 11h6" />
              <path d="M9 15h4" />
            </svg>
          </span>
          {isExpanded && <span>Tests</span>}
        </NavLink>

        <NavLink to="/scheduled-tests" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <rect x="3" y="5" width="18" height="16" rx="2" />
              <path d="M16 3v4" />
              <path d="M8 3v4" />
              <path d="M3 10h18" />
              <circle cx="16" cy="15" r="3" />
              <path d="M16 13.5V15l1 1" />
            </svg>
          </span>
          {isExpanded && <span>Scheduled Tests</span>}
        </NavLink>

        <NavLink to="/results" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <rect x="4" y="3" width="16" height="18" rx="2" />
              <path d="M8 8h8" />
              <path d="M8 12h4" />
              <path d="m8 16 2 2 5-5" />
            </svg>
          </span>
          {isExpanded && <span>Results</span>}
        </NavLink>

        <NavLink to="/ranking" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <path d="M8 21v-8" />
              <path d="M16 21V9" />
              <path d="M12 21V3" />
              <path d="M5 21h14" />
              <path d="M10 6h4" />
              <path d="M14 6l2-2" />
              <path d="M8 13H5" />
            </svg>
          </span>
          {isExpanded && <span>Ranking</span>}
        </NavLink>

        <NavLink to="/profile" onClick={onNavigate}>
          <span className="sidebar-nav-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <circle cx="12" cy="8" r="4" />
              <path d="M4 21c.8-4 3.5-6 8-6s7.2 2 8 6" />
            </svg>
          </span>
          {isExpanded && <span>Profile</span>}
        </NavLink>
      </nav>
    </aside>
  );
}

export default Sidebar;