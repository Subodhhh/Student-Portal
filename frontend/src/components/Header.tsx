import { useState } from "react";
import { useNavigate } from "react-router-dom";

type HeaderProps = {
  onMenuClick?: () => void;
};

function Header({ onMenuClick }: HeaderProps) {
  const navigate = useNavigate();
  const [profileOpen, setProfileOpen] = useState(false);

  const unreadNotifications = 3;
  const studentName = "Student";
  const studentInitial = studentName.charAt(0).toUpperCase();

  return (
    <header className="student-header">
      <div className="student-header-left">
        <button
          className="mobile-menu-button"
          type="button"
          onClick={onMenuClick}
          aria-label="Open navigation"
        >
          ☰
        </button>

        <div>
          <h1>Student Portal</h1>
        </div>
      </div>

      <div className="student-header-actions">
        <button
          className="notification-button"
          onClick={() => navigate("/notifications")}
          aria-label="Notifications"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>

          {unreadNotifications > 0 && (
            <span className="notification-badge">
              {unreadNotifications > 99 ? "99+" : unreadNotifications}
            </span>
          )}
        </button>

        <div className="student-profile-menu">
          <button
            className="student-avatar-button"
            onClick={() => setProfileOpen((current) => !current)}
            aria-label="Student menu"
          >
            <span className="student-avatar">{studentInitial}</span>
          </button>

          {profileOpen && (
            <div className="student-profile-dropdown">
              <div className="student-profile-info">
                <span className="student-avatar student-avatar-small">
                  {studentInitial}
                </span>

                <div>
                  <strong>{studentName}</strong>
                  <span>Student</span>
                </div>
              </div>

              <div className="student-profile-divider" />

              <button
                className="logout-button"
                onClick={() => navigate("/login")}
              >
                Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export default Header;