import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import "../../styles/test-details.css";
import { TEST_API_URL } from "../../services/api";

type Test = {
  test_id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  total_questions: number;
  total_marks: number;
  start_at: string | null;
  end_at: string | null;
  status: string;
  global_occurrence_id?: string | null;  // a unified Global Test: one exam with papers from your institutes

  provider: {
    provider_id: string;
    name: string;
  };

  branch: {
    branch_id: string;
    name: string;
    city: string | null;
    state: string | null;
  };
};

function formatDate(date: string | null) {
  if (!date) {
    return "Date not specified";
  }

  return new Date(date).toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function formatTime(date: string | null) {
  if (!date) {
    return "";
  }

  return new Date(date).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function TestDetailsPage() {
  const navigate = useNavigate();
  const { testId } = useParams();

  const [test, setTest] = useState<Test | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadTest() {
      if (!testId) {
        setError("Test not found.");
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError("");

        const response = await fetch(
          `${TEST_API_URL}/tests/${testId}/details`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          throw new Error(
            `Failed to load test: ${response.status}`
          );
        }

        const data: Test = await response.json();

        setTest(data);
      } catch (loadError) {
        console.error(
          "Failed to load test details:",
          loadError
        );

        setError("Unable to load test details.");
      } finally {
        setLoading(false);
      }
    }

    loadTest();
  }, [testId]);

  function handleTakeTest() {
    if (!testId) {
      return;
    }

    navigate(`/tests/${testId}/attempt`);
  }

  if (loading) {
    return (
      <section className="test-details-page">
        <button
          className="test-details-back"
          type="button"
          onClick={() => navigate("/scheduled-tests")}
        >
          ← Back to Scheduled Tests
        </button>

        <div className="test-details-description">
          <p>Loading test details...</p>
        </div>
      </section>
    );
  }

  if (error || !test) {
    return (
      <section className="test-details-page">
        <button
          className="test-details-back"
          type="button"
          onClick={() => navigate("/scheduled-tests")}
        >
          ← Back to Scheduled Tests
        </button>

        <div className="test-details-description">
          <p>{error || "Test not found."}</p>
        </div>
      </section>
    );
  }

  const isAvailable =
    test.status === "ACTIVE" &&
    test.start_at !== null &&
    test.end_at !== null &&
    new Date() >= new Date(test.start_at) &&
    new Date() <= new Date(test.end_at);

  return (
    <section className="test-details-page">
      <button
        className="test-details-back"
        type="button"
        onClick={() => navigate("/scheduled-tests")}
      >
        ← Back to Scheduled Tests
      </button>

      <div className="test-details-header">
        <div>
          <div className="test-details-provider">
            <span className="test-details-provider-icon">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                aria-hidden="true"
              >
                <path d="M3 21h18" />
                <path d="M5 21V5l7-3 7 3v16" />
                <path d="M9 9h1" />
                <path d="M14 9h1" />
                <path d="M9 13h1" />
                <path d="M14 13h1" />
                <path d="M10 21v-4h4v4" />
              </svg>
            </span>

            {test.global_occurrence_id ? (
              <span>Global Test</span>
            ) : (
              <>
                <span>{test.provider.name}</span>

                {test.branch?.name && (
                  <span> · {test.branch.name}</span>
                )}
              </>
            )}
          </div>

          <h2>{test.name}</h2>

          <p>
            {test.description ||
              "Test details provided by your provider."}
          </p>
        </div>

        <span
          className={`test-details-status ${
            isAvailable
              ? "test-details-status-available"
              : "test-details-status-upcoming"
          }`}
        >
          {isAvailable ? "Available" : "Upcoming"}
        </span>
      </div>

      <div className="test-details-description">
        <p>
          {test.description ||
            "No additional description has been provided for this test."}
        </p>
      </div>

      <div className="test-details-grid">
        <div className="test-detail-item">
          <span>Provider</span>
          <strong>{test.global_occurrence_id ? "Global Test (papers from your institutes)" : test.provider.name}</strong>
        </div>

        <div className="test-detail-item">
          <span>Questions</span>
          <strong>{test.total_questions}</strong>
        </div>

        <div className="test-detail-item">
          <span>Total Marks</span>
          <strong>{test.total_marks}</strong>
        </div>

        <div className="test-detail-item">
          <span>Duration</span>
          <strong>
            {test.duration_minutes} Minutes
          </strong>
        </div>
      </div>

      <div className="test-details-section">
        <h3>Test Schedule</h3>

        <div className="test-schedule-info">
          <div>
            <span>Date</span>
            <strong>
              {formatDate(test.start_at)}
            </strong>
          </div>

          <div>
            <span>Test Window</span>
            <strong>
              {formatTime(test.start_at)} -{" "}
              {formatTime(test.end_at)}
            </strong>
          </div>
        </div>
      </div>

      {isAvailable && (
        <div className="test-details-action">
          <button
            type="button"
            onClick={handleTakeTest}
          >
            Take Test
          </button>
        </div>
      )}
    </section>
  );
}

export default TestDetailsPage;