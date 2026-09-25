import { useNavigate, useParams } from "react-router-dom";
import "../../styles/test-details.css";

const test = {
  name: "NEET Full Mock Test 01",
  provider: "ABC Coaching Institute",
  exam: "NEET",
  testType: "Full Length Mock Test",
  status: "Upcoming",
  questions: 180,
  marks: 720,
  duration: "180 Minutes",
  date: "25 Sep 2026",
  time: "10:00 AM - 01:00 PM",
  description:
    "Complete NEET pattern mock examination covering Physics, Chemistry and Biology.",
  instructions: [
    "The test contains 180 questions.",
    "The total duration of the test is 180 minutes.",
    "The timer will start when you begin the test.",
    "Make sure you have a stable internet connection before starting.",
    "Once the test is submitted, you cannot attempt it again.",
  ],
};

function TestDetailsPage() {
  const navigate = useNavigate();
  const { testId } = useParams();

  function handleTakeTest() {
    navigate(`/tests/${testId}/attempt`);
  }

  return (
    <section className="test-details-page">
      <button
        className="test-details-back"
        type="button"
        onClick={() => navigate("/tests")}
      >
        ← Back to Tests
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

            <span>{test.provider}</span>
          </div>

          <h2>{test.name}</h2>
          <p>{test.testType}</p>
        </div>

        <span
          className={`test-details-status ${
            test.status === "Available"
              ? "test-details-status-available"
              : "test-details-status-upcoming"
          }`}
        >
          {test.status}
        </span>
      </div>

      <div className="test-details-description">
        <p>{test.description}</p>
      </div>

      <div className="test-details-grid">
        <div className="test-detail-item">
          <span>Exam</span>
          <strong>{test.exam}</strong>
        </div>

        <div className="test-detail-item">
          <span>Questions</span>
          <strong>{test.questions}</strong>
        </div>

        <div className="test-detail-item">
          <span>Total Marks</span>
          <strong>{test.marks}</strong>
        </div>

        <div className="test-detail-item">
          <span>Duration</span>
          <strong>{test.duration}</strong>
        </div>
      </div>

      <div className="test-details-section">
        <h3>Test Schedule</h3>

        <div className="test-schedule-info">
          <div>
            <span>Date</span>
            <strong>{test.date}</strong>
          </div>

          <div>
            <span>Test Window</span>
            <strong>{test.time}</strong>
          </div>
        </div>
      </div>

      <div className="test-details-section">
        <h3>Instructions</h3>

        <ul className="test-instructions">
          {test.instructions.map((instruction, index) => (
            <li key={index}>{instruction}</li>
          ))}
        </ul>
      </div>

      {test.status === "Available" && (
        <div className="test-details-action">
          <button type="button" onClick={handleTakeTest}>
            Take Test
          </button>
        </div>
      )}
    </section>
  );
}

export default TestDetailsPage;