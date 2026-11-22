import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PROFILE_API_URL, RESULT_API_URL } from "../../hooks/api";
import "../../styles/DashboardPage.css";

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

        if (!response.ok) return;

        const student = await response.json();
        const fullName = [student.first_name, student.last_name]
          .filter(Boolean)
          .join(" ");

        if (fullName) setStudentName(fullName);
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

        if (!response.ok) throw new Error("Failed to fetch results");

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
    return new Date(dateString).toLocaleDateString("en-IN", {
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
      {/* Welcome Banner */}
      <section className="dashboard-welcome">
        <div className="welcome-text">
          <h1>Welcome back, {studentName}</h1>
          <p>Here is your latest test activity and performance overview.</p>
        </div>
      </section>

      <div className="dashboard-section-grid">
        {/* Upcoming Tests Section */}
        <section className="dashboard-card">
          <div className="card-header">
            <div>
              <h3>Upcoming Tests</h3>
              <p>Your scheduled exams will appear here.</p>
            </div>
          </div>

          <div className="dashboard-empty-state">
            <div className="empty-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5" />
              </svg>
            </div>
            <h4>No upcoming tests</h4>
            <p>You don't have any exams scheduled at the moment.</p>
          </div>
        </section>

        {/* Recent Results Section */}
        <section className="dashboard-card">
          <div className="card-header">
            <div>
              <h3>Recent Results</h3>
              <p>Your latest 3 completed test results.</p>
            </div>
          </div>

          {resultsLoading ? (
            <div className="dashboard-empty-state">
              <div className="loading-spinner" />
              <p>Loading your results...</p>
            </div>
          ) : recentResults.length === 0 ? (
            <div className="dashboard-empty-state">
              <div className="empty-icon-wrapper">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
              </div>
              <h4>No results yet</h4>
              <p>Complete a test to see your performance here.</p>
            </div>
          ) : (
            <div className="dashboard-results-list">
              {recentResults.map((result) => (
                <div className="result-list-item" key={result.result_id}>
                  <div className="result-info">
                    <h4>{result.test_name}</h4>
                    <span className="result-meta">
                      {result.provider_name}
                      {result.branch_name ? ` • ${result.branch_name}` : ""}
                    </span>
                    <span className="result-date">{formatDate(result.created_at)}</span>
                  </div>

                  <div className="result-score">
                    <div className="score-value">
                      {result.obtained_marks} <span>/ {result.total_marks}</span>
                    </div>
                    <div className="score-accuracy">
                      {Number(result.accuracy).toFixed(1)}% accuracy
                    </div>
                  </div>

                  <button
                    type="button"
                    className="result-view-btn"
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