import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "../../components/PageHeader";
import EmptyState from "../../components/EmptyState";
import { getTestAttempt } from "../../utils/testAttemptStorage";
import "../../styles/scheduled-tests.css";

type ScheduledTest = {
  id: number;
  name: string;
  provider: string;
  date: string;
  time: string;
  duration: string;
  status:
    | "Active"
    | "Upcoming"
    | "Missed"
    | "Completed"
    | "Left In Between";
};

const scheduledTests: ScheduledTest[] = [
  {
    id: 2,
    name: "Physics Chapter Test",
    provider: "ABC Coaching Institute",
    date: "25 Sep 2026",
    time: "10:00 AM - 11:00 AM",
    duration: "60 min",
    status: "Active",
  },
  {
    id: 1,
    name: "NEET Full Mock Test 01",
    provider: "ABC Coaching Institute",
    date: "27 Sep 2026",
    time: "10:00 AM - 01:00 PM",
    duration: "180 min",
    status: "Upcoming",
  },
  {
    id: 3,
    name: "Chemistry Practice Test",
    provider: "XYZ Academy",
    date: "22 Sep 2026",
    time: "02:00 PM - 03:00 PM",
    duration: "60 min",
    status: "Completed",
  },
  {
    id: 4,
    name: "Biology Revision Test",
    provider: "XYZ Academy",
    date: "20 Sep 2026",
    time: "10:00 AM - 11:00 AM",
    duration: "60 min",
    status: "Missed",
  },
  {
    id: 5,
    name: "Physics Test 05",
    provider: "ABC Coaching Institute",
    date: "18 Sep 2026",
    time: "04:00 PM - 05:00 PM",
    duration: "60 min",
    status: "Left In Between",
  },
];

const filters = [
  "Active",
  "Upcoming",
  "Missed",
  "Completed",
  "Left In Between",
];

function ScheduledTestsPage() {
  const navigate = useNavigate();

  const [activeFilter, setActiveFilter] =
    useState("Active");

  const [, setAttemptRefresh] =
    useState(0);

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

  const filteredTests =
    scheduledTests.filter(
      (test) =>
        test.status === activeFilter
    );

  function openTest(testId: number) {
    window.open(
      `/tests/${testId}/attempt`,
      "_blank"
    );
  }

  return (
    <div className="scheduled-tests-page">
      <PageHeader
        title="Scheduled Tests"
        description="View tests scheduled for you by your enrolled providers."
      />

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

      <div className="scheduled-test-list">
        {filteredTests.length > 0 ? (
          filteredTests.map((test) => {
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
                className="scheduled-test-card"
                key={test.id}
              >
                <div className="scheduled-test-main">
                  <div className="scheduled-test-title-row">
                    <h3>{test.name}</h3>

                    <span
                      className={`scheduled-test-status scheduled-test-status-${test.status
                        .toLowerCase()
                        .replaceAll(
                          " ",
                          "-"
                        )}`}
                    >
                      {test.status}
                    </span>
                  </div>

                  <div className="scheduled-test-provider">
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

                  <div className="scheduled-test-meta">
                    <span>
                      {test.date}
                    </span>

                    <span>
                      {test.time}
                    </span>

                    <span>
                      {test.duration}
                    </span>
                  </div>
                </div>

                <div className="scheduled-test-action">
                  {test.status ===
                    "Active" &&
                  !isSubmitted ? (
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
                  ) : isSubmitted ? (
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
                      className="scheduled-test-details-button"
                      onClick={() =>
                        navigate(
                          `/tests/${test.id}`
                        )
                      }
                    >
                      View Details
                    </button>
                  )}
                </div>
              </article>
            );
          })
        ) : (
          <EmptyState
            title={`No ${activeFilter.toLowerCase()} tests`}
            description="There are no tests in this category."
          />
        )}
      </div>
    </div>
  );
}

export default ScheduledTestsPage;