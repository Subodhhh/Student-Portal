import { useEffect, useMemo, useState } from "react";
import "../../styles/tests.css";
import { getTestAttempt } from "../../utils/testAttemptStorage";

type Test = {
  id: number;
  name: string;
  provider: string;
  exam: string;
  description: string;
  date: string;
  duration: number;
  questions: number;
};

const tests: Test[] = [
  {
    id: 1,
    name: "NEET Full Mock Test 01",
    provider: "ABC Coaching Institute",
    exam: "NEET",
    description:
      "Complete NEET pattern mock examination.",
    date: "25 Sep 2026",
    duration: 180,
    questions: 180,
  },
  {
    id: 2,
    name: "Physics Chapter Test",
    provider: "ABC Coaching Institute",
    exam: "Physics",
    description:
      "Practice test covering mechanics and motion.",
    date: "24 Sep 2026",
    duration: 60,
    questions: 45,
  },
];

function TestsPage() {
  const [search, setSearch] = useState("");
  const [selectedProvider, setSelectedProvider] =
    useState("All Providers");

  const [, setAttemptRefresh] = useState(0);

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
    ...Array.from(
      new Set(
        tests.map(
          (test) => test.provider
        )
      )
    ),
  ];

  const filteredTests = useMemo(() => {
    return tests.filter((test) => {
      const matchesProvider =
        selectedProvider ===
          "All Providers" ||
        test.provider ===
          selectedProvider;

      const searchText =
        search.toLowerCase();

      const matchesSearch =
        test.name
          .toLowerCase()
          .includes(searchText) ||
        test.provider
          .toLowerCase()
          .includes(searchText) ||
        test.exam
          .toLowerCase()
          .includes(searchText);

      return (
        matchesProvider &&
        matchesSearch
      );
    });
  }, [search, selectedProvider]);

  function openTest(testId: number) {
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

        <div className="tests-provider-count">
          <span>
            {providers.length - 1}
          </span>

          <small>Providers</small>
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

      {filteredTests.length > 0 ? (
        <div className="tests-list">
          {filteredTests.map((test) => {
            const attempt =
              getTestAttempt(
                String(test.id)
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
                key={test.id}
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
                      {test.provider}
                    </span>
                  </div>

                  <p className="test-description">
                    {test.description}
                  </p>

                  <div className="test-meta">
                    <span>
                      <strong>
                        {test.exam}
                      </strong>
                    </span>

                    <span>
                      {test.date}
                    </span>

                    <span>
                      {test.duration} min
                    </span>

                    <span>
                      {test.questions} Questions
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
                        openTest(test.id)
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