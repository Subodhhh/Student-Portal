import { useCallback, useEffect, useMemo, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";

import {
  ATTEMPT_API_URL,
  PROFILE_API_URL,
  RESULT_API_URL,
  TEST_API_URL,
} from "../../services/api";

import "../../styles/test-attempt.css";
import { enterFullscreen, useExamIntegrity, type IntegrityState } from "./examIntegrity";

type QuestionStatus =
  | "not-visited"
  | "not-answered"
  | "answered"
  | "review";

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
};

type TestResponse = {
  test_id: string;
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
  name: string;
  description: string | null;
  duration_minutes: number;
  total_questions: number;
  total_marks: number;
  start_at: string | null;
  end_at: string | null;
  status: string;
  global_occurrence_id?: string | null;  // a unified Global Test
  sections?: TestSection[] | null;       // every configured subject, in order (unified Global Test only)
  questions: ApiQuestion[];
};

type TestSection = {
  position: number;
  subject: string;
  status: string;                // PAPER, or why there is no paper for this student
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
  options: string[];
  answer: string | null;
  status: QuestionStatus;
  imageReference: string | null;
  marks: number;
  negativeMarks: number;
};

function TestAttemptPage() {
  const navigate = useNavigate();
  const { testId } = useParams();

  const currentTestId = testId ?? "";

  const [test, setTest] = useState<TestResponse | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [student, setStudent] = useState<ProfileResponse | null>(null);

  const [selectedSection, setSelectedSection] =
    useState<string>("All Sections");

  const [currentQuestionId, setCurrentQuestionId] =
    useState<string | null>(null);

  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [attemptExpiresAt, setAttemptExpiresAt] =
    useState<number | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState(0);

  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Exam integrity: the rules are accepted (and fullscreen offered) before the attempt starts or resumes; warnings are counted by the
  // server, which submits the attempt automatically at its threshold.
  const [rulesAccepted, setRulesAccepted] = useState(false);
  const [autoSubmitted, setAutoSubmitted] = useState<IntegrityState | null>(null);
  const [reviewMessage, setReviewMessage] = useState("");
  const [reviewSent, setReviewSent] = useState<string>("");
  const onAutoSubmitted = useCallback((s: IntegrityState) => {
    setAutoSubmitted(s);
    setSubmitted(true);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => { /* already left */ });
  }, []);
  const integrity = useExamIntegrity(ATTEMPT_API_URL, attemptId, rulesAccepted && !submitted, onAutoSubmitted);

  /*
   * Subjects come directly from the questions returned by the API.
   *
   * If DB has:
   * Physics
   * Mathematics
   * Chemistry
   *
   * those three appear.
   *
   * If DB has only:
   * Physics
   * Chemistry
   *
   * only those two appear.
   *
   * If DB has one subject, only that one appears.
   */
  const sections = useMemo(() => {
    // A unified Global Test shows every configured subject in the configured order, with or without a paper.
    if (test?.sections?.length) {
      return ["All Sections", ...[...test.sections].sort((a, b) => a.position - b.position).map((section) => section.subject)];
    }
    const subjects = Array.from(
      new Set(
        questions
          .map((question) => question.section)
          .filter((subject) => subject.trim() !== "")
      )
    );

    return ["All Sections", ...subjects];
  }, [questions, test]);

  // the selected subject has no paper for this student: its tab explains why instead of showing a question
  const emptySection = test?.sections?.find(
    (section) => section.subject === selectedSection && section.status !== "PAPER"
  );

  const questionStatusCounts = useMemo(() => {
   return {
    answered: questions.filter(
      (question) => question.status === "answered"
    ).length,

    notAnswered: questions.filter(
      (question) => question.status === "not-answered"
    ).length,

    notVisited: questions.filter(
      (question) => question.status === "not-visited"
    ).length,

    review: questions.filter(
      (question) => question.status === "review"
    ).length,
  };
}, [questions]);

  const visibleQuestions = useMemo(() => {
    if (selectedSection === "All Sections") {
      return questions;
    }

    return questions.filter(
      (question) => question.section === selectedSection
    );
  }, [questions, selectedSection]);

  const currentQuestion = questions.find(
    (question) => question.id === currentQuestionId
  );

  const currentVisibleIndex = visibleQuestions.findIndex(
    (question) => question.id === currentQuestionId
  );

  /*
   * Load test, questions, student profile and attempt.
   */
  useEffect(() => {
    async function loadTestAndAttempt() {
      if (!rulesAccepted) {
        return;                                   // nothing starts (no timer) before the student accepts the exam rules
      }

      if (!currentTestId) {
        setError("Invalid test.");
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError("");

        /*
         * Load test from M4.
         */
        const testResponse = await fetch(
          `${TEST_API_URL}/tests/${currentTestId}/start`,
          {
            credentials: "include",
          }
        );

        if (!testResponse.ok) {
          throw new Error(
            `Failed to load test: ${testResponse.status}`
          );
        }

        const testData: TestResponse =
          await testResponse.json();

        setTest(testData);

        /*
         * Convert API questions into UI questions.
         *
         * SUBJECT COMES DIRECTLY FROM DATABASE/API.
         */
        const mappedQuestions: Question[] =
          testData.questions.map((question) => {
            const options = [
              question.option_a,
              question.option_b,
              question.option_c,
              question.option_d,
            ].filter(
              (option): option is string =>
                Boolean(option)
            );

            return {
              id: question.question_id,

              section:
                question.subject?.trim() || "Uncategorized",

              question: question.question_text,

              options,

              answer: null,

              status: "not-visited",

              imageReference:
                question.image_reference,

              marks: question.marks,

              negativeMarks:
                question.negative_marks,
            };
          });

        setQuestions(mappedQuestions);

        if (mappedQuestions.length > 0) {
          setCurrentQuestionId(
            mappedQuestions[0].id
          );
        }

        /*
         * Load student profile.
         */
        try {
          const profileResponse = await fetch(
            `${PROFILE_API_URL}/profile`,
            {
              credentials: "include",
            }
          );

          if (profileResponse.ok) {
            const profileData: ProfileResponse =
              await profileResponse.json();

            setStudent(profileData);
          }
        } catch (profileError) {
          console.error(
            "Failed to load student profile:",
            profileError
          );
        }

        /*
         * Start or resume backend attempt.
         */
        const attemptResponse = await fetch(
          `${ATTEMPT_API_URL}/attempts/start`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            credentials: "include",
            body: JSON.stringify({
              test_id: currentTestId,
            }),
          }
        );

        if (!attemptResponse.ok) {
          // already finished (e.g. submitted automatically after warnings, then the page was reloaded): show that state, never an error
          const finished = await fetch(`${ATTEMPT_API_URL}/attempts/test/${currentTestId}`, { credentials: "include" });
          const last: AttemptResponse | null = finished.ok ? await finished.json() : null;
          if (last && last.status !== "IN_PROGRESS") {
            setAttemptId(last.attempt_id);
            const st = await fetch(`${ATTEMPT_API_URL}/attempts/${last.attempt_id}/integrity`, { credentials: "include" });
            const integrityState: IntegrityState | null = st.ok ? await st.json() : null;
            if (integrityState?.submit_reason === "WARNING_THRESHOLD") setAutoSubmitted(integrityState);
            setSubmitted(true);
            return;
          }
          throw new Error(
            `Failed to start attempt: ${attemptResponse.status}`
          );
        }

        const attemptData: AttemptResponse =
          await attemptResponse.json();

        setAttemptId(attemptData.attempt_id);

        setAttemptExpiresAt(
          new Date(
            attemptData.expires_at
          ).getTime()
        );

        if (
          attemptData.status === "SUBMITTED" ||
          attemptData.status === "AUTO_SUBMITTED"
        ) {
          setSubmitted(true);
          return;
        }

        /*
         * Load saved answers.
         */
        const answersResponse = await fetch(
          `${ATTEMPT_API_URL}/attempts/${attemptData.attempt_id}/answers`,
          {
            credentials: "include",
          }
        );

        if (!answersResponse.ok) {
          throw new Error(
            `Failed to load saved answers: ${answersResponse.status}`
          );
        }

        const savedAnswers: AttemptAnswerResponse[] =
          await answersResponse.json();

        setQuestions((previousQuestions) =>
          previousQuestions.map((question) => {
            const savedAnswer =
              savedAnswers.find(
                (answer) =>
                  answer.question_id === question.id
              );

            if (
              !savedAnswer ||
              !savedAnswer.selected_answer
            ) {
              return question;
            }

            const optionIndex =
              savedAnswer.selected_answer.charCodeAt(0) -
              65;

            const selectedOption =
              question.options[optionIndex];

            if (!selectedOption) {
              return question;
            }

            return {
              ...question,
              answer: selectedOption,
              status: "answered",
            };
          })
        );
      } catch (loadError) {
        console.error(
          "Failed to load test attempt:",
          loadError
        );

        setError("Unable to load this test.");
      } finally {
        setLoading(false);
      }
    }

    loadTestAndAttempt();
  }, [currentTestId, rulesAccepted]);

  /*
   * Mark current question as visited.
   */
  useEffect(() => {
    if (!currentQuestionId || submitted) {
      return;
    }

    // Deliberate: opening a question marks it visited locally, mirroring the visit recorded on the server for the same event.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuestions((previousQuestions) =>
      previousQuestions.map((question) =>
        question.id === currentQuestionId &&
        question.status === "not-visited"
          ? {
              ...question,
              status: "not-answered",
            }
          : question
      )
    );
  }, [currentQuestionId, submitted]);

  /*
   * Server-controlled timer.
   */
  useEffect(() => {
    if (
      attemptExpiresAt === null ||
      submitted
    ) {
      return;
    }

    const expiresAt = attemptExpiresAt;

    async function updateTimer() {
      const remaining = Math.max(
        0,
        Math.ceil(
          (expiresAt - Date.now()) / 1000
        )
      );

      setRemainingSeconds(remaining);

      if (remaining > 0 || !attemptId) {
        return;
      }

      try {
        const response = await fetch(
          `${ATTEMPT_API_URL}/attempts/${attemptId}`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          return;
        }

        const attempt: AttemptResponse =
          await response.json();

        if (
          attempt.status === "AUTO_SUBMITTED"
        ) {
          setSubmitted(true);
        }
      } catch (timerError) {
        console.error(
          "Failed to verify attempt expiry:",
          timerError
        );
      }
    }

    updateTimer();

    const timer = window.setInterval(
      updateTimer,
      1000
    );

    return () => {
      window.clearInterval(timer);
    };
  }, [
    attemptExpiresAt,
    attemptId,
    submitted,
  ]);

  function formatTime(seconds: number) {
    const hours = Math.floor(
      seconds / 3600
    );

    const minutes = Math.floor(
      (seconds % 3600) / 60
    );

    const remaining = seconds % 60;

    return [
      hours,
      minutes,
      remaining,
    ]
      .map((value) =>
        String(value).padStart(2, "0")
      )
      .join(":");
  }

  function handleSectionChange(
    section: string
  ) {
    setSelectedSection(section);

    const sectionQuestions =
      section === "All Sections"
        ? questions
        : questions.filter(
            (question) =>
              question.section === section
          );

    if (sectionQuestions.length > 0) {
      setCurrentQuestionId(
        sectionQuestions[0].id
      );
    }
  }

  async function handleAnswerSelect(
    answer: string
  ) {
    if (
      !currentQuestionId ||
      !attemptId ||
      submitted
    ) {
      return;
    }

    const currentQuestion =
      questions.find(
        (question) =>
          question.id === currentQuestionId
      );

    if (!currentQuestion) {
      return;
    }

    const optionIndex =
      currentQuestion.options.indexOf(answer);

    if (optionIndex === -1) {
      return;
    }

    const selectedAnswer =
      String.fromCharCode(65 + optionIndex);

    setQuestions((previousQuestions) =>
      previousQuestions.map((question) =>
        question.id === currentQuestionId
          ? {
              ...question,
              answer,
              status: "answered",
            }
          : question
      )
    );

    try {
      const response = await fetch(
        `${ATTEMPT_API_URL}/attempts/${attemptId}/answers`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json",
          },
          credentials: "include",
          body: JSON.stringify({
            question_id: currentQuestionId,
            selected_answer:
              selectedAnswer,
          }),
        }
      );

      if (!response.ok) {
        if (response.status === 400) {
          setSubmitted(true);
        }

        throw new Error(
          `Failed to save answer: ${response.status}`
        );
      }
    } catch (saveError) {
      console.error(
        "Failed to save answer:",
        saveError
      );
    }
  }

  async function handleClearResponse() {
    if (
      !currentQuestionId ||
      !attemptId ||
      submitted
    ) {
      return;
    }

    setQuestions((previousQuestions) =>
      previousQuestions.map((question) =>
        question.id === currentQuestionId
          ? {
              ...question,
              answer: null,
              status: "not-answered",
            }
          : question
      )
    );

    try {
      const response = await fetch(
        `${ATTEMPT_API_URL}/attempts/${attemptId}/answers`,
        {
          method: "POST",
          headers: {
            "Content-Type":
              "application/json",
          },
          credentials: "include",
          body: JSON.stringify({
            question_id: currentQuestionId,
            selected_answer: null,
          }),
        }
      );

      if (!response.ok) {
        if (response.status === 400) {
          setSubmitted(true);
        }

        throw new Error(
          `Failed to clear answer: ${response.status}`
        );
      }
    } catch (clearError) {
      console.error(
        "Failed to clear answer:",
        clearError
      );
    }
  }

  function handleMarkForReview() {
    if (
      !currentQuestionId ||
      submitted
    ) {
      return;
    }

    setQuestions((previousQuestions) =>
      previousQuestions.map((question) =>
        question.id === currentQuestionId
          ? {
              ...question,
              status: "review",
            }
          : question
      )
    );

    moveToNextQuestion();
  }

  function moveToNextQuestion() {
    if (
      currentVisibleIndex >= 0 &&
      currentVisibleIndex <
        visibleQuestions.length - 1
    ) {
      setCurrentQuestionId(
        visibleQuestions[
          currentVisibleIndex + 1
        ].id
      );
    }
  }

  function moveToPreviousQuestion() {
    if (currentVisibleIndex > 0) {
      setCurrentQuestionId(
        visibleQuestions[
          currentVisibleIndex - 1
        ].id
      );
    }
  }

  function handleSaveAndNext() {
    moveToNextQuestion();
  }

  async function handleSubmit() {
  if (!attemptId || submitted) {
    return;
  }

  const confirmed = integrity.withoutWarnings(() => window.confirm(
    "Are you sure you want to submit the test?"
  ));

  if (!confirmed) {
    return;
  }

  try {
    setError("");

    // 1. Submit the attempt
    const response = await fetch(
      `${ATTEMPT_API_URL}/attempts/${attemptId}/submit`,
      {
        method: "POST",
        credentials: "include",
      }
    );

    if (!response.ok) {
      throw new Error(
        `Failed to submit attempt: ${response.status}`
      );
    }

    // 2. Evaluate the result
    const resultResponse = await fetch(
      `${RESULT_API_URL}/results/attempts/${attemptId}/evaluate`,
      {
        method: "POST",
        credentials: "include",
      }
    );

    if (!resultResponse.ok) {
      throw new Error(
        `Failed to evaluate result: ${resultResponse.status}`
      );
    }

    await resultResponse.json();

    // 3. Go directly to this test's result
    navigate(`/tests/result/${attemptId}`);
  } catch (submitError) {
    console.error("Failed to submit test:", submitError);

    setError(
      "The test was submitted, but the result could not be generated. Please try again."
    );
  }
}

  if (!rulesAccepted) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card test-attempt-rules">
          <h2>Before you begin</h2>
          <ul>
            <li>The test opens in fullscreen. Stay on this page until you submit.</li>
            <li>Leaving the test is recorded as a warning: switching to another tab or window, minimising, or leaving fullscreen.</li>
            <li>
              Several browser signals from one action count as one warning. When the warning limit is reached (shown at the top of the
              test) the test is <strong>submitted automatically</strong> with the answers you have saved.
            </li>
            <li>Reloading the page or a short network interruption does not end your attempt; you continue where you left off.</li>
            <li>Warnings are a record of what the browser reported, not a finding of cheating. An automatic submission can be reviewed by your institute.</li>
          </ul>
          <button
            type="button"
            onClick={() => {
              enterFullscreen();
              setRulesAccepted(true);
            }}
          >
            I understand, start in fullscreen
          </button>
        </div>
      </section>
    );
  }

  if (autoSubmitted) {
    const review = autoSubmitted.review;
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Test submitted automatically</h2>
          <p role="status">
            Your test was submitted automatically because {autoSubmitted.warnings} warnings were recorded (leaving the test tab or
            window, or leaving fullscreen). Your saved answers were submitted and are evaluated as usual. The submission is under
            review: this is a record of browser activity, not a finding of cheating.
          </p>
          {review || reviewSent ? (
            <p className="test-attempt-review-status">
              Review requested{review ? ` (${review.status === "OPEN" ? "waiting for your institute" : review.status === "RETAKE_AUTHORIZED" ? "a retake was authorized" : "the submission was upheld"})` : ""}.
              {review?.decision_reason ? ` Reason: ${review.decision_reason}` : ""}
            </p>
          ) : (
            <form
              className="test-attempt-review-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const r = await fetch(`${ATTEMPT_API_URL}/attempts/${autoSubmitted.attempt_id}/review`, {
                  method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ message: reviewMessage }),
                });
                setReviewSent(r.ok ? "sent" : "");
                if (!r.ok) setError("The review request could not be sent. Please try again.");
              }}
            >
              <label htmlFor="review-message">If you believe this was unjustified, tell your institute what happened:</label>
              <textarea id="review-message" value={reviewMessage} minLength={10} maxLength={2000} required
                        onChange={(event) => setReviewMessage(event.target.value)} />
              <button type="submit" disabled={reviewMessage.trim().length < 10}>Request a review</button>
              {error && <p className="test-attempt-error">{error}</p>}
            </form>
          )}
          <button type="button" onClick={() => navigate("/results")}>Go to Results</button>
        </div>
      </section>
    );
  }

  if (loading) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Loading Test...</h2>

          <p>
            Please wait while we load the
            test and questions.
          </p>
        </div>
      </section>
    );
  }

  if (error || !test) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>Unable to Load Test</h2>

          <p>
            {error || "Test not found."}
          </p>

          <button
            type="button"
            onClick={() =>
              navigate("/tests")
            }
          >
            Back to Tests
          </button>
        </div>
      </section>
    );
  }

  if (submitted) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <div className="test-attempt-submitted-icon">
            ✓
          </div>

          <h2>Test Submitted</h2>

          <p>
            Your test has been submitted
            successfully. Your result will be
            available after evaluation.
          </p>

          <button
            type="button"
            onClick={() =>
              navigate("/results")
            }
          >
            Go to Results
          </button>
        </div>
      </section>
    );
  }

  if (!currentQuestion) {
    return (
      <section className="test-attempt-submitted">
        <div className="test-attempt-submitted-card">
          <h2>No Questions Available</h2>

          <p>
            Questions for this test are not
            available yet.
          </p>

          <button
            type="button"
            onClick={() =>
              navigate("/tests")
            }
          >
            Back to Tests
          </button>
        </div>
      </section>
    );
  }

  const studentName = student
    ? `${student.first_name} ${student.last_name}`.trim()
    : "Student";

  return (
    <section className="test-attempt-page">
      <header className="test-attempt-header">
        <div className="test-attempt-header-left">
          <span className="test-attempt-provider">
            {test.global_occurrence_id ? "Global Test" : test.provider.name}
          </span>

          <h1>{test.name}</h1>

          {!test.global_occurrence_id && (
            <span>
              {test.branch.name}
            </span>
          )}
        </div>

        <div className="test-attempt-header-right">
          <span className="test-attempt-timer-label">
            Time Remaining
          </span>

          <strong
            className={
              remainingSeconds <= 300
                ? "test-attempt-timer warning"
                : "test-attempt-timer"
            }
          >
            {formatTime(
              remainingSeconds
            )}
          </strong>
        </div>
      </header>

      {integrity.state && (
        <div className={integrity.state.warnings > 0 ? "test-attempt-warnings active" : "test-attempt-warnings"} role="status">
          Warnings: {integrity.state.warnings} / {integrity.state.threshold}.{" "}
          {integrity.state.warnings > 0
            ? `Leaving the test again counts as another warning; at ${integrity.state.threshold} the test is submitted automatically.`
            : "Stay on this page; leaving the test or fullscreen is recorded."}
          {!document.fullscreenElement && (
            <button type="button" className="test-attempt-fullscreen" onClick={() => enterFullscreen()}>Return to fullscreen</button>
          )}
        </div>
      )}

      <div className="test-attempt-sections">
        {sections.map((section) => (
          <button
            key={section}
            type="button"
            className={
              selectedSection === section
                ? "test-attempt-section active"
                : "test-attempt-section"
            }
            onClick={() =>
              handleSectionChange(section)
            }
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
              <span>
                Question{" "}
                {currentVisibleIndex + 1}
              </span>

              <strong>
                Question{" "}
                {currentVisibleIndex + 1} of{" "}
                {visibleQuestions.length}
              </strong>
            </div>
          </div>

          <div className="test-attempt-question">
            <h2>
              {currentQuestion.question}
            </h2>

            {currentQuestion.imageReference && (
              <div className="test-attempt-question-image">
                <img
                  src={
                    currentQuestion.imageReference
                  }
                  alt="Question diagram"
                />
              </div>
            )}

            <div className="test-attempt-options">
              {currentQuestion.options.map(
                (option, index) => {
                  const optionLetter =
                    String.fromCharCode(
                      65 + index
                    );

                  return (
                    <label
                      key={option}
                      className={
                        currentQuestion.answer ===
                        option
                          ? "test-attempt-option selected"
                          : "test-attempt-option"
                      }
                    >
                      <input
                        type="radio"
                        name={`question-${currentQuestion.id}`}
                        value={option}
                        checked={
                          currentQuestion.answer ===
                          option
                        }
                        onChange={() =>
                          handleAnswerSelect(
                            option
                          )
                        }
                      />

                      <span className="option-radio" />

                      <span className="option-letter">
                        {optionLetter}.
                      </span>

                      <span>
                        {option}
                      </span>
                    </label>
                  );
                }
              )}
            </div>
          </div>

          <div className="test-attempt-navigation">
            <button
              type="button"
              className="secondary-button"
              onClick={
                handleClearResponse
              }
            >
              Clear Response
            </button>

            <button
              type="button"
              className="secondary-button"
              onClick={
                handleMarkForReview
              }
            >
              Mark for Review
            </button>

            <div className="test-attempt-navigation-right">
              <button
                type="button"
                className="secondary-button"
                disabled={
                  currentVisibleIndex === 0
                }
                onClick={
                  moveToPreviousQuestion
                }
              >
                Previous
              </button>

              <button
                type="button"
                className="primary-button"
                onClick={
                  handleSaveAndNext
                }
                disabled={
                  currentVisibleIndex ===
                  visibleQuestions.length - 1
                }
              >
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
              <strong>
                {studentName}
              </strong>

              <span>
                {student?.student_id ?? ""}
              </span>
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
              <h2>
                Question Palette
              </h2>

              <span>
                {visibleQuestions.length}{" "}
                Questions
              </span>
            </div>

            <div className="question-palette">
              {visibleQuestions.map(
                (question, index) => (
                  <button
                    key={question.id}
                    type="button"
                    className={`palette-question ${
                      question.status
                    } ${
                      question.id ===
                      currentQuestion.id
                        ? "current"
                        : ""
                    }`}
                    onClick={() =>
                      setCurrentQuestionId(
                        question.id
                      )
                    }
                  >
                    {index + 1}
                  </button>
                )
              )}
            </div>
          </div>

          <div className="test-attempt-legend">
            <h2>Legend</h2>

            <div className="legend-item">
              <span className="legend-dot not-visited" />
              <span>
                Not Visited
              </span>
            </div>

            <div className="legend-item">
              <span className="legend-dot not-answered" />
              <span>
                Not Answered
              </span>
            </div>

            <div className="legend-item">
              <span className="legend-dot answered" />
              <span>
                Answered
              </span>
            </div>

            <div className="legend-item">
              <span className="legend-dot review" />
              <span>
                Marked for Review
              </span>
            </div>
          </div>

          <button
            type="button"
            className="submit-test-button"
            onClick={handleSubmit}
          >
            Submit Test
          </button>
        </aside>
      </div>
    </section>
  );
}

export default TestAttemptPage;