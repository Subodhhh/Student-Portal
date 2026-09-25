import { useState } from "react";
import PageHeader from "../../components/PageHeader";
import EmptyState from "../../components/EmptyState";
import "../../styles/ranking.css";

const tests = [
  {
    id: "test-1",
    name: "NEET Full Mock Test 01",
    date: "25 Sep 2026",
    score: "645 / 720",
    rank: 18,
    participants: 2450,
  },
  {
    id: "test-2",
    name: "NEET Full Mock Test 02",
    date: "18 Sep 2026",
    score: "632 / 720",
    rank: 24,
    participants: 2180,
  },
];

const rankings = [
  { rank: 1, name: "Student A", score: 710 },
  { rank: 2, name: "Student B", score: 705 },
  { rank: 3, name: "Student C", score: 702 },
  { rank: 4, name: "Student D", score: 698 },
  { rank: 5, name: "Student E", score: 695 },
  { rank: 18, name: "You", score: 645 },
];

function RankingPage() {
  const [selectedTestId, setSelectedTestId] = useState(tests[0].id);

  const selectedTest = tests.find((test) => test.id === selectedTestId);

  if (!selectedTest) {
    return (
      <EmptyState
        title="No test selected"
        description="Select a test to view its global ranking."
      />
    );
  }

  return (
    <div className="ranking-page">
      <PageHeader
        title="Global Ranking"
        description="View your rank for globally conducted tests."
      />

      <div className="ranking-selector">
        <label htmlFor="ranking-test">Select Test</label>

        <select
          id="ranking-test"
          value={selectedTestId}
          onChange={(event) => setSelectedTestId(event.target.value)}
        >
          {tests.map((test) => (
            <option key={test.id} value={test.id}>
              {test.name}
            </option>
          ))}
        </select>
      </div>

      <section className="ranking-summary">
        <div>
          <span>Test</span>
          <strong>{selectedTest.name}</strong>
        </div>

        <div>
          <span>Your Score</span>
          <strong>{selectedTest.score}</strong>
        </div>

        <div>
          <span>Your Rank</span>
          <strong>#{selectedTest.rank}</strong>
        </div>

        <div>
          <span>Participants</span>
          <strong>{selectedTest.participants}</strong>
        </div>
      </section>

      <section className="ranking-panel">
        <div className="ranking-panel-header">
          <div>
            <h3>Test Ranking</h3>
            <p>Global ranking for {selectedTest.name}</p>
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
              {rankings.map((student) => (
                <tr
                  key={student.rank}
                  className={student.name === "You" ? "current-student" : ""}
                >
                  <td>#{student.rank}</td>
                  <td>{student.name}</td>
                  <td>{student.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default RankingPage;