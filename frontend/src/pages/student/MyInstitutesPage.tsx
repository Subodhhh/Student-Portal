import { useEffect, useState } from "react";
import { getMyEnrollments } from "../../services/enrollmentService";
import "../../styles/my-institutes.css";

type Batch = {
  batch_id: string;
  name: string;
};

type Enrollment = {
  enrollment_id: string;
  provider: {
    provider_id: string;
    name: string;
  };
  status: string;
  enrolled_at: string;
  batches: Batch[];
};

function MyInstitutesPage() {
  const [institutes, setInstitutes] = useState<Enrollment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadInstitutes() {
      try {
        const data = await getMyEnrollments();
        setInstitutes(data);
      } catch {
        setError("Unable to load your institutes.");
      } finally {
        setLoading(false);
      }
    }

    loadInstitutes();
  }, []);

  if (loading) {
    return <div className="page-container">Loading institutes...</div>;
  }

  if (error) {
    return <div className="page-container">{error}</div>;
  }

  return (
    <div className="page-container my-institutes-page">
      <div className="my-institutes-header">
        <h1>My Institutes</h1>
        <p>View your institutes, enrollment status, and batches.</p>
      </div>

      {institutes.length === 0 ? (
        <div className="empty-state">
          <h3>No institutes yet</h3>
          <p>You are not currently enrolled with any institute.</p>
        </div>
      ) : (
        <div className="institute-grid">
          {institutes.map((enrollment) => (
            <div
              className="institute-card"
              key={enrollment.enrollment_id}
            >
              <div className="institute-card-header">
                <div>
                  <h2>{enrollment.provider.name}</h2>

                  <span
                    className={`institute-status ${enrollment.status.toLowerCase()}`}
                  >
                    {enrollment.status}
                  </span>
                </div>
              </div>

              <div className="institute-details">
                <div className="institute-detail">
                  <span>Enrolled Since</span>
                  <strong>{enrollment.enrolled_at}</strong>
                </div>

                <div className="institute-detail">
                  <span>Batches</span>
                  <strong>{enrollment.batches.length}</strong>
                </div>
              </div>

              <div className="batch-section">
                <h3>Your Batches</h3>

                {enrollment.batches.length === 0 ? (
                  <p className="no-batches">No batches assigned.</p>
                ) : (
                  <ul className="batch-list">
                    {enrollment.batches.map((batch) => (
                      <li key={batch.batch_id}>{batch.name}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default MyInstitutesPage;