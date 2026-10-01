import { useEffect, useState } from "react";
import { getMyEnrollments } from "../../services/enrollmentService";
import "../../styles/my-institutes.css";

type Batch = {
  batch_id: string;
  name: string;
  start_date: string | null;
  end_date: string | null;
};

type Enrollment = {
  enrollment_id: string;
  provider: {
    provider_id: string;
    name: string;
    email: string | null;
    phone: string | null;
    address: string | null;
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
  batches: Batch[];
};

function formatDate(date: string | null) {
  if (!date) {
    return "Not available";
  }

  return new Date(date).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function MyInstitutesPage() {
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedEnrollment, setSelectedEnrollment] =
    useState<Enrollment | null>(null);

  useEffect(() => {
    async function loadEnrollments() {
      try {
        const data = await getMyEnrollments();
        setEnrollments(data);
      } catch {
        setError("Unable to load your providers.");
      } finally {
        setLoading(false);
      }
    }

    loadEnrollments();
  }, []);

  if (loading) {
    return <div className="page-container">Loading...</div>;
  }

  if (error) {
    return <div className="page-container">{error}</div>;
  }

  return (
    <div className="page-container my-institutes-page">
      <div className="my-institutes-header">
        <h1>My Enrollments</h1>
        <p>View your providers, branches, and batches.</p>
      </div>

      {enrollments.length === 0 ? (
        <div className="empty-state">
          <h3>No providers yet</h3>
          <p>You are not currently enrolled with any provider.</p>
        </div>
      ) : (
        <div className="institute-grid">
          {enrollments.map((enrollment) => (
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
                  <span>Branch</span>
                  <strong>{enrollment.branch.name}</strong>

                  {enrollment.branch.city && (
                    <small>
                      {enrollment.branch.city}
                      {enrollment.branch.state
                        ? `, ${enrollment.branch.state}`
                        : ""}
                    </small>
                  )}
                </div>

                <div className="institute-detail">
                  <span>Enrolled Since</span>
                  <strong>
                    {formatDate(enrollment.enrolled_at)}
                  </strong>
                </div>
              </div>

              <div className="batch-section">
                <h3>Your Batches</h3>

                {enrollment.batches.length === 0 ? (
                  <p className="no-batches">No batches assigned.</p>
                ) : (
                  <ul className="batch-list">
                    {enrollment.batches.map((batch) => (
                      <li key={batch.batch_id}>
                        <div>
                          <strong>{batch.name}</strong>

                          <small>
                            {formatDate(batch.start_date)} –{" "}
                            {formatDate(batch.end_date)}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <button
                className="view-details-button"
                onClick={() => setSelectedEnrollment(enrollment)}
              >
                View Details
              </button>
            </div>
          ))}
        </div>
      )}

      {selectedEnrollment && (
        <div
          className="enrollment-modal-overlay"
          onClick={() => setSelectedEnrollment(null)}
        >
          <div
            className="enrollment-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="enrollment-modal-header">
              <div>
                <h2>{selectedEnrollment.provider.name}</h2>
                <span
                  className={`institute-status ${selectedEnrollment.status.toLowerCase()}`}
                >
                  {selectedEnrollment.status}
                </span>
              </div>

              <button
                className="modal-close-button"
                onClick={() => setSelectedEnrollment(null)}
              >
                ×
              </button>
            </div>

            <div className="enrollment-modal-section">
              <h3>Branch</h3>

              <p>
                <strong>{selectedEnrollment.branch.name}</strong>
              </p>

              {selectedEnrollment.branch.address && (
                <p>{selectedEnrollment.branch.address}</p>
              )}

              <p>
                {selectedEnrollment.branch.city}
                {selectedEnrollment.branch.state
                  ? `, ${selectedEnrollment.branch.state}`
                  : ""}
                {selectedEnrollment.branch.pincode
                  ? ` - ${selectedEnrollment.branch.pincode}`
                  : ""}
              </p>
            </div>

            <div className="enrollment-modal-section">
              <h3>Provider Contact</h3>

              {selectedEnrollment.provider.phone && (
                <p>{selectedEnrollment.provider.phone}</p>
              )}

              {selectedEnrollment.provider.email && (
                <p>{selectedEnrollment.provider.email}</p>
              )}

              {selectedEnrollment.provider.address && (
                <p>{selectedEnrollment.provider.address}</p>
              )}
            </div>

            <div className="enrollment-modal-section">
              <h3>Enrollment</h3>

              <p>
                <strong>Enrollment ID:</strong>{" "}
                {selectedEnrollment.enrollment_id}
              </p>

              <p>
                <strong>Enrolled Since:</strong>{" "}
                {formatDate(selectedEnrollment.enrolled_at)}
              </p>
            </div>

            <div className="enrollment-modal-section">
              <h3>Your Batches</h3>

              {selectedEnrollment.batches.map((batch) => (
                <div className="modal-batch" key={batch.batch_id}>
                  <strong>{batch.name}</strong>

                  <span>
                    {formatDate(batch.start_date)} –{" "}
                    {formatDate(batch.end_date)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MyInstitutesPage;