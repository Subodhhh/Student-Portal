import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  PROFILE_API_URL,
  NOTIFICATION_API_URL,
} from "../services/api";
import { logoutStudent } from "../services/authService";
import { clearTestAttempts } from "../utils/testAttemptStorage";

type HeaderProps = {
  onMenuClick?: () => void;
};

const pageTitles: Record<
  string,
  {
    title: string;
    subtitle: string;
  }
> = {
  "/dashboard": {
    title: "Dashboard",
    subtitle: "Overview of your tests and performance.",
  },
  "/my-institutes": {
    title: "My Enrollments",
    subtitle: "View your providers, branches, and batches.",
  },
  "/tests": {
    title: "Tests",
    subtitle: "Browse and attempt your available tests.",
  },
  "/scheduled-tests": {
    title: "Scheduled Tests",
    subtitle: "View your upcoming scheduled tests.",
  },
  "/results": {
    title: "Results",
    subtitle: "View your test results and performance.",
  },
  "/ranking": {
    title: "Ranking",
    subtitle: "Compare your performance with other students.",
  },
  "/profile": {
    title: "Profile",
    subtitle: "View and manage your profile information.",
  },
  "/notifications": {
    title: "Notifications",
    subtitle: "View your latest notifications and updates.",
  },
  "/performance": {
    title: "Performance",
    subtitle: "Track your overall test performance.",
  },
};

function Header({ onMenuClick }: HeaderProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const [profileOpen, setProfileOpen] = useState(false);

  const [studentName, setStudentName] = useState(
    () => sessionStorage.getItem("student_name") || "Student"
  );

  const [unreadNotifications, setUnreadNotifications] = useState(0);

  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");

  const isTestResultPage =
    location.pathname.startsWith("/tests/result/");

  const isTestPerformancePage =
    /^\/tests\/[^/]+\/performance$/.test(
      location.pathname
    );

  const currentPage = isTestResultPage
    ? {
        title: "Test Performance",
        subtitle: "View your performance and detailed test results.",
      }
    : isTestPerformancePage
    ? {
        title: "Test Performance",
        subtitle: "View your performance and detailed test results.",
      }
    : pageTitles[location.pathname] ||
      pageTitles["/dashboard"];

  useEffect(() => {
    const fetchStudentProfile = async () => {
      try {
        const response = await fetch(`${PROFILE_API_URL}/profile`, {
          credentials: "include",
        });

        if (!response.ok) {
          return;
        }

        const student = await response.json();

        const fullName = [student.first_name, student.last_name]
          .filter(Boolean)
          .join(" ");

        if (fullName) {
          setStudentName(fullName);
          sessionStorage.setItem("student_name", fullName);
        }
      } catch (error) {
        console.error("Failed to fetch student profile:", error);
      }
    };

    fetchStudentProfile();
  }, []);

  useEffect(() => {
    const fetchUnreadNotificationCount = async () => {
      try {
        const response = await fetch(
          `${NOTIFICATION_API_URL}/notifications/unread-count`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          return;
        }

        const data = await response.json();
        setUnreadNotifications(data.unread_count ?? 0);
      } catch (error) {
        console.error(
          "Failed to fetch unread notification count:",
          error
        );
      }
    };

    fetchUnreadNotificationCount();
  }, []);

  const studentInitial = studentName.charAt(0).toUpperCase();

  const handleLogout = async () => {
    setLoggingOut(true);
    setLogoutError("");

    try {
      // The server must end the session; only then is the student treated as logged out.
      await logoutStudent();
    } catch (error) {
      console.error("Logout failed:", error);
      setLogoutError(
        "Logout failed. You are still signed in. Please try again."
      );
      setLoggingOut(false);
      return;
    }

    sessionStorage.removeItem("student_name");
    clearTestAttempts();
    navigate("/login", { replace: true });
  };

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

        <div className="student-page-heading">
          <h1>{currentPage.title}</h1>
          <p>{currentPage.subtitle}</p>
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
              {unreadNotifications > 99
                ? "99+"
                : unreadNotifications}
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
                onClick={handleLogout}
                disabled={loggingOut}
              >
                {loggingOut ? "Logging out..." : "Logout"}
              </button>

              {logoutError && (
                <p className="logout-error" role="alert">
                  {logoutError}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

export default Header;