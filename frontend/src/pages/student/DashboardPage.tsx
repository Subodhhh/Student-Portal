import { useEffect, useState } from "react";
import { PROFILE_API_URL } from "../../services/api";
import "../../styles/dashboard.css";

function DashboardPage() {
  const [studentName, setStudentName] = useState("Student");

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
        }
      } catch (error) {
        console.error("Failed to fetch student profile:", error);
      }
    };

    fetchStudentProfile();
  }, []);

  return (
    <div className="dashboard-page">
      <section className="dashboard-welcome">
        <div>
          <h2>Welcome back, {studentName}</h2>
          <p>
            Here is your latest test activity and performance overview.
          </p>
        </div>
      </section>

      <div className="dashboard-section-grid">
        <section className="dashboard-section">
          <div className="section-header">
            <div>
              <h3>Upcoming Tests</h3>
              <p>You don't have any scheduled tests right now.</p>
            </div>
          </div>

          <div className="section-empty">
            <div className="section-empty-icon">✓</div>
            <p>No upcoming tests available.</p>
          </div>
        </section>

        <section className="dashboard-section">
          <div className="section-header">
            <div>
              <h3>Recent Results</h3>
              <p>Your completed test results will appear here.</p>
            </div>
          </div>

          <div className="section-empty">
            <div className="section-empty-icon">—</div>
            <p>No test results available.</p>
          </div>
        </section>
      </div>
    </div>
  );
}

export default DashboardPage;