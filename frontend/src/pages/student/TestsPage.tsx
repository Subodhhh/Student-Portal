import { useEffect, useMemo, useState } from "react";
import "../../styles/tests.css";
import { getTestAttempt } from "../../utils/testAttemptStorage";
import {
  ENROLLMENT_API_URL,
  TEST_API_URL,
} from "../../services/api";

type Enrollment = {
  enrollment_id: string;
  provider: {
    provider_id: string;
    name: string;
    email: string;
    phone: string;
    address: string;
  };
  branch: {
    branch_id: string;
    name: string;
    city: string | null;
    state: string | null;
    address: string | null;
    pincode: string | null;
  };
  status: string;
  enrolled_at: string;
  batches: {
    batch_id: string;
    name: string;
    start_date: string;
    end_date: string;
  }[];
};

type Test = {
  test_id: string;
  name: string;
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
  description: string | null;
  duration_minutes: number;
  total_questions: number;
  total_marks: number;
  start_at: string | null;
  end_at: string | null;
  status: string;
};

function formatDate(date: string | null) {
  if (!date) {
    return "Date not specified";
  }

  return new Date(date).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function TestsPage() {
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [tests, setTests] = useState<Test[]>([]);
  const [search, setSearch] = useState("");
  const [selectedProvider, setSelectedProvider] =
    useState("All Providers");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [, setAttemptRefresh] = useState(0);

  useEffect(() => {
    async function fetchData() {
      try {
        setLoading(true);
        setError("");

        const [
          enrollmentsResponse,
          testsResponse,
        ] = await Promise.all([
          fetch(
            `${ENROLLMENT_API_URL}/enrollments`,
            {
              credentials: "include",
            }
          ),
          fetch(`${TEST_API_URL}/tests`, {
            credentials: "include",
          }),
        ]);

        if (!enrollmentsResponse.ok) {
          throw new Error(
            `Failed to fetch enrollments: ${enrollmentsResponse.status}`
          );
        }

        if (!testsResponse.ok) {
          throw new Error(
            `Failed to fetch tests: ${testsResponse.status}`
          );
        }

        const enrollmentData: Enrollment[] =
          await enrollmentsResponse.json();

        const testData: Test[] =
          await testsResponse.json();

        setEnrollments(enrollmentData);
        setTests(testData);
      } catch (error) {
        console.error(
          "Failed to load tests:",
          error
        );

        setError("Unable to load tests.");
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, []);

  useEffect(() => {
    function refreshAttempts() {
      setAttemptRefresh(
        (value) => value + 1
      );
    }

    window.addEventListener(
      "focus",
      refreshAttempts
    );

    return () => {
      window.removeEventListener(
        "focus",
        refreshAttempts
      );
    };
  }, []);

  const providers = [
    "All Providers",
    ...enrollments.map(
      (enrollment) =>
        enrollment.provider.name
    ),
  ];

  const filteredTests = useMemo(() => {
    const searchText =
      search.trim().toLowerCase();

    return tests.filter((test) => {
      const matchesProvider =
        selectedProvider === "All Providers" ||
        test.provider.name ===
          selectedProvider;

      const searchableText = [
        test.name,
        test.provider.name,
        test.branch.name,
        test.description ?? "",
      ]
        .join(" ")
        .toLowerCase();

      const matchesSearch =
        searchableText.includes(searchText);

      return (
        matchesProvider &&
        matchesSearch
      );
    });
  }, [
    tests,
    search,
    selectedProvider,
  ]);

  function openTest(testId: string) {
    window.open(
      `/tests/${testId}/attempt`,
      "_blank"
    );
  }

  return (
    <section className="tests-page">
      <div className="tests-page-header">
        <div>
          <h2>Tests</h2>

          <p>
            Tests currently available for
            you to take.
          </p>
        </div>
      </div>

      <div className="tests-filters">
        <div className="tests-search">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            aria-hidden="true"
          >
            <circle
              cx="11"
              cy="11"
              r="7"
            />

            <path d="m20 20-4-4" />
          </svg>

          <input
            type="text"
            placeholder="Search available tests..."
            value={search}
            onChange={(event) =>
              setSearch(event.target.value)
            }
          />
        </div>

        <div className="tests-provider-filter">
          <label htmlFor="provider">
            Provider
          </label>

          <select
            id="provider"
            value={selectedProvider}
            onChange={(event) =>
              setSelectedProvider(
                event.target.value
              )
            }
          >
            {providers.map((provider) => (
              <option
                key={provider}
                value={provider}
              >
                {provider}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="tests-empty">
          <h3>Loading tests...</h3>

          <p>
            Please wait while we load your
            available tests.
          </p>
        </div>
      ) : error ? (
        <div className="tests-empty">
          <h3>Unable to load tests</h3>

          <p>{error}</p>
        </div>
      ) : filteredTests.length > 0 ? (
        <div className="tests-list">
          {filteredTests.map((test) => {
            const attempt =
              getTestAttempt(
                test.test_id
              );

            const isResume =
              attempt?.status ===
              "IN_PROGRESS";

            const isSubmitted =
              attempt?.status ===
                "SUBMITTED" ||
              attempt?.status ===
                "AUTO_SUBMITTED";

            return (
              <article
                className="test-card"
                key={test.test_id}
              >
                <div className="test-card-main">
                  <div className="test-card-title-row">
                    <h3>{test.name}</h3>

                    <span
                      className={
                        isSubmitted
                          ? "test-status test-status-submitted"
                          : "test-status test-status-available"
                      }
                    >
                      {isSubmitted
                        ? "Submitted"
                        : "Available"}
                    </span>
                  </div>

                  <div className="test-provider">
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

                    <span>
                      {test.provider.name}
                    </span>
                  </div>

                  <p className="test-description">
                    {test.description ??
                      "No description available."}
                  </p>

                  <div className="test-meta">
                    <span>
                      <strong>
                        {test.branch.name}
                      </strong>
                    </span>

                    <span>
                      {formatDate(
                        test.start_at
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

                <div className="test-card-action">
                  {isSubmitted ? (
                    <button
                      type="button"
                      className="submitted-test-button"
                      disabled
                    >
                      Submitted
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={
                        isResume
                          ? "resume-test-button"
                          : ""
                      }
                      onClick={() =>
                        openTest(
                          test.test_id
                        )
                      }
                    >
                      {isResume
                        ? "Resume Test"
                        : "Start Test"}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="tests-empty">
          <div className="tests-empty-icon">
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

          <h3>No available tests</h3>

          <p>
            There are no available tests
            matching your current search or
            provider selection.
          </p>
        </div>
      )}
    </section>
  );
}

export default TestsPage;