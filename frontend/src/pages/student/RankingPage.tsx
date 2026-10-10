import { useEffect, useState } from "react";
import EmptyState from "../../components/EmptyState";
import "../../styles/ranking.css";

type Result = {
  test_id: string;
  test_name: string;
  total_marks: number;
  obtained_marks: number;
};

type RankingItem = {
  rank: number;
  student_id: string;
  student_name: string;
  obtained_marks: number;
};

type RankingResponse = {
  test_id: string;
  my_student_id: string;
  my_rank: number | null;
  total_students: number;
  my_score: number | null;
  leaderboard: RankingItem[];
};

const RESULT_API_URL =
  import.meta.env.VITE_RESULT_API_URL ||
  "http://localhost:8007";

const RANKING_API_URL =
  import.meta.env.VITE_RANKING_API_URL ||
  "http://localhost:8008";

function RankingPage() {
  const [tests, setTests] = useState<Result[]>([]);
  const [selectedTestId, setSelectedTestId] = useState("");
  const [ranking, setRanking] = useState<RankingResponse | null>(null);

  const [loadingTests, setLoadingTests] = useState(true);
  const [loadingRanking, setLoadingRanking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const loadTests = async () => {
      try {
        setLoadingTests(true);
        setError("");

        const response = await fetch(
          `${RESULT_API_URL}/results`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          throw new Error("Failed to load completed tests.");
        }

        const data: Result[] = await response.json();

        setTests(data);

        if (data.length > 0) {
          setSelectedTestId(data[0].test_id);
        }
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load completed tests."
        );
      } finally {
        setLoadingTests(false);
      }
    };

    loadTests();
  }, []);

  useEffect(() => {
    if (!selectedTestId) {
      setRanking(null);
      return;
    }

    const loadRanking = async () => {
      try {
        setLoadingRanking(true);
        setError("");

        const response = await fetch(
          `${RANKING_API_URL}/ranking/${selectedTestId}`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          throw new Error("Failed to load ranking.");
        }

        const data: RankingResponse = await response.json();

        setRanking(data);
      } catch (err) {
        setRanking(null);
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load ranking."
        );
      } finally {
        setLoadingRanking(false);
      }
    };

    loadRanking();
  }, [selectedTestId]);

  if (loadingTests) {
    return (
      <div className="ranking-page">
        <div className="ranking-loading">
          Loading your completed tests...
        </div>
      </div>
    );
  }

  if (error && tests.length === 0) {
    return (
      <div className="ranking-page">
        <div className="ranking-error">
          {error}
        </div>
      </div>
    );
  }

  if (tests.length === 0) {
    return (
      <div className="ranking-page">
        <EmptyState
          title="No rankings available"
          description="Complete a test to view its ranking."
        />
      </div>
    );
  }

  const selectedTest = tests.find(
    (test) => test.test_id === selectedTestId
  );

  if (!selectedTest) {
    return (
      <div className="ranking-page">
        <EmptyState
          title="No test selected"
          description="Select a test to view its ranking."
        />
      </div>
    );
  }

  return (
    <div className="ranking-page">
      <div className="ranking-selector">
        <label htmlFor="ranking-test">
          Select Test
        </label>

        <select
          id="ranking-test"
          value={selectedTestId}
          onChange={(event) =>
            setSelectedTestId(event.target.value)
          }
        >
          {tests.map((test) => (
            <option
              key={test.test_id}
              value={test.test_id}
            >
              {test.test_name}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="ranking-error">
          {error}
        </div>
      )}

      {loadingRanking ? (
        <div className="ranking-loading">
          Loading ranking...
        </div>
      ) : ranking ? (
        <>
          <section className="ranking-summary">
            <div>
              <span>Test</span>
              <strong>{selectedTest.test_name}</strong>
            </div>

            <div>
              <span>Your Score</span>
              <strong>
                {ranking.my_score ?? 0} / {selectedTest.total_marks}
              </strong>
            </div>

            <div>
              <span>Your Rank</span>
              <strong>
                {ranking.my_rank
                  ? `#${ranking.my_rank}`
                  : "Not ranked"}
              </strong>
            </div>

            <div>
              <span>Participants</span>
              <strong>
                {ranking.total_students}
              </strong>
            </div>
          </section>

          <section className="ranking-panel">
            <div className="ranking-panel-header">
              <div>
                <h3>Test Ranking</h3>
                <p>
                  Global ranking for {selectedTest.test_name}
                </p>
              </div>
            </div>

            <div className="ranking-table-wrapper">
              <table className="ranking-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Student</th>
                    <th>Score</th>
                  </tr>
                </thead>

                <tbody>
                  {ranking.leaderboard.map((student) => (
                    <tr
                      key={student.student_id}
                      className={
                        student.student_id ===
                        ranking.my_student_id
                          ? "current-student"
                          : ""
                      }
                    >
                      <td>#{student.rank}</td>
                      <td>{student.student_name}</td>
                      <td>{student.obtained_marks}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}

export default RankingPage;