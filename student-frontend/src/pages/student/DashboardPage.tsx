import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PROFILE_API_URL, RESULT_API_URL } from "../../services/api";
import "../../styles/dashboard.css";

interface RecentResult {
  result_id: string;
  attempt_id: string;
  test_id: string;
  student_id: string;
  total_questions: number;
  attempted_questions: number;
  correct_answers: number;
  incorrect_answers: number;
  unanswered_questions: number;
  total_marks: number;
  obtained_marks: number;
  accuracy: number;
  created_at: string;
  test_name: string;
  duration_minutes: number;
  provider_id: string;
  provider_name: string;
  branch_id: string;
  branch_name: string;
  city: string;
  state: string;
}

function DashboardPage() {
  const navigate = useNavigate();

  const [studentName, setStudentName] = useState("Student");
  const [recentResults, setRecentResults] = useState<RecentResult[]>([]);
  const [resultsLoading, setResultsLoading] = useState(true);

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

  useEffect(() => {
    const fetchRecentResults = async () => {
      try {
        setResultsLoading(true);

        const response = await fetch(`${RESULT_API_URL}/results`, {
          credentials: "include",
        });

        if (!response.ok) {
          throw new Error("Failed to fetch results");
        }

        const results: RecentResult[] = await response.json();

        setRecentResults(results.slice(0, 3));
      } catch (error) {
        console.error("Failed to fetch recent results:", error);
        setRecentResults([]);
      } finally {
        setResultsLoading(false);
      }
    };

    fetchRecentResults();
  }, []);

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);

    return date.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  };

  const handleViewResult = (attemptId: string) => {
    navigate(`/tests/result/${attemptId}`);
  };

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
              <p>Your latest 3 completed test results.</p>
            </div>
          </div>

          {resultsLoading ? (
            <div className="section-empty">
              <div className="section-empty-icon">...</div>
              <p>Loading recent results...</p>
            </div>
          ) : recentResults.length === 0 ? (
            <div className="section-empty">
              <div className="section-empty-icon">—</div>
              <p>No test results available.</p>
            </div>
          ) : (
            <div className="dashboard-results-list">
              {recentResults.map((result) => (
                <div
                  className="dashboard-result-item"
                  key={result.result_id}
                >
                  <div className="dashboard-result-info">
                    <strong>{result.test_name}</strong>

                    <span>
                      {result.provider_name}
                      {result.branch_name
                        ? ` • ${result.branch_name}`
                        : ""}
                    </span>

                    <small>{formatDate(result.created_at)}</small>
                  </div>

                  <div className="dashboard-result-score">
                    <strong>
                      {result.obtained_marks}/{result.total_marks}
                    </strong>

                    <span>
                      {Number(result.accuracy).toFixed(1)}% accuracy
                    </span>
                  </div>

                  <button
                    type="button"
                    className="dashboard-view-result-button"
                    onClick={() => handleViewResult(result.attempt_id)}
                  >
                    View Result
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

export default DashboardPage;