import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import "../../styles/test-details.css";
import PaperSelection, { type PaperChoices } from "../../components/PaperSelection";
import { TEST_API_URL } from "../../services/api";

type TestSection = {
  position: number;
  subject: string;
  status: string;                // PAPER, or why there is no paper for this student
  message: string | null;
  question_count: number;
  papers?: unknown[] | null;     // a Global Test with paper choice: the student's eligible papers (chosen on this page)
};

type Test = {
  test_id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  total_questions: number | null;   // null: a Global Test with paper choice whose papers are not chosen yet
  total_marks: number | null;
  start_at: string | null;
  end_at: string | null;
  status: string;
  global_occurrence_id?: string | null;  // a unified Global Test: one exam with papers from your institutes
  sections?: TestSection[] | null;       // every configured subject, in order (unified Global Test only)

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
  const [choices, setChoices] = useState<PaperChoices | null>(null);

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

  // a Global Test with paper choice: the student chooses their papers here; the attempt service says when the choice is complete
  const choosesPapers = Boolean(test.sections?.length && test.sections.every((section) => Array.isArray(section.papers)));
  const canStart = !choosesPapers || Boolean(choices && (choices.complete || choices.locked));

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
          <strong>{test.total_questions ?? "Depends on your papers"}</strong>
        </div>

        <div className="test-detail-item">
          <span>Total Marks</span>
          <strong>{test.total_marks ?? "Depends on your papers"}</strong>
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

      {choosesPapers && testId && <PaperSelection testId={testId} onChange={setChoices} />}

      {!choosesPapers && test.sections && test.sections.length > 0 && (
        <div className="test-details-sections">
          <h3>Subjects</h3>
          <ul>
            {[...test.sections].sort((a, b) => a.position - b.position).map((section) => (
              <li key={section.subject} className={section.status === "PAPER" ? "available" : "unavailable"}>
                <strong>{section.subject}</strong>
                <span>{section.status === "PAPER" ? `${section.question_count} question(s)` : section.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {isAvailable && test.total_questions === 0 && (
        <p className="test-details-unavailable" role="status">
          No paper is available to you in this test, so it cannot be started. Each subject above explains why.
        </p>
      )}

      {isAvailable && test.total_questions !== 0 && (
        <div className="test-details-action">
          <button
            type="button"
            onClick={handleTakeTest}
            disabled={!canStart}
          >
            Take Test
          </button>
        </div>
      )}
    </section>
  );
}

export default TestDetailsPage;