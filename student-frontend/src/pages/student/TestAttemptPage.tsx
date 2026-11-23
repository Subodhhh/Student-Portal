import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ExamRulesScreen,
  FullscreenBlocker,
  IntegrityBadge
} from "../../components/TestIntegrityUI";

import {
  ATTEMPT_API_URL,
  PROFILE_API_URL,
  RESULT_API_URL,
  TEST_API_URL,
} from "../../hooks/api";

import "../../styles/TestAttemptPage.css";
import { MathText } from "../../components/MathText";
import type { IntegrityState } from "../../components/examIntegrity";
import { useExamIntegrity } from "../../components/examIntegrity";
import { isNoAttempt } from "../../hooks/attempts";

type QuestionStatus = "not-visited" | "not-answered" | "answered" | "review";

type ApiQuestion = {
  question_id: string;
  question_order: number;
  subject: string | null;
  question_text: string;
  question_type: string;
  option_a: string | null;
  option_b: string | null;
  option_c: string | null;
  option_d: string | null;
  marks: number;
  negative_marks: number;
  image_reference: string | null;
  question_image?: string | null;
  option_a_image?: string | null;
  option_b_image?: string | null;
  option_c_image?: string | null;
  option_d_image?: string | null;
};

type QuestionOption = { letter: string; text: string; image: string | null };

type TestResponse = {
  test_id: string;
  provider: { provider_id: string; name: string };
  branch: { branch_id: string; name: string; city: string | null; state: string | null };
  name: string;
  description: string | null;
  duration_minutes: number;
  total_questions: number;
  total_marks: number;
  start_at: string | null;
  end_at: string | null;
  status: string;
  global_occurrence_id?: string | null;
  sections?: TestSection[] | null;
  questions: ApiQuestion[];
};

type TestSection = {
  position: number;
  subject: string;
  status: string;
  message: string | null;
  question_count: number;
};

type AttemptResponse = {
  attempt_id: string;
  test_id: string;
  started_at: string;
  expires_at: string;
  status: "IN_PROGRESS" | "SUBMITTED" | "AUTO_SUBMITTED";
};

type AttemptAnswerResponse = {
  answer_id: string;
  attempt_id: string;
  question_id: string;
  selected_answer: string | null;
  answered_at: string;
  updated_at: string;
};

type ProfileResponse = {
  student_id: string;
  first_name: string;
  last_name: string;
};

type Question = {
  id: string;
  section: string;
  question: string;
  questionImage: string | null;
  options: QuestionOption[];
  answer: string | null;
  status: QuestionStatus;
  imageReference: string | null;
  marks: number;
  negativeMarks: number;
};

const SAVE_FAILED_MESSAGE =
  "Your last change could not be saved. Check your connection and select your answer again.";

function exitFullscreenQuietly() {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => { /* already left */ });
  }
}

function TestAttemptPage() {
  const navigate = useNavigate();
  const { testId } = useParams();
  const currentTestId = testId ?? "";

  const [test, setTest] = useState<TestResponse | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [student, setStudent] = useState<ProfileResponse | null>(null);

  const [selectedSection, setSelectedSection] = useState<string>("All Sections");
  const [currentQuestionId, setCurrentQuestionId] = useState<string | null>(null);

  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [attemptExpiresAt, setAttemptExpiresAt] = useState<number | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [saveWarning, setSaveWarning] = useState("");
  const [selectionRequired, setSelectionRequired] = useState<string[] | null>(null);

  // --- Exam Integrity State ---
  const [isFullscreen, setIsFullscreen] = useState(!!document.fullscreenElement);
  const [rulesAccepted, setRulesAccepted] = useState(false);
  const [autoSubmitted, setAutoSubmitted] = useState<IntegrityState | null>(null);
  const [reviewMessage, setReviewMessage] = useState("");
  const [reviewSent, setReviewSent] = useState<string>("");
  const [reviewError, setReviewError] = useState("");

  // Track fullscreen changes
  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const onAutoSubmitted = useCallback((s: IntegrityState) => {
    setAutoSubmitted(s);
    setSubmitted(true);
    exitFullscreenQuietly();
  }, []);

  const integrity = useExamIntegrity(ATTEMPT_API_URL, attemptId, rulesAccepted && !submitted, onAutoSubmitted);

  // --- Sections & Questions Logic ---
  const sections = useMemo(() => {
    if (test?.sections?.length) {
      return ["All Sections", ...[...test.sections].sort((a, b) => a.position - b.position).map((section) => section.subject)];
    }
    const subjects = Array.from(
      new Set(questions.map((q) => q.section).filter((s) => s.trim() !== ""))
    );
    return ["All Sections", ...subjects];
  }, [questions, test]);

  const emptySection = test?.sections?.find(
    (section) => section.subject === selectedSection && section.status !== "PAPER"
  );

  const questionStatusCounts = useMemo(() => {
    return {
      answered: questions.filter((q) => q.status === "answered").length,
      notAnswered: questions.filter((q) => q.status === "not-answered").length,
      notVisited: questions.filter((q) => q.status === "not-visited").length,
      review: questions.filter((q) => q.status === "review").length,
    };
  }, [questions]);

  const visibleQuestions = useMemo(() => {
    if (selectedSection === "All Sections") return questions;
    return questions.filter((q) => q.section === selectedSection);
  }, [questions, selectedSection]);

  const currentQuestion = questions.find((q) => q.id === currentQuestionId);
  const currentVisibleIndex = visibleQuestions.findIndex((q) => q.id === currentQuestionId);

  // --- Load Test & Attempt ---
  useEffect(() => {
    async function loadTestAndAttempt() {
      if (!rulesAccepted) return;

      if (!currentTestId) {
        setError("Invalid test.");
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError("");

        const attemptResponse = await fetch(`${ATTEMPT_API_URL}/attempts/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ test_id: currentTestId }),
        });

        let attemptData: AttemptResponse | null = null;
        let finished = false;

        if (!attemptResponse.ok) {
          const refusal = attemptResponse.status === 409 ? await attemptResponse.json().catch(() => null) : null;
          if (refusal?.detail?.code === "SELECTION_REQUIRED") {
            setSelectionRequired(refusal.detail.subjects ?? []);
            return;
          }
          const finishedResponse = await fetch(`${ATTEMPT_API_URL}/attempts/test/${currentTestId}`, { credentials: "include" });
          const found: unknown = finishedResponse.ok ? await finishedResponse.json() : null;
          const last = found && !isNoAttempt(found) ? (found as AttemptResponse) : null;
          if (!last || last.status === "IN_PROGRESS") throw new Error(`Failed to start attempt: ${attemptResponse.status}`);

          setAttemptId(last.attempt_id);
          const st = await fetch(`${ATTEMPT_API_URL}/attempts/${last.attempt_id}/integrity`, { credentials: "include" });
          const integrityState: IntegrityState | null = st.ok ? await st.json() : null;
          if (integrityState?.submit_reason === "WARNING_THRESHOLD") setAutoSubmitted(integrityState);
          finished = true;
        } else {
          attemptData = await attemptResponse.json();
          if (attemptData) {
            const expiresAt = new Date(attemptData.expires_at).getTime();
            setAttemptId(attemptData.attempt_id);
            setAttemptExpiresAt(expiresAt);
            setRemainingSeconds(Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)));
            finished = attemptData.status === "SUBMITTED" || attemptData.status === "AUTO_SUBMITTED";
          }
        }

        // An attempt that is already over only needs the submitted screen: no test or answers to load.
        if (finished || !attemptData) {
          setSubmitted(true);
          exitFullscreenQuietly();
          return;
        }

        const testResponse = await fetch(`${TEST_API_URL}/tests/${currentTestId}/start`, { credentials: "include" });
        if (!testResponse.ok) throw new Error(`Failed to load test: ${testResponse.status}`);

        const testData: TestResponse = await testResponse.json();
        setTest(testData);

        const mappedQuestions: Question[] = testData.questions.map((question) => {
          const options: QuestionOption[] = [
            ["A", question.option_a, question.option_a_image],
            ["B", question.option_b, question.option_b_image],
            ["C", question.option_c, question.option_c_image],
            ["D", question.option_d, question.option_d_image],
          ]
            .map(([letter, text, image]) => ({ letter: letter as string, text: text ?? "", image: image ?? null }))
            .filter((option) => Boolean(option.text) || Boolean(option.image));

          return {
            id: question.question_id,
            section: question.subject?.trim() || "Uncategorized",
            question: question.question_text,
            questionImage: question.question_image ?? null,
            options,
            answer: null,
            status: "not-visited",
            imageReference: question.image_reference,
            marks: question.marks,
            negativeMarks: question.negative_marks,
          };
        });

        setQuestions(mappedQuestions);
        if (mappedQuestions.length > 0) setCurrentQuestionId(mappedQuestions[0].id);

        try {
          const profileResponse = await fetch(`${PROFILE_API_URL}/profile`, { credentials: "include" });
          if (profileResponse.ok) {
            const profileData: ProfileResponse = await profileResponse.json();
            setStudent(profileData);
          }
        } catch (profileError) {
          console.error("Failed to load student profile:", profileError);
        }

        const answersResponse = await fetch(`${ATTEMPT_API_URL}/attempts/${attemptData.attempt_id}/answers`, { credentials: "include" });
        if (!answersResponse.ok) throw new Error(`Failed to load saved answers: ${answersResponse.status}`);

        const savedAnswers: AttemptAnswerResponse[] = await answersResponse.json();

        setQuestions((prev) =>
          prev.map((question) => {
            const savedAnswer = savedAnswers.find((a) => a.question_id === question.id);
            if (!savedAnswer || !savedAnswer.selected_answer) return question;
            const selectedOption = question.options.find((o) => o.letter === savedAnswer.selected_answer);
            if (!selectedOption) return question;
            return { ...question, answer: selectedOption.letter, status: "answered" };
          })
        );
      } catch (loadError) {
        console.error("Failed to load test attempt:", loadError);
        setError("Unable to load this test.");
      } finally {
        setLoading(false);
      }
    }

    loadTestAndAttempt();
  }, [currentTestId, rulesAccepted]);

  // Mark current question as visited
  useEffect(() => {
    if (!currentQuestionId || submitted) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuestions((prev) =>
      prev.map((q) => (q.id === currentQuestionId && q.status === "not-visited" ? { ...q, status: "not-answered" } : q))
    );
  }, [currentQuestionId, submitted]);

  // Server-controlled timer
  useEffect(() => {
    if (attemptExpiresAt === null || submitted) return;
    const expiresAt = attemptExpiresAt;
    let checking = false;

    async function updateTimer() {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setRemainingSeconds(remaining);

      // At zero, ask the server whether it closed the attempt. One check at a time.
      if (remaining > 0 || !attemptId || checking) return;
      checking = true;

      try {
        const response = await fetch(`${ATTEMPT_API_URL}/attempts/${attemptId}`, { credentials: "include" });
        if (!response.ok) return;
        const attempt: AttemptResponse = await response.json();
        if (attempt.status === "AUTO_SUBMITTED" || attempt.status === "SUBMITTED") {
          setSubmitted(true);
          exitFullscreenQuietly();
        }
      } catch (timerError) {
        console.error("Failed to verify attempt expiry:", timerError);
      } finally {
        checking = false;
      }
    }

    updateTimer();
    const timer = window.setInterval(updateTimer, 1000);
    return () => window.clearInterval(timer);
  }, [attemptExpiresAt, attemptId, submitted]);

  function formatTime(seconds: number) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remaining = seconds % 60;
    return [hours, minutes, remaining].map((v) => String(v).padStart(2, "0")).join(":");
  }

  function handleSectionChange(section: string) {
    setSelectedSection(section);
    const sectionQuestions = section === "All Sections" ? questions : questions.filter((q) => q.section === section);
    if (sectionQuestions.length > 0) setCurrentQuestionId(sectionQuestions[0].id);
  }

  async function handleAnswerSelect(answer: string) {
    if (!currentQuestionId || !attemptId || submitted) return;
    const currentQuestion = questions.find((q) => q.id === currentQuestionId);
    if (!currentQuestion) return;
    if (!currentQuestion.options.some((o) => o.letter === answer)) return;

    setQuestions((prev) =>
      prev.map((q) => (q.id === currentQuestionId ? { ...q, answer, status: "answered" } : q))
    );

    try {
      const response = await fetch(`${ATTEMPT_API_URL}/attempts/${attemptId}/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ question_id: currentQuestionId, selected_answer: answer }),
      });
      if (!response.ok) {
        if (response.status === 400) setSubmitted(true);
        throw new Error(`Failed to save answer: ${response.status}`);
      }
      setSaveWarning("");
    } catch (saveError) {
      console.error("Failed to save answer:", saveError);
      setSaveWarning(SAVE_FAILED_MESSAGE);
    }
  }

  async function handleClearResponse() {
    if (!currentQuestionId || !attemptId || submitted) return;

    setQuestions((prev) =>
      prev.map((q) => (q.id === currentQuestionId ? { ...q, answer: null, status: "not-answered" } : q))
    );

    try {
      const response = await fetch(`${ATTEMPT_API_URL}/attempts/${attemptId}/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ question_id: currentQuestionId, selected_answer: null }),
      });
      if (!response.ok) {
        if (response.status === 400) setSubmitted(true);
        throw new Error(`Failed to clear answer: ${response.status}`);
      }
      setSaveWarning("");
    } catch (clearError) {
      console.error("Failed to clear answer:", clearError);
      setSaveWarning(SAVE_FAILED_MESSAGE);
    }
  }

  function handleMarkForReview() {
    if (!currentQuestionId || submitted) return;
    setQuestions((prev) => prev.map((q) => (q.id === currentQuestionId ? { ...q, status: "review" } : q)));
    moveToNextQuestion();
  }

  function moveToNextQuestion() {
    if (currentVisibleIndex >= 0 && currentVisibleIndex < visibleQuestions.length - 1) {
      setCurrentQuestionId(visibleQuestions[currentVisibleIndex + 1].id);
    }
  }

  function moveToPreviousQuestion() {
    if (currentVisibleIndex > 0) setCurrentQuestionId(visibleQuestions[currentVisibleIndex - 1].id);
  }

  function handleSaveAndNext() {
    moveToNextQuestion();
  }

  async function handleSubmit() {
    if (!attemptId || submitted || submitting) return;

    // Avoid counting the native confirmation dialog as a warning
    const confirmed = integrity.withoutWarnings(() => window.confirm("Are you sure you want to submit the test?"));
    if (!confirmed) return;

    setSubmitting(true);
    setSubmitError("");

    try {
      const response = await fetch(`${ATTEMPT_API_URL}/attempts/${attemptId}/submit`, { method: "POST", credentials: "include" });

      // 400/409 means the attempt is already closed, e.g. an earlier try submitted it
      // but evaluation failed. Carry on to evaluation instead of failing the retry.
      if (!response.ok && response.status !== 400 && response.status !== 409) {
        throw new Error(`Failed to submit attempt: ${response.status}`);
      }

      const resultResponse = await fetch(`${RESULT_API_URL}/results/attempts/${attemptId}/evaluate`, { method: "POST", credentials: "include" });
      if (!resultResponse.ok) throw new Error(`Failed to evaluate result: ${resultResponse.status}`);

      await resultResponse.json();

      // Leave fullscreen without it being recorded as a warning
      integrity.withoutWarnings(() => exitFullscreenQuietly());
      navigate(`/tests/result/${attemptId}`);
    } catch (submitFailure) {
      console.error("Failed to submit test:", submitFailure);
      setSubmitError("The test could not be submitted or evaluated. Your answers are saved. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReviewRequest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!autoSubmitted) return;
    setReviewError("");

    try {
      const r = await fetch(`${ATTEMPT_API_URL}/attempts/${autoSubmitted.attempt_id}/review`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: reviewMessage }),
      });
      if (r.ok) {
        setReviewSent("sent");
      } else {
        setReviewSent("");
        setReviewError("The review request could not be sent. Please try again.");
      }
    } catch (reviewFailure) {
      console.error("Failed to send review request:", reviewFailure);
      setReviewSent("");
      setReviewError("The review request could not be sent. Please try again.");
    }
  }

  // --- Render Conditional Screens ---

  // 1. Rules Screen
  if (!rulesAccepted) {
    return <ExamRulesScreen onAccept={() => setRulesAccepted(true)} />;
  }

  // 2. Auto Submitted Screen
  if (autoSubmitted) {
    const review = autoSubmitted.review;
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Test submitted automatically</h2>
          <p role="status">
            Your test was submitted automatically because {autoSubmitted.warnings} warnings were recorded
            (leaving the test tab or window, leaving fullscreen, copying, pasting, taking a screenshot,
            using the browser back button, or refreshing). Your saved answers were submitted and are
            evaluated as usual. The submission is under review: this is a record of browser activity,
            not a finding of cheating.
          </p>
          {review || reviewSent ? (
            <p className="test-attempt-review-status">
              Review requested{review ? ` (${review.status === "OPEN" ? "waiting for your institute" : review.status === "RETAKE_AUTHORIZED" ? "a retake was authorized" : "the submission was upheld"})` : ""}.
              {review?.decision_reason ? ` Reason: ${review.decision_reason}` : ""}
            </p>
          ) : (
            <form className="test-attempt-review-form" onSubmit={handleReviewRequest}>
              <label htmlFor="review-message">If you believe this was unjustified, tell your institute what happened:</label>
              <textarea id="review-message" value={reviewMessage} minLength={10} maxLength={2000} required
                        onChange={(event) => setReviewMessage(event.target.value)} />
              <button type="submit" disabled={reviewMessage.trim().length < 10}>Request a review</button>
              {reviewError && <p className="test-attempt-error" role="alert">{reviewError}</p>}
            </form>
          )}
          <button type="button" onClick={() => navigate("/results")}>Go to Results</button>
        </div>
      </section>
    );
  }

  // 3. Loading Screen
  if (loading) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Loading Test...</h2>
          <p>Please wait while we load the test and questions.</p>
        </div>
      </section>
    );
  }

  // 4. Selection Required
  if (selectionRequired) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Choose Your Papers</h2>
          <p>Choose one paper for each of these subjects before starting: {selectionRequired.join(", ")}.</p>
          <button type="button" onClick={() => navigate(`/tests/${currentTestId}`)}>Choose Papers</button>
        </div>
      </section>
    );
  }

  // 5. Submitted Screen (before the error screen, so a finished attempt never shows a load error)
  if (submitted) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <div className="test-attempt-submitted-icon">✓</div>
          <h2>Test Submitted</h2>
          <p>Your test has been submitted successfully. Your result will be available after evaluation.</p>
          <button type="button" onClick={() => navigate("/results")}>Go to Results</button>
        </div>
      </section>
    );
  }

  // 6. Error Screen
  if (error || !test) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Unable to Load Test</h2>
          <p>{error || "Test not found."}</p>
          <button type="button" onClick={() => navigate("/tests")}>Back to Tests</button>
        </div>
      </section>
    );
  }

  // 7. No Questions
  if (!currentQuestion) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>No Questions Available</h2>
          <p>Questions for this test are not available yet.</p>
          <button type="button" onClick={() => navigate("/tests")}>Back to Tests</button>
        </div>
      </section>
    );
  }

  const studentName = student ? `${student.first_name} ${student.last_name}`.trim() : "Student";

  // --- Main Test UI ---
  return (
    <section className="test-attempt-page">

      {/* Modularized Integrity UI */}
      <FullscreenBlocker isFullscreen={isFullscreen} />
      <IntegrityBadge integrity={integrity.state} />

      <header className="test-attempt-header">
        <div className="test-attempt-header-left">
          <span className="test-attempt-provider">
            {test.global_occurrence_id ? "Global Test" : test.provider.name}
          </span>
          <h1>{test.name}</h1>
          {!test.global_occurrence_id && <span>{test.branch.name}</span>}
        </div>

        <div className="test-attempt-header-right">
          <span className="test-attempt-timer-label">Time Remaining</span>
          <strong className={remainingSeconds <= 300 ? "test-attempt-timer warning" : "test-attempt-timer"}>
            {formatTime(remainingSeconds)}
          </strong>
        </div>
      </header>

      <div className="test-attempt-sections">
        {sections.map((section) => (
          <button
            key={section}
            type="button"
            className={selectedSection === section ? "test-attempt-section active" : "test-attempt-section"}
            onClick={() => handleSectionChange(section)}
          >
            {section}
          </button>
        ))}
      </div>

      <div className="test-attempt-content">
        <main className="test-attempt-question-area">
          {emptySection ? (
            <div className="test-attempt-empty-section" role="status">
              <h2>{emptySection.subject}</h2>
              <p>{emptySection.message ?? "No paper is available to you for this subject."}</p>
            </div>
          ) : (
          <>
          <div className="test-attempt-question-header">
            <div>
              <span>Question {currentVisibleIndex + 1}</span>
              <strong>Question {currentVisibleIndex + 1} of {visibleQuestions.length}</strong>
            </div>
          </div>

          <div className="test-attempt-question">
            {currentQuestion.question && (
              <h2><MathText text={currentQuestion.question} /></h2>
            )}

            {currentQuestion.questionImage && (
              <div className="test-attempt-question-image">
                <img src={`${TEST_API_URL}/tests/${currentTestId}/images/${currentQuestion.questionImage}`} alt="Question picture" />
              </div>
            )}

            {currentQuestion.imageReference && (
              <div className="test-attempt-question-image">
                <img src={currentQuestion.imageReference} alt="Question diagram" />
              </div>
            )}

            <div className="test-attempt-options">
              {currentQuestion.options.map((option) => (
                <label
                  key={option.letter}
                  className={currentQuestion.answer === option.letter ? "test-attempt-option selected" : "test-attempt-option"}
                >
                  <input
                    type="radio"
                    name={`question-${currentQuestion.id}`}
                    value={option.letter}
                    checked={currentQuestion.answer === option.letter}
                    onChange={() => handleAnswerSelect(option.letter)}
                  />
                  <span className="option-radio" />
                  <span className="option-letter">{option.letter}.</span>

                  {option.image ? (
                    <img
                      className="test-attempt-option-image"
                      src={`${TEST_API_URL}/tests/${currentTestId}/images/${option.image}`}
                      alt={`Option ${option.letter} picture`}
                    />
                  ) : (
                    <MathText text={option.text} />
                  )}
                </label>
              ))}
            </div>
          </div>

          <div className="test-attempt-navigation">
            <button type="button" className="secondary-button" onClick={handleClearResponse}>Clear Response</button>
            <button type="button" className="secondary-button" onClick={handleMarkForReview}>Mark for Review</button>

            <div className="test-attempt-navigation-right">
              <button type="button" className="secondary-button" disabled={currentVisibleIndex <= 0} onClick={moveToPreviousQuestion}>
                Previous
              </button>
              <button type="button" className="primary-button" onClick={handleSaveAndNext} disabled={currentVisibleIndex < 0 || currentVisibleIndex === visibleQuestions.length - 1}>
                Save & Next
              </button>
            </div>
          </div>
          </>
          )}
        </main>

        <aside className="test-attempt-sidebar">
          <div className="student-mini-profile">
            <div>
              <strong>{studentName}</strong>
              <span>{student?.student_id ?? ""}</span>
            </div>
          </div>

          <div className="question-status-card">
            <div className="question-status-heading">
              <h2>Question Status</h2>
            </div>
            <div className="question-status-grid">
              <div className="question-status-item answered">
                <span>{questionStatusCounts.answered}</span>
                <strong>Answered</strong>
              </div>
              <div className="question-status-item not-answered">
                <span>{questionStatusCounts.notAnswered}</span>
                <strong>Not Answered</strong>
              </div>
              <div className="question-status-item not-visited">
                <span>{questionStatusCounts.notVisited}</span>
                <strong>Not Visited</strong>
              </div>
              <div className="question-status-item review">
                <span>{questionStatusCounts.review}</span>
                <strong>Marked for Review</strong>
              </div>
            </div>
          </div>

          <div className="question-palette-card">
            <div className="palette-heading">
              <h2>Question Palette</h2>
              <span>{visibleQuestions.length} Questions</span>
            </div>
            <div className="question-palette">
              {visibleQuestions.map((question, index) => (
                <button
                  key={question.id}
                  type="button"
                  className={`palette-question ${question.status} ${question.id === currentQuestion.id ? "current" : ""}`}
                  onClick={() => setCurrentQuestionId(question.id)}
                >
                  {index + 1}
                </button>
              ))}
            </div>
          </div>

          <div className="test-attempt-legend">
            <h2>Legend</h2>
            <div className="legend-item"><span className="legend-dot not-visited" /><span>Not Visited</span></div>
            <div className="legend-item"><span className="legend-dot not-answered" /><span>Not Answered</span></div>
            <div className="legend-item"><span className="legend-dot answered" /><span>Answered</span></div>
            <div className="legend-item"><span className="legend-dot review" /><span>Marked for Review</span></div>
          </div>

          {saveWarning && (
            <p className="test-attempt-save-warning" role="alert">{saveWarning}</p>
          )}

          {submitError && (
            <p className="test-attempt-submit-error" role="alert">{submitError}</p>
          )}

          <button type="button" className="submit-test-button" onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting..." : "Submit Test"}
          </button>
        </aside>
      </div>
    </section>
  );
}

export default TestAttemptPage;