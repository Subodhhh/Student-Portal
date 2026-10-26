import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ENROLLMENT_API_URL,
  RESULT_API_URL,
} from "../../services/api";
import "../../styles/results.css";

type Result = {
  result_id: string;
  attempt_id: string;
  test_id: string;
  student_id: string;
  test_name: string;
  global_occurrence_id?: string | null;  // a unified Global Test: papers from several of your institutes, owned by none of them alone
  is_retake?: boolean;                   // an attempt your institute authorized after a review
  superseded?: boolean;                  // replaced by that authorized retake (kept for the record; not your official result)
  provider_id: string;
  provider_name: string;
  branch_id: string;
  branch_name: string;
  city: string | null;
  state: string | null;
  total_marks: number;
  obtained_marks: number;
  created_at: string;
};

type Enrollment = {
  provider: {
    provider_id: string;
    name: string;
  };
  status: string;
};

function ResultsPage() {
  const navigate = useNavigate();

  const [results, setResults] = useState<Result[]>([]);
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("ALL");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadData() {
      try {
        const [resultsResponse, enrollmentsResponse] =
          await Promise.all([
            fetch(`${RESULT_API_URL}/results`, {
              credentials: "include",
            }),
            fetch(`${ENROLLMENT_API_URL}/enrollments`, {
              credentials: "include",
            }),
          ]);

        if (!resultsResponse.ok) {
          throw new Error("Failed to load results");
        }

        if (!enrollmentsResponse.ok) {
          throw new Error("Failed to load enrollments");
        }

        const resultsData: Result[] =
          await resultsResponse.json();

        const enrollmentsData: Enrollment[] =
          await enrollmentsResponse.json();

        setResults(resultsData);
        setEnrollments(enrollmentsData);
      } catch (loadError) {
        console.error("Failed to load results:", loadError);
        setError("Unable to load your results.");
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, []);

  const providers = useMemo(() => {
    const providerMap = new Map<string, string>();

    enrollments.forEach((enrollment) => {
      if (enrollment.status !== "ACTIVE") {
        return;
      }

      providerMap.set(
        enrollment.provider.provider_id,
        enrollment.provider.name
      );
    });

    return Array.from(providerMap.entries()).map(
      ([id, name]) => ({
        id,
        name,
      })
    );
  }, [enrollments]);

  const filteredResults = useMemo(() => {
    const searchValue = search.trim().toLowerCase();

    return results.filter((result) => {
      // A unified Global Test result belongs to none of the institutes alone (provider_id is only its anchor): it is listed, labelled
      // "Global Test", under every institute filter rather than under one institute as if it owned the whole exam.
      const matchesProvider =
        provider === "ALL" ||
        !!result.global_occurrence_id ||
        result.provider_id === provider;

      const matchesSearch =
        !searchValue ||
        result.test_name
          .toLowerCase()
          .includes(searchValue) ||
        result.provider_name
          .toLowerCase()
          .includes(searchValue) ||
        result.branch_name
          .toLowerCase()
          .includes(searchValue);

      return matchesProvider && matchesSearch;
    });
  }, [results, provider, search]);

  const selectedProviderName = useMemo(() => {
    if (provider === "ALL") {
      return "";
    }

    return (
      providers.find((item) => item.id === provider)?.name ||
      ""
    );
  }, [provider, providers]);

  function formatDate(value: string) {
    return new Date(value).toLocaleDateString(
      "en-IN",
      {
        day: "2-digit",
        month: "short",
        year: "numeric",
      }
    );
  }

  if (loading) {
    return (
      <div className="results-page">
        <div className="results-loading">
          Loading your results...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="results-page">
        <div className="results-error">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="results-page">
      <div className="results-toolbar">
        <div className="results-search">
          <span>⌕</span>

          <input
            type="text"
            placeholder="Search tests, providers or branches..."
            value={search}
            onChange={(event) =>
              setSearch(event.target.value)
            }
          />
        </div>

        <select
          className="results-provider-select"
          value={provider}
          onChange={(event) =>
            setProvider(event.target.value)
          }
        >
          <option value="ALL">
            All Providers
          </option>

          {providers.map((item) => (
            <option
              key={item.id}
              value={item.id}
            >
              {item.name}
            </option>
          ))}
        </select>
      </div>

      {filteredResults.length > 0 ? (
        <div className="results-list">
          {filteredResults.map((result) => (
            <article
              className="result-card"
              key={result.result_id}
            >
              <div className="result-card-main">
                <div className="result-provider">
                  {result.provider_name}
                </div>

                <h3>{result.test_name}</h3>
                {result.is_retake && <span className="result-attempt-label">Authorized retake</span>}
                {result.superseded && <span className="result-attempt-label superseded">Superseded by an authorized retake (not official)</span>}

                <p>
                  {result.branch_name}
                  {result.city
                    ? ` · ${result.city}`
                    : ""}
                </p>

                <span className="result-date">
                  Completed{" "}
                  {formatDate(result.created_at)}
                </span>
              </div>

              <div className="result-card-score">
                <span>Score</span>

                <strong>
                  {result.obtained_marks}
                  <small>
                    /{result.total_marks}
                  </small>
                </strong>
              </div>

              <button
                type="button"
                className="result-view-button"
                onClick={() =>
                  navigate(
                    `/tests/result/${result.attempt_id}`
                  )
                }
              >
                View Result
              </button>
            </article>
          ))}
        </div>
      ) : provider !== "ALL" ? (
        <div className="provider-no-results">
          <div className="provider-no-results-content">
            <span className="provider-no-results-name">
              {selectedProviderName}
            </span>

            <strong>
              No results available
            </strong>

            <p>
              You have not completed any tests from
              this provider yet.
            </p>
          </div>
        </div>
      ) : provider !== "ALL" ? (
        <div className="provider-no-results">
          <div className="provider-no-results-content">
            <span className="provider-no-results-name">
              {selectedProviderName}
            </span>

            <strong>
              No results available
            </strong>

            <p>
              You have not completed any tests from this provider yet.
            </p>
          </div>
        </div>
      ) : (
        <div className="results-empty-card">
          <div className="results-empty-content">
            <div className="results-empty-icon">
              —
            </div>

            <h3>
              No results found
            </h3>

            <p>
              {search
                ? "Try changing your search."
                : "You have not completed any tests yet."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default ResultsPage;