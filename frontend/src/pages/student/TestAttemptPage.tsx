import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  getTestAttempt,
  startTestAttempt,
  updateTestAttempt,
} from "../../utils/testAttemptStorage";
import "../../styles/test-attempt.css";

type Section = "Physics" | "Chemistry" | "Biology";

type QuestionStatus =
  | "not-visited"
  | "not-answered"
  | "answered"
  | "review";

type Question = {
  id: number;
  section: Section;
  question: string;
  options: string[];
  answer: string | null;
  status: QuestionStatus;
};

const initialQuestions: Question[] = [
  {
    id: 1,
    section: "Physics",
    question:
      "A body is moving with a uniform velocity. What can be said about its acceleration?",
    options: [
      "Zero",
      "Constant but non-zero",
      "Increasing",
      "Decreasing",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 2,
    section: "Physics",
    question: "The SI unit of force is:",
    options: [
      "Joule",
      "Newton",
      "Watt",
      "Pascal",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 3,
    section: "Physics",
    question:
      "Which quantity has both magnitude and direction?",
    options: [
      "Speed",
      "Distance",
      "Mass",
      "Velocity",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 4,
    section: "Physics",
    question:
      "The acceleration due to gravity near the surface of Earth is approximately:",
    options: [
      "5.8 m/s²",
      "7.8 m/s²",
      "9.8 m/s²",
      "12.8 m/s²",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 5,
    section: "Chemistry",
    question:
      "The atomic number of carbon is:",
    options: ["4", "6", "8", "12"],
    answer: null,
    status: "not-visited",
  },
  {
    id: 6,
    section: "Chemistry",
    question:
      "The pH of a neutral solution at 25°C is:",
    options: ["0", "5", "7", "14"],
    answer: null,
    status: "not-visited",
  },
  {
    id: 7,
    section: "Chemistry",
    question:
      "Which of the following is a noble gas?",
    options: [
      "Oxygen",
      "Nitrogen",
      "Chlorine",
      "Neon",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 8,
    section: "Chemistry",
    question:
      "Water is chemically represented as:",
    options: ["CO₂", "H₂O", "O₂", "H₂"],
    answer: null,
    status: "not-visited",
  },
  {
    id: 9,
    section: "Biology",
    question:
      "The basic structural and functional unit of life is:",
    options: [
      "Tissue",
      "Organ",
      "Cell",
      "Nucleus",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 10,
    section: "Biology",
    question:
      "Which organelle is known as the powerhouse of the cell?",
    options: [
      "Ribosome",
      "Mitochondria",
      "Nucleus",
      "Golgi apparatus",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 11,
    section: "Biology",
    question:
      "Which blood cells are primarily responsible for immunity?",
    options: [
      "Red blood cells",
      "White blood cells",
      "Platelets",
      "Plasma",
    ],
    answer: null,
    status: "not-visited",
  },
  {
    id: 12,
    section: "Biology",
    question:
      "Photosynthesis mainly takes place in the:",
    options: [
      "Mitochondria",
      "Nucleus",
      "Chloroplast",
      "Ribosome",
    ],
    answer: null,
    status: "not-visited",
  },
];

const sections: Array<
  "All Sections" | Section
> = [
  "All Sections",
  "Physics",
  "Chemistry",
  "Biology",
];

function TestAttemptPage() {
  const navigate = useNavigate();
  const { testId } = useParams();

  const currentTestId = testId ?? "1";

  const [questions, setQuestions] =
    useState<Question[]>(initialQuestions);

  const [selectedSection, setSelectedSection] =
    useState<"All Sections" | Section>(
      "All Sections"
    );

  const [currentQuestionId, setCurrentQuestionId] =
    useState(1);

  const [remainingSeconds, setRemainingSeconds] =
    useState(0);

  const [attemptExpiresAt, setAttemptExpiresAt] =
    useState<number | null>(null);

  const [submitted, setSubmitted] =
    useState(false);

  const visibleQuestions = useMemo(() => {
    if (selectedSection === "All Sections") {
      return questions;
    }

    return questions.filter(
      (question) =>
        question.section === selectedSection
    );
  }, [questions, selectedSection]);

  const currentQuestion = questions.find(
    (question) =>
      question.id === currentQuestionId
  );

  const currentVisibleIndex =
    visibleQuestions.findIndex(
      (question) =>
        question.id === currentQuestionId
    );

  /*
   * Load the existing attempt.
   *
   * If no attempt exists, create one.
   *
   * If the attempt was already submitted,
   * show the submitted screen instead.
   */
  useEffect(() => {
    const existingAttempt =
      getTestAttempt(currentTestId);

    if (
      existingAttempt?.status === "SUBMITTED" ||
      existingAttempt?.status === "AUTO_SUBMITTED"
    ) {
      setSubmitted(true);
      return;
    }

    const attempt =
      existingAttempt ??
      startTestAttempt(currentTestId, 180);

    setAttemptExpiresAt(attempt.expiresAt);

    const remaining = Math.max(
      0,
      Math.ceil(
        (attempt.expiresAt - Date.now()) / 1000
      )
    );

    setRemainingSeconds(remaining);
  }, [currentTestId]);

  /*
   * Mark a question as visited when the student
   * opens it for the first time.
   */
  useEffect(() => {
    const currentQuestion = questions.find(
      (question) =>
        question.id === currentQuestionId
    );

    if (
      !currentQuestion ||
      currentQuestion.status !== "not-visited"
    ) {
      return;
    }

    setQuestions((previousQuestions) =>
      previousQuestions.map((question) =>
        question.id === currentQuestionId
          ? {
              ...question,
              status: "not-answered",
            }
          : question
      )
    );
  }, [currentQuestionId, questions]);

  /*
   * Composite test timer.
   *
   * The timer is calculated from expiresAt.
   * It does NOT reset when the page reloads
   * or the student returns to the test.
   */
  useEffect(() => {
    if (
      attemptExpiresAt === null ||
      submitted
    ) {
      return;
    }

    const expiresAt = attemptExpiresAt;

    function updateTimer() {
      const remaining = Math.max(
        0,
        Math.ceil(
          (expiresAt - Date.now()) / 1000
        )
      );

      setRemainingSeconds(remaining);
    }

    updateTimer();

    const timer = window.setInterval(
      updateTimer,
      1000
    );

    return () => {
      window.clearInterval(timer);
    };
  }, [attemptExpiresAt, submitted]);

  /*
   * Automatically submit when the composite
   * timer reaches zero.
   */
  useEffect(() => {
    if (
      remainingSeconds > 0 ||
      submitted ||
      attemptExpiresAt === null
    ) {
      return;
    }

    updateTestAttempt(currentTestId, {
      status: "AUTO_SUBMITTED",
    });

    setSubmitted(true);
  }, [
    remainingSeconds,
    submitted,
    attemptExpiresAt,
    currentTestId,
  ]);

  function formatTime(seconds: number) {
    const hours = Math.floor(seconds / 3600);

    const minutes = Math.floor(
      (seconds % 3600) / 60
    );

    const remaining = seconds % 60;

    return [hours, minutes, remaining]
      .map((value) =>
        String(value).padStart(2, "0")
      )
      .join(":");
  }

  function handleSectionChange(
    section: "All Sections" | Section
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

  function handleAnswerSelect(
    answer: string
  ) {
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
  }

  function handleClearResponse() {
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
  }

  function handleMarkForReview() {
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

  function handleSubmit() {
  const confirmed = window.confirm(
    "Are you sure you want to submit the test?"
  );

  if (!confirmed) {
    return;
  }

  updateTestAttempt(currentTestId, {
    status: "AUTO_SUBMITTED",
  });

  navigate(
    `/tests/${currentTestId}/performance`
  );  
}

  if (!currentQuestion || submitted) {
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

  return (
    <section className="test-attempt-page">
      <header className="test-attempt-header">
        <div className="test-attempt-header-left">
          <span className="test-attempt-provider">
            ABC Coaching Institute
          </span>

          <h1>NEET Full Mock Test 01</h1>
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
            {formatTime(remainingSeconds)}
          </strong>
        </div>
      </header>

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
          <div className="test-attempt-question-header">
            <div>
              <span>
                Question {currentQuestion.id} of{" "}
                {questions.length}
              </span>

              <strong>
                {currentQuestion.section}
              </strong>
            </div>

            <button
              type="button"
              className="language-button"
            >
              English ▾
            </button>
          </div>

          <div className="test-attempt-question">
            <h2>
              {currentQuestion.question}
            </h2>

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

                      <span>{option}</span>
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
              >
                Save & Next
              </button>
            </div>
          </div>
        </main>

        <aside className="test-attempt-sidebar">
          <div className="student-mini-profile">
            <div className="student-mini-avatar">
              S
            </div>

            <div>
              <strong>Student</strong>
              <span>STU001</span>
            </div>
          </div>

          <div className="question-palette-card">
            <div className="palette-heading">
              <h2>Question Palette</h2>

              <span>
                {visibleQuestions.length} Questions
              </span>
            </div>

            <div className="question-palette">
              {visibleQuestions.map(
                (question) => (
                  <button
                    key={question.id}
                    type="button"
                    className={`palette-question ${question.status} ${
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
                    {question.id}
                  </button>
                )
              )}
            </div>
          </div>

          <div className="test-attempt-legend">
            <h2>Legend</h2>

            <div className="legend-item">
              <span className="legend-dot not-visited" />
              <span>Not Visited</span>
            </div>

            <div className="legend-item">
              <span className="legend-dot not-answered" />
              <span>Not Answered</span>
            </div>

            <div className="legend-item">
              <span className="legend-dot answered" />
              <span>Answered</span>
            </div>

            <div className="legend-item">
              <span className="legend-dot review" />
              <span>Marked for Review</span>
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