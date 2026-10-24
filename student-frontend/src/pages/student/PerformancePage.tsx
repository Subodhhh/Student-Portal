import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import "../../styles/performance.css";

import {
  ATTEMPT_API_URL,
  RESULT_API_URL,
  TEST_API_URL,
} from "../../services/api";

type SubjectPerformance = {
  subject: string;
  total_questions: number;
  attempted_questions: number;
  correct_answers: number;
  incorrect_answers: number;
  unanswered_questions: number;
  total_marks: number;
  obtained_marks: number;
  accuracy: number;
};

type ResultDetails = {
  result: {
    attempt_id: string;
    total_questions: number;
    attempted_questions: number;
    correct_answers: number;
    incorrect_answers: number;
    unanswered_questions: number;
    total_marks: number;
    obtained_marks: number;
    accuracy: number;
  };
  subjects: SubjectPerformance[];
  review_available: boolean;
  review_available_at: string | null;
};

type TestInfo = {
  name: string;
  provider: { name: string };
  branch: { name: string };
};

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "pending"; message: string }
  | { kind: "ready"; details: ResultDetails; test: TestInfo | null };

async function fetchJson(url: string) {
  const response = await fetch(url, { credentials: "include" });
  return response;
}

function PerformancePage() {
  const navigate = useNavigate();
  const { testId } = useParams();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const attemptResponse = await fetchJson(
          `${ATTEMPT_API_URL}/attempts/test/${testId}`
        );

        if (attemptResponse.status === 404) {
          if (!cancelled) {
            setState({
              kind: "pending",
              message: "You have not attempted this test yet.",
            });
          }
          return;
        }

        if (!attemptResponse.ok) {
          throw new Error(`attempt ${attemptResponse.status}`);
        }

        const attempt = await attemptResponse.json();

        if (attempt.status === "IN_PROGRESS") {
          if (!cancelled) {
            setState({
              kind: "pending",
              message: "This test is still in progress. Your performance appears after you submit it.",
            });
          }
          return;
        }

        const [detailsResponse, testResponse] = await Promise.all([
          fetchJson(`${RESULT_API_URL}/results/attempts/${attempt.attempt_id}/details`),
          fetchJson(`${TEST_API_URL}/tests/${testId}/details`),
        ]);

        if (detailsResponse.status === 404) {
          if (!cancelled) {
            setState({
              kind: "pending",
              message: "Your attempt is being evaluated. Please check again in a minute.",
            });
          }
          return;
        }

        if (!detailsResponse.ok) {
          throw new Error(`result ${detailsResponse.status}`);
        }

        const details: ResultDetails = await detailsResponse.json();
        const test: TestInfo | null = testResponse.ok ? await testResponse.json() : null;

        if (!cancelled) {
          setState({ kind: "ready", details, test });
        }
      } catch (error) {
        console.error("Failed to load performance:", error);
        if (!cancelled) {
          setState({ kind: "error", message: "Unable to load your performance for this test." });
        }
      }
    }

    load();

    return () => {
      cancelled = true;
    };
  }, [testId]);

  function handleBackToResults() {
    navigate("/results");
  }

  function handleViewTest() {
    navigate(`/tests/${testId}`);
  }

  if (state.kind !== "ready") {
    return (
      <section className="performance-page">
        <div className="performance-footer-card">
          <div>
            <h3>
              {state.kind === "loading"
                ? "Loading performance…"
                : state.kind === "error"
                  ? "Something went wrong"
                  : "Performance not available yet"}
            </h3>
            {state.kind !== "loading" && <p>{state.message}</p>}
          </div>
          <button type="button" onClick={handleBackToResults}>
            View All Results
          </button>
        </div>
      </section>
    );
  }

  const { result, subjects, review_available, review_available_at } = state.details;
  const test = state.test;
  const percentage = result.total_marks > 0 ? (result.obtained_marks / result.total_marks) * 100 : 0;

  return (
    <section className="performance-page">
      <div className="performance-header">
        <div>
          {test && (
            <span className="performance-provider">
              {test.provider.name} · {test.branch.name}
            </span>
          )}

          <h2>{test ? test.name : "Test performance"}</h2>

          <p>Test Performance</p>
        </div>

        <div className="performance-header-actions">
          <button type="button" className="performance-secondary-button" onClick={handleViewTest}>
            Test Details
          </button>

          <button type="button" className="performance-primary-button" onClick={handleBackToResults}>
            Back to Results
          </button>
        </div>
      </div>

      <div className="performance-summary-card">
        <div className="performance-score">
          <span>Score</span>

          <strong>
            {result.obtained_marks}
            <small> / {result.total_marks}</small>
          </strong>

          <p>{percentage.toFixed(2)}%</p>
        </div>

        <div className="performance-summary-divider" />

        <div className="performance-summary-item">
          <span>Correct</span>
          <strong>{result.correct_answers}</strong>
          <small>out of {result.total_questions}</small>
        </div>

        <div className="performance-summary-item">
          <span>Wrong</span>
          <strong>{result.incorrect_answers}</strong>
          <small>answers</small>
        </div>

        <div className="performance-summary-item">
          <span>Unanswered</span>
          <strong>{result.unanswered_questions}</strong>
          <small>questions</small>
        </div>

        <div className="performance-summary-item">
          <span>Accuracy</span>
          <strong>{result.accuracy.toFixed(2)}%</strong>
          <small>attempted</small>
        </div>
      </div>

      <div className="performance-grid">
        <div className="performance-card">
          <div className="performance-card-header">
            <div>
              <h3>Section-wise Performance</h3>
              <p>Your performance across each section.</p>
            </div>
          </div>

          <div className="performance-table-wrapper">
            <table className="performance-table">
              <thead>
                <tr>
                  <th>Section</th>
                  <th>Questions</th>
                  <th>Correct</th>
                  <th>Wrong</th>
                  <th>Unanswered</th>
                  <th>Marks</th>
                  <th>Accuracy</th>
                </tr>
              </thead>

              <tbody>
                {subjects.map((section) => (
                  <tr key={section.subject}>
                    <td>
                      <strong>{section.subject}</strong>
                    </td>
                    <td>{section.total_questions}</td>
                    <td className="performance-correct">{section.correct_answers}</td>
                    <td className="performance-wrong">{section.incorrect_answers}</td>
                    <td className="performance-unanswered">{section.unanswered_questions}</td>
                    <td>
                      {section.obtained_marks} / {section.total_marks}
                    </td>
                    <td>{section.accuracy.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="performance-card performance-question-summary">
          <div className="performance-card-header">
            <div>
              <h3>Question Summary</h3>
              <p>Overall attempt breakdown.</p>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot correct" />
            <div>
              <strong>{result.correct_answers}</strong>
              <span>Correct Answers</span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot wrong" />
            <div>
              <strong>{result.incorrect_answers}</strong>
              <span>Wrong Answers</span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot unanswered" />
            <div>
              <strong>{result.unanswered_questions}</strong>
              <span>Unanswered</span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot attempted" />
            <div>
              <strong>{result.attempted_questions}</strong>
              <span>Total Attempted</span>
            </div>
          </div>
        </div>
      </div>

      <div className="performance-footer-card">
        <div>
          <h3>Test Completed</h3>
          <p>
            {review_available
              ? "Your test has been evaluated. Question-by-question answer review is available from the result page."
              : `Your test has been evaluated. Answer review opens after the test window closes${
                  review_available_at ? ` (${new Date(review_available_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })})` : ""
                }.`}
          </p>
        </div>

        <button type="button" onClick={() => navigate(`/tests/result/${result.attempt_id}`)}>
          View Result
        </button>
      </div>
    </section>
  );
}

export default PerformancePage;
