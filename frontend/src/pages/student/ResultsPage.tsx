import { useState } from "react";
import PageHeader from "../../components/PageHeader";
import EmptyState from "../../components/EmptyState";
import "../../styles/results.css";

const results = [
  {
    name: "NEET Mock Test 01",
    date: "22 Sep 2026",
    score: "645 / 720",
    percentage: "89.58%",
  },
  {
    name: "Physics Test 05",
    date: "20 Sep 2026",
    score: "82 / 100",
    percentage: "82%",
  },
  {
    name: "Chemistry Practice Test",
    date: "18 Sep 2026",
    score: "76 / 100",
    percentage: "76%",
  },
  {
    name: "Biology Revision Test",
    date: "15 Sep 2026",
    score: "91 / 100",
    percentage: "91%",
  },
];

function ResultsPage() {
  const [search, setSearch] = useState("");

  const filteredResults = results.filter((result) =>
    result.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="results-page">
      <PageHeader
        title="Results"
        description="View your results from completed tests."
      />

      <div className="results-toolbar">
        <input
          type="text"
          placeholder="Search results..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <div className="results-list">
        {filteredResults.length > 0 ? (
          filteredResults.map((result) => (
            <div className="result-card" key={result.name}>
              <div className="result-card-main">
                <h3>{result.name}</h3>
                <p>{result.date}</p>
              </div>

              <div className="result-card-score">
                <strong>{result.score}</strong>
                <span>{result.percentage}</span>
              </div>

              <button>View Result</button>
            </div>
          ))
        ) : (
          <EmptyState
            title="No results found"
            description="Try changing your search."
          />
        )}
      </div>
    </div>
  );
}

export default ResultsPage;