import { useNavigate, useParams } from "react-router-dom";
import "../../styles/performance.css";

type SectionPerformance = {
  name: string;
  totalQuestions: number;
  correct: number;
  wrong: number;
  unanswered: number;
  marks: number;
  totalMarks: number;
  accuracy: number;
};

const sectionPerformance: SectionPerformance[] = [
  {
    name: "Physics",
    totalQuestions: 60,
    correct: 52,
    wrong: 6,
    unanswered: 2,
    marks: 208,
    totalMarks: 240,
    accuracy: 89.66,
  },
  {
    name: "Chemistry",
    totalQuestions: 60,
    correct: 54,
    wrong: 5,
    unanswered: 1,
    marks: 216,
    totalMarks: 240,
    accuracy: 91.53,
  },
  {
    name: "Biology",
    totalQuestions: 60,
    correct: 55,
    wrong: 4,
    unanswered: 1,
    marks: 221,
    totalMarks: 240,
    accuracy: 93.22,
  },
];

function PerformancePage() {
  const navigate = useNavigate();
  const { testId } = useParams();

  const totalQuestions = 180;
  const correctAnswers = 161;
  const wrongAnswers = 15;
  const unansweredAnswers = 4;

  const marksObtained = 645;
  const totalMarks = 720;

  const percentage =
    (marksObtained / totalMarks) * 100;

  const attempted =
    correctAnswers + wrongAnswers;

  const accuracy =
    attempted > 0
      ? (correctAnswers / attempted) * 100
      : 0;

  function handleBackToResults() {
    navigate("/results");
  }

  function handleViewTest() {
    navigate(`/tests/${testId}`);
  }

  return (
    <section className="performance-page">
      <div className="performance-header">
        <div>
          <span className="performance-provider">
            ABC Coaching Institute
          </span>

          <h2>NEET Full Mock Test 01</h2>

          <p>
            Test Performance
          </p>
        </div>

        <div className="performance-header-actions">
          <button
            type="button"
            className="performance-secondary-button"
            onClick={handleViewTest}
          >
            Test Details
          </button>

          <button
            type="button"
            className="performance-primary-button"
            onClick={handleBackToResults}
          >
            Back to Results
          </button>
        </div>
      </div>

      <div className="performance-summary-card">
        <div className="performance-score">
          <span>Score</span>

          <strong>
            {marksObtained}
            <small> / {totalMarks}</small>
          </strong>

          <p>
            {percentage.toFixed(2)}%
          </p>
        </div>

        <div className="performance-summary-divider" />

        <div className="performance-summary-item">
          <span>Correct</span>
          <strong>{correctAnswers}</strong>
          <small>
            out of {totalQuestions}
          </small>
        </div>

        <div className="performance-summary-item">
          <span>Wrong</span>
          <strong>{wrongAnswers}</strong>
          <small>
            answers
          </small>
        </div>

        <div className="performance-summary-item">
          <span>Unanswered</span>
          <strong>{unansweredAnswers}</strong>
          <small>
            questions
          </small>
        </div>

        <div className="performance-summary-item">
          <span>Accuracy</span>
          <strong>
            {accuracy.toFixed(2)}%
          </strong>
          <small>
            attempted
          </small>
        </div>
      </div>

      <div className="performance-grid">
        <div className="performance-card">
          <div className="performance-card-header">
            <div>
              <h3>Section-wise Performance</h3>

              <p>
                Your performance across each
                section.
              </p>
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
                {sectionPerformance.map(
                  (section) => (
                    <tr key={section.name}>
                      <td>
                        <strong>
                          {section.name}
                        </strong>
                      </td>

                      <td>
                        {section.totalQuestions}
                      </td>

                      <td className="performance-correct">
                        {section.correct}
                      </td>

                      <td className="performance-wrong">
                        {section.wrong}
                      </td>

                      <td className="performance-unanswered">
                        {section.unanswered}
                      </td>

                      <td>
                        {section.marks} /{" "}
                        {section.totalMarks}
                      </td>

                      <td>
                        {section.accuracy.toFixed(
                          2
                        )}
                        %
                      </td>
                    </tr>
                  )
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="performance-card performance-question-summary">
          <div className="performance-card-header">
            <div>
              <h3>Question Summary</h3>

              <p>
                Overall attempt breakdown.
              </p>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot correct" />

            <div>
              <strong>
                {correctAnswers}
              </strong>

              <span>
                Correct Answers
              </span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot wrong" />

            <div>
              <strong>
                {wrongAnswers}
              </strong>

              <span>
                Wrong Answers
              </span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot unanswered" />

            <div>
              <strong>
                {unansweredAnswers}
              </strong>

              <span>
                Unanswered
              </span>
            </div>
          </div>

          <div className="performance-question-stat">
            <span className="performance-stat-dot attempted" />

            <div>
              <strong>
                {attempted}
              </strong>

              <span>
                Total Attempted
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="performance-footer-card">
        <div>
          <h3>Test Completed</h3>

          <p>
            Your test has been evaluated. You can
            review your performance from this page.
          </p>
        </div>

        <button
          type="button"
          onClick={handleBackToResults}
        >
          View All Results
        </button>
      </div>
    </section>
  );
}

export default PerformancePage;