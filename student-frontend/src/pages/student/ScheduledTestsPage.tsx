import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ATTEMPT_API_URL,
  TEST_API_URL,
} from "../../services/api";

import "../../styles/scheduled-tests.css";

type TestSection = {
  position: number;
  subject: string;
  status: string;                // PAPER, or why there is no paper for this student
  message: string | null;
  question_count: number;
};

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

type Attempt = {
  attempt_id: string;
  test_id: string;
  started_at: string;
  expires_at: string;
  submitted_at?: string | null;
  status:
    | "IN_PROGRESS"
    | "SUBMITTED"
    | "AUTO_SUBMITTED";
};

type CompletedTest = {
  test: Test;
  attempt: Attempt;
};

const filters = [
  "Active",
  "Upcoming",
  "Missed",
  "Completed",
];

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

function formatTimeRange(
  startAt: string | null,
  endAt: string | null
) {
  if (!startAt || !endAt) {
    return "Time not specified";
  }

  return `${formatTime(startAt)} - ${formatTime(endAt)}`;
}

// A unified Global Test is one exam whose subjects come from the student's own institutes: it is shown as the Global Test, never as
// one of those institutes.
function TestSource({ test }: { test: Test }) {
  if (test.global_occurrence_id) {
    return <span>Global Test</span>;
  }
  return (
    <>
      <span>{test.provider.name}</span>
      {test.branch?.name && <span> · {test.branch.name}</span>}
    </>
  );
}

function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="scheduled-tests-empty-card">
      <div className="scheduled-tests-empty">
        <div className="scheduled-tests-empty-icon">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            aria-hidden="true"
          >
            <rect
              x="5"
              y="3"
              width="14"
              height="18"
              rx="2"
            />
            <path d="M9 8h6" />
            <path d="M9 12h6" />
            <path d="M9 16h3" />
          </svg>
        </div>

        <h3>{title}</h3>

        <p>{description}</p>
      </div>
    </div>
  );
}

function ScheduledTestsPage() {
  const navigate = useNavigate();

  const [activeFilter, setActiveFilter] =
    useState("Active");

  const [activeTests, setActiveTests] = useState<Test[]>(
    []
  );

  const [upcomingTests, setUpcomingTests] =
    useState<Test[]>([]);

  const [missedTests, setMissedTests] = useState<Test[]>(
    []
  );

  const [completedTests, setCompletedTests] = useState<
    CompletedTest[]
  >([]);

  const [attempts, setAttempts] = useState<
    Record<string, Attempt | null>
  >({});

  const [loading, setLoading] = useState(true);

  const [error, setError] = useState("");

  useEffect(() => {
    async function loadTests() {
      try {
        const [
          activeResponse,
          upcomingResponse,
          missedResponse,
          completedResponse,
        ] = await Promise.all([
          fetch(`${TEST_API_URL}/tests`, {
            credentials: "include",
          }),

          fetch(`${TEST_API_URL}/tests/scheduled`, {
            credentials: "include",
          }),

          fetch(`${TEST_API_URL}/tests/missed`, {
            credentials: "include",
          }),

          fetch(`${ATTEMPT_API_URL}/attempts/completed`, {
            credentials: "include",
          }),
        ]);

        if (
          !activeResponse.ok ||
          !upcomingResponse.ok ||
          !missedResponse.ok ||
          !completedResponse.ok
        ) {
          throw new Error(
            "Failed to load scheduled tests."
          );
        }

        const activeData: Test[] =
          await activeResponse.json();

        const upcomingData: Test[] =
          await upcomingResponse.json();

        const missedData: Test[] =
          await missedResponse.json();

        const completedAttempts: Attempt[] =
          await completedResponse.json();

        /*
         * Get attempt status for every active test.
         */
        const attemptResults = await Promise.all(
          activeData.map(async (test) => {
            const response = await fetch(
              `${ATTEMPT_API_URL}/attempts/test/${test.test_id}`,
              {
                credentials: "include",
              }
            );

            if (response.status === 404) {
              return {
                testId: test.test_id,
                attempt: null,
              };
            }

            if (!response.ok) {
              throw new Error(
                "Failed to load attempt."
              );
            }

            const attempt: Attempt =
              await response.json();

            return {
              testId: test.test_id,
              attempt,
            };
          })
        );

        const attemptMap: Record<
          string,
          Attempt | null
        > = {};

        attemptResults.forEach(
          ({ testId, attempt }) => {
            attemptMap[testId] = attempt;
          }
        );

        setAttempts(attemptMap);

        /*
         * Submitted tests should not appear
         * under Active.
         */
        const availableActiveTests =
          activeData.filter((test) => {
            const attempt = attemptMap[test.test_id];

            return (
              !attempt ||
              attempt.status === "IN_PROGRESS"
            );
          });

        setActiveTests(availableActiveTests);

        setUpcomingTests(upcomingData);

        setMissedTests(missedData);

        /*
         * Keep only the latest completed attempt
         * for each test.
         */
        const latestAttempts = new Map<
          string,
          Attempt
        >();

        completedAttempts.forEach((attempt) => {
          if (!latestAttempts.has(attempt.test_id)) {
            latestAttempts.set(
              attempt.test_id,
              attempt
            );
          }
        });

        /*
         * Load test details for completed tests.
         */
        const completedResults = await Promise.all(
          Array.from(latestAttempts.values()).map(
            async (attempt) => {
              const response = await fetch(
                `${TEST_API_URL}/tests/${attempt.test_id}/details`,
                {
                  credentials: "include",
                }
              );

              if (!response.ok) {
                return null;
              }

              const test: Test =
                await response.json();

              return {
                test,
                attempt,
              };
            }
          )
        );

        setCompletedTests(
          completedResults.filter(
            (item): item is CompletedTest =>
              item !== null
          )
        );
      } catch (loadError) {
        console.error(
          "Failed to load scheduled tests:",
          loadError
        );

        setError(
          "Unable to load scheduled tests."
        );
      } finally {
        setLoading(false);
      }
    }

    loadTests();
  }, []);

  function getCurrentTests() {
    switch (activeFilter) {
      case "Active":
        return activeTests;

      case "Upcoming":
        return upcomingTests;

      case "Missed":
        return missedTests;

      default:
        return [];
    }
  }

  function openTest(testId: string) {
    navigate(`/tests/${testId}/attempt`);
  }

  function openDetails(testId: string) {
    navigate(`/tests/${testId}`);
  }

  function openResult(attemptId: string) {
    navigate(`/tests/result/${attemptId}`);
  }

  function getEmptyState() {
    const states: Record<
      string,
      {
        title: string;
        description: string;
      }
    > = {
      Active: {
        title: "No active tests",
        description:
          "There are no tests currently available for you to take.",
      },

      Upcoming: {
        title: "No upcoming tests",
        description:
          "There are no upcoming tests scheduled for you.",
      },

      Missed: {
        title: "No missed tests",
        description:
          "There are no missed tests in this category.",
      },

      Completed: {
        title: "No completed tests",
        description:
          "You have not completed any tests yet.",
      },
    };

    return states[activeFilter];
  }

  const currentTests = getCurrentTests();

  const emptyState = getEmptyState();

  return (
    <div className="scheduled-tests-page">
      <div className="scheduled-test-tabs">
        {filters.map((filter) => (
          <button
            key={filter}
            type="button"
            className={
              activeFilter === filter
                ? "active"
                : ""
            }
            onClick={() =>
              setActiveFilter(filter)
            }
          >
            {filter}
          </button>
        ))}
      </div>

      {loading ? (
        <EmptyState
          title="Loading tests"
          description="Please wait while we load your tests."
        />
      ) : error ? (
        <EmptyState
          title="Unable to load tests"
          description={error}
        />
      ) : activeFilter === "Completed" ? (
        completedTests.length > 0 ? (
          <div className="scheduled-test-list">
            {completedTests.map(
              ({ test, attempt }) => (
                <article
                  className="scheduled-test-card"
                  key={attempt.attempt_id}
                >
                  <div className="scheduled-test-main">
                    <div className="scheduled-test-title-row">
                      <h3>{test.name}</h3>

                      <span className="scheduled-test-status scheduled-test-status-completed">
                        Completed
                      </span>
                    </div>

                    <div className="scheduled-test-provider">
                      <TestSource test={test} />
                    </div>

                    <div className="scheduled-test-meta">
                      <span>
                        {formatDate(test.start_at)}
                      </span>

                      <span>
                        {formatTimeRange(
                          test.start_at,
                          test.end_at
                        )}
                      </span>

                      <span>
                        {test.duration_minutes} min
                      </span>

                      <span>
                        {test.total_questions} Questions
                      </span>

                      <span>
                        {test.total_marks} Marks
                      </span>
                    </div>
                  </div>

                  <div className="scheduled-test-action">
                    <button
                      type="button"
                      onClick={() =>
                        openResult(
                          attempt.attempt_id
                        )
                      }
                    >
                      View Result
                    </button>
                  </div>
                </article>
              )
            )}
          </div>
        ) : (
          <EmptyState
            title={emptyState.title}
            description={emptyState.description}
          />
        )
      ) : currentTests.length > 0 ? (
        <div className="scheduled-test-list">
          {currentTests.map((test) => {
            const attempt =
              attempts[test.test_id];

            const isInProgress =
              attempt?.status === "IN_PROGRESS";

            return (
              <article
                className="scheduled-test-card"
                key={test.test_id}
              >
                <div className="scheduled-test-main">
                  <div className="scheduled-test-title-row">
                    <h3>{test.name}</h3>

                    <span
                      className={`scheduled-test-status scheduled-test-status-${activeFilter.toLowerCase()}`}
                    >
                      {test.total_questions === 0 && activeFilter !== "Upcoming" ? "Unavailable" : activeFilter}
                    </span>
                  </div>

                  <div className="scheduled-test-provider">
                    <TestSource test={test} />
                  </div>

                  {test.description && (
                    <p className="scheduled-test-description">
                      {test.description}
                    </p>
                  )}

                  <div className="scheduled-test-meta">
                    <span>
                      {formatDate(test.start_at)}
                    </span>

                    <span>
                      {formatTimeRange(
                        test.start_at,
                        test.end_at
                      )}
                    </span>

                    <span>
                      {test.duration_minutes} min
                    </span>

                    <span>
                      {test.total_questions} Questions
                    </span>

                    <span>
                      {test.total_marks} Marks
                    </span>
                  </div>
                </div>

                <div className="scheduled-test-action">
                  {activeFilter === "Active" && test.total_questions === 0 && (
                    // a Global Test card without any paper for this student: it explains each subject, nothing to start
                    <>
                      <span className="scheduled-test-unavailable">No paper available</span>
                      <button type="button" className="scheduled-test-details-button" onClick={() => openDetails(test.test_id)}>
                        View Details
                      </button>
                    </>
                  )}

                  {activeFilter === "Active" && test.total_questions > 0 && (
                    <button
                      type="button"
                      className={
                        isInProgress
                          ? "resume-test-button"
                          : ""
                      }
                      onClick={() =>
                        openTest(test.test_id)
                      }
                    >
                      {isInProgress
                        ? "Resume Test"
                        : "Start Test"}
                    </button>
                  )}

                  {activeFilter === "Upcoming" && (
                    <button
                      type="button"
                      className="scheduled-test-details-button"
                      onClick={() =>
                        openDetails(test.test_id)
                      }
                    >
                      View Details
                    </button>
                  )}

                  {activeFilter === "Missed" && (
                    <button
                      type="button"
                      className="scheduled-test-details-button"
                      onClick={() =>
                        openDetails(test.test_id)
                      }
                    >
                      View Details
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState
          title={emptyState.title}
          description={emptyState.description}
        />
      )}
    </div>
  );
}

export default ScheduledTestsPage;