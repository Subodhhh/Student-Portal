import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";

import { TEST_API_URL } from "../../services/api";
import { getResultDetails } from "../../services/resultService";

import "../../styles/test-result.css";

interface Result {
  result_id: string;
  attempt_id: string;
  test_id: string;
  total_questions: number;
  attempted_questions: number;
  correct_answers: number;
  incorrect_answers: number;
  unanswered_questions: number;
  total_marks: number;
  obtained_marks: number;
  accuracy: number;
}

interface SubjectPerformance {
  subject: string;
  total_questions: number;
  attempted_questions: number;
  correct_answers: number;
  incorrect_answers: number;
  unanswered_questions: number;
  total_marks: number;
  obtained_marks: number;
  accuracy: number;
  status?: string | null;    // unified Global Test: PAPER, or why there was no paper for this subject
  message?: string | null;
}

interface QuestionReview {
  question_id: string;
  question_order: number;
  subject: string | null;
  question_text: string;
  option_a: string | null;
  option_b: string | null;
  option_c: string | null;
  option_d: string | null;
  selected_answer: string | null;
  correct_answer: string;
  status: "CORRECT" | "INCORRECT" | "UNANSWERED";
  marks_obtained: number;
  marks: number;
  negative_marks: number;
  image_reference: string | null;
}

interface ResultDetails {
  result: Result;
  subjects: SubjectPerformance[];
  questions: QuestionReview[];
  review_available?: boolean;
  review_available_at?: string | null;
}

interface Test {
  name: string;
  provider?: {
    name: string;
  };
  branch?: {
    name: string;
    city: string | null;
    state: string | null;
  };
  duration_minutes?: number;
}

export default function TestResultPage() {
  const { attemptId } = useParams();
  const navigate = useNavigate();

  const [result, setResult] = useState<Result | null>(null);
  const [test, setTest] = useState<Test | null>(null);
  const [subjects, setSubjects] = useState<SubjectPerformance[]>([]);
  const [questions, setQuestions] = useState<QuestionReview[]>([]);
  const [selectedSubject, setSelectedSubject] = useState<string | null>(
    null
  );
  const [loading, setLoading] = useState(Boolean(attemptId));
  const [reviewAvailable, setReviewAvailable] = useState(true);
  const [reviewAvailableAt, setReviewAvailableAt] = useState<string | null>(null);

  const questionReviewRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    async function loadResult() {
      try {
        if (!attemptId) {
          throw new Error("Attempt ID is missing");
        }

        const resultDetails: ResultDetails =
          await getResultDetails(attemptId);

        setResult(resultDetails.result);
        setSubjects(resultDetails.subjects);
        setQuestions(resultDetails.questions);
        setReviewAvailable(resultDetails.review_available !== false);
        setReviewAvailableAt(resultDetails.review_available_at ?? null);

        // /details resolves for any test the student is assigned to, also after its window closed.
        const testResponse = await fetch(
          `${TEST_API_URL}/tests/${resultDetails.result.test_id}/details`,
          {
            credentials: "include",
          }
        );

        if (testResponse.ok) {
          const testData: Test = await testResponse.json();
          setTest(testData);
        }
      } catch (error) {
        console.error("Failed to load test performance:", error);
      } finally {
        setLoading(false);
      }
    }

    if (attemptId) {
      loadResult();
    }
  }, [attemptId]);

  useEffect(() => {
    if (!selectedSubject || !questionReviewRef.current) {
      return;
    }

    questionReviewRef.current.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [selectedSubject]);

  const percentage = useMemo(() => {
    if (!result || result.total_marks === 0) {
      return 0;
    }

    return Math.round(
      (result.obtained_marks / result.total_marks) * 100
    );
  }, [result]);

  const selectedSubjectQuestions = useMemo(() => {
    if (!selectedSubject) {
      return [];
    }

    return questions.filter(
      (question) => question.subject === selectedSubject
    );
  }, [questions, selectedSubject]);

  const handleSubjectReview = (subject: string) => {
    if (selectedSubject === subject) {
      setSelectedSubject(null);
      return;
    }

    setSelectedSubject(subject);
  };

  if (loading) {
    return (
      <section className="test-performance-page">
        <div className="performance-loading">
          <div className="performance-loading-spinner" />
          <h2>Loading Performance...</h2>
          <p>
            Please wait while we prepare your test analysis.
          </p>
        </div>
      </section>
    );
  }

  if (!result) {
    return (
      <section className="test-performance-page">
        <div className="performance-empty">
          <div className="performance-empty-icon">!</div>
          <h2>Performance Not Found</h2>
          <p>
            We couldn't find the result for this test.
          </p>

          <button
            type="button"
            onClick={() => navigate("/results")}
          >
            Back to Results
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="test-performance-page">
      <div className="test-performance-container">

        {/* Header */}
        <div className="performance-header">
          <div>
            <span className="performance-eyebrow">
              TEST PERFORMANCE
            </span>

            <h1>{test?.name || "Test Performance"}</h1>

            <div className="performance-context">
              <span>
                {test?.provider?.name || "Provider"}
              </span>

              {test?.branch?.name && (
                <>
                  <span className="context-dot">•</span>
                  <span>{test.branch.name}</span>
                </>
              )}
            </div>
          </div>

          <button
            type="button"
            className="performance-back-button"
            onClick={() => navigate("/results")}
          >
            ← Back to Results
          </button>
        </div>

        {/* Hero */}
        <div className="performance-hero">
          <div className="performance-hero-content">
            <div>
              <span className="hero-label">FINAL SCORE</span>

              <div className="hero-score">
                {result.obtained_marks}
                <span>/ {result.total_marks}</span>
              </div>

              <p className="hero-subtitle">
                Your final score for this test
              </p>
            </div>

            <div
              className="score-ring"
              style={{
                background: `conic-gradient(
                  #2563eb ${percentage * 3.6}deg,
                  #e2e8f0 ${percentage * 3.6}deg
                )`,
              }}
            >
              <div className="score-ring-inner">
                <strong>{percentage}%</strong>
                <span>Score</span>
              </div>
            </div>

            <div className="completion-badge">
              <span>✓</span>
              Completed
            </div>
          </div>
        </div>

        {/* Summary Cards */}
        <div className="performance-summary-grid">

          <div className="performance-stat-card correct">
            <div className="stat-icon">✓</div>
            <div>
              <span>Correct</span>
              <strong>{result.correct_answers}</strong>
            </div>
          </div>

          <div className="performance-stat-card incorrect">
            <div className="stat-icon">×</div>
            <div>
              <span>Incorrect</span>
              <strong>{result.incorrect_answers}</strong>
            </div>
          </div>

          <div className="performance-stat-card unanswered">
            <div className="stat-icon">—</div>
            <div>
              <span>Unanswered</span>
              <strong>{result.unanswered_questions}</strong>
            </div>
          </div>

          <div className="performance-stat-card accuracy">
            <div className="stat-icon">%</div>
            <div>
              <span>Accuracy</span>
              <strong>{result.accuracy}%</strong>
            </div>
          </div>

        </div>

        {/* Attempt Overview */}
        <div className="performance-card attempt-overview-card">
          <div className="performance-card-header">
            <div>
              <span className="card-eyebrow">OVERVIEW</span>
              <h2>Attempt Summary</h2>
            </div>
          </div>

          <div className="overview-grid">

            <div className="overview-item">
              <span>Total Questions</span>
              <strong>{result.total_questions}</strong>
            </div>

            <div className="overview-item">
              <span>Attempted</span>
              <strong>{result.attempted_questions}</strong>
            </div>

            <div className="overview-item">
              <span>Maximum Marks</span>
              <strong>{result.total_marks}</strong>
            </div>

            <div className="overview-item">
              <span>Marks Obtained</span>
              <strong>{result.obtained_marks}</strong>
            </div>

          </div>
        </div>

        {/* Subject Performance */}
        {subjects.length > 0 && (
          <div className="performance-card subject-performance-card">
            <div className="performance-card-header">
              <div>
                <span className="card-eyebrow">
                  SUBJECT ANALYSIS
                </span>
                <h2>Subject-wise Performance</h2>
              </div>

              <span className="question-total">
                {subjects.length} Subject
                {subjects.length !== 1 ? "s" : ""}
              </span>
            </div>

            <div className="subject-performance-list">
              {subjects.map((subject) => {
                const isSelected =
                  selectedSubject === subject.subject;

                return (
                  <div
                    className={`subject-performance-item ${
                      isSelected ? "selected" : ""
                    }`}
                    key={subject.subject}
                  >
                    <div className="subject-performance-main">

                      <div className="subject-performance-title">
                        <h3>{subject.subject}</h3>

                        <span>
                          {subject.status && subject.status !== "PAPER"
                            ? `Unavailable: ${subject.message ?? "no paper for you"}`
                            : `${subject.attempted_questions} of ${subject.total_questions} attempted`}
                        </span>
                      </div>

                      <div className="subject-performance-score">
                        <strong>
                          {subject.obtained_marks}
                        </strong>

                        <span>
                          / {subject.total_marks}
                        </span>
                      </div>

                    </div>

                    <div className="subject-performance-stats">
                      <span className="subject-stat correct">
                        ✓ {subject.correct_answers} Correct
                      </span>

                      <span className="subject-stat incorrect">
                        × {subject.incorrect_answers} Incorrect
                      </span>

                      <span className="subject-stat unanswered">
                        — {subject.unanswered_questions} Unanswered
                      </span>

                      <span className="subject-stat accuracy">
                        {subject.accuracy}% Accuracy
                      </span>
                    </div>

                    <div className="subject-performance-footer">
                      <div className="subject-progress">
                        <div
                          className="subject-progress-fill"
                          style={{
                            width: `${Math.min(
                              Math.max(
                                (subject.obtained_marks /
                                  subject.total_marks) *
                                  100,
                                0
                              ),
                              100
                            )}%`,
                          }}
                        />
                      </div>

                      {reviewAvailable && (
                        <button
                          type="button"
                          className="review-subject-button"
                          onClick={() =>
                            handleSubjectReview(subject.subject)
                          }
                        >
                          {isSelected
                            ? "Hide Questions"
                            : "Review Questions"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {!reviewAvailable && (
          <div className="performance-card">
            <div className="performance-card-header">
              <div>
                <span className="card-eyebrow">ANSWER REVIEW</span>
                <h2>Opens after the test window closes</h2>
                <p>
                  Correct answers are shown once no other student can start this test
                  {reviewAvailableAt
                    ? ` (${new Date(reviewAvailableAt).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}).`
                    : "."}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Question Review */}
        {reviewAvailable && selectedSubject && (
          <div
            className="performance-card question-review-card"
            ref={questionReviewRef}
          >
            <div className="performance-card-header">
              <div>
                <span className="card-eyebrow">
                  QUESTION REVIEW
                </span>

                <h2>{selectedSubject}</h2>
              </div>

              <span className="question-total">
                {selectedSubjectQuestions.length} Questions
              </span>
            </div>

            <div className="question-review-list">
              {selectedSubjectQuestions.map((question) => (
                <div
                  className={`question-review-item ${question.status.toLowerCase()}`}
                  key={question.question_id}
                >
                  <div className="question-review-header">
                    <span className="question-number">
                      Question {question.question_order}
                    </span>

                    <span
                      className={`question-status ${question.status.toLowerCase()}`}
                    >
                      {question.status === "CORRECT" &&
                        "✓ Correct"}

                      {question.status === "INCORRECT" &&
                        "× Incorrect"}

                      {question.status === "UNANSWERED" &&
                        "— Unanswered"}
                    </span>
                  </div>

                  <div className="question-review-content">

                    <p className="question-text">
                      {question.question_text}
                    </p>

                    {question.image_reference && (
                      <img
                        src={question.image_reference}
                        alt={`Question ${question.question_order}`}
                        className="question-image"
                      />
                    )}

                    <div className="question-review-answer-grid">

                      <div className="question-answer-box">
                        <span>Your Answer</span>

                        <strong>
                          {question.selected_answer
                            ? `${question.selected_answer}${
                                question[
                                  `option_${question.selected_answer.toLowerCase()}` as
                                    | "option_a"
                                    | "option_b"
                                    | "option_c"
                                    | "option_d"
                                ]
                                  ? ` — ${
                                      question[
                                        `option_${question.selected_answer.toLowerCase()}` as
                                          | "option_a"
                                          | "option_b"
                                          | "option_c"
                                          | "option_d"
                                      ]
                                    }`
                                  : ""
                              }`
                            : "Not Answered"}
                        </strong>
                      </div>

                      <div className="question-answer-box">
                        <span>Correct Answer</span>

                        <strong>
                          {question.correct_answer}
                          {question[
                            `option_${question.correct_answer.toLowerCase()}` as
                              | "option_a"
                              | "option_b"
                              | "option_c"
                              | "option_d"
                          ]
                            ? ` — ${
                                question[
                                  `option_${question.correct_answer.toLowerCase()}` as
                                    | "option_a"
                                    | "option_b"
                                    | "option_c"
                                    | "option_d"
                                ]
                              }`
                            : ""}
                        </strong>
                      </div>

                      <div className="question-answer-box">
                        <span>Marks</span>

                        <strong>
                          {question.marks_obtained > 0
                            ? `+${question.marks_obtained}`
                            : question.marks_obtained}
                        </strong>
                      </div>

                    </div>

                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="performance-actions">
          <button
            type="button"
            className="secondary-action"
            onClick={() => navigate("/results")}
          >
            ← All Results
          </button>
        </div>

      </div>
    </section>
  );
}