import { useEffect, useState } from "react";
import { getMyEnrollments } from "../../hooks/enrollmentService";
import "../../styles/MyInstitutesPage.css";

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
  if (!date) return "Not available";
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
  const [selectedEnrollment, setSelectedEnrollment] = useState<Enrollment | null>(null);

  useEffect(() => {
    async function loadEnrollments() {
      try {
        const data = await getMyEnrollments();
        setEnrollments(data);
      } catch {
        setError("Unable to load your providers. Please try again later.");
      } finally {
        setLoading(false);
      }
    }
    loadEnrollments();
  }, []);

  useEffect(() => {
    if (selectedEnrollment) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "unset";
    return () => { document.body.style.overflow = "unset"; };
  }, [selectedEnrollment]);

  if (loading) {
    return (
      <div className="my-institutes-page">
        <div className="loading-state">
          <p>Loading your enrollments...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="my-institutes-page">
        <div className="error-state">
          <h3>Oops!</h3>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="my-institutes-page">
      {enrollments.length === 0 ? (
        <div className="empty-state">
          <h3>No providers yet</h3>
          <p>You are not currently enrolled with any provider.</p>
        </div>
      ) : (
        <div className="institute-grid">
          {enrollments.map((enrollment) => (
            <div className="institute-card" key={enrollment.enrollment_id}>
              <div className="institute-card-header">
                <div>
                  <h2>{enrollment.provider.name}</h2>
                  <span className={`institute-status ${enrollment.status?.toLowerCase() || "unknown"}`}>
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
                      {enrollment.branch.state ? `, ${enrollment.branch.state}` : ""}
                    </small>
                  )}
                </div>

                <div className="institute-detail">
                  <span>Enrolled Since</span>
                  <strong>{formatDate(enrollment.enrolled_at)}</strong>
                </div>
              </div>

              <div className="batch-section">
                <h3>Your Batches</h3>
                {enrollment.batches.length === 0 ? (
                  <p className="no-batches">No batches assigned yet.</p>
                ) : (
                  <ul className="batch-list">
                    {enrollment.batches.map((batch) => (
                      <li key={batch.batch_id}>
                        <strong>{batch.name}</strong>
                        <small>
                          {formatDate(batch.start_date)} – {formatDate(batch.end_date)}
                        </small>
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

      {/* Modal */}
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
                <span className={`institute-status ${selectedEnrollment.status?.toLowerCase() || "unknown"}`}>
                  {selectedEnrollment.status}
                </span>
              </div>
              <button
                className="modal-close-button"
                onClick={() => setSelectedEnrollment(null)}
                aria-label="Close modal"
              >
                ✕
              </button>
            </div>

            <div className="enrollment-modal-body">
              
              {/* Branch Section */}
              <div className="enrollment-modal-section">
                <h3>
                  <svg className="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 21h18M5 21V9l7-5 7 5v12M9 21v-6h6v6" />
                  </svg>
                  Branch
                </h3>
                <p><strong>{selectedEnrollment.branch.name}</strong></p>
                {selectedEnrollment.branch.address && <p>{selectedEnrollment.branch.address}</p>}
                <p>
                  {selectedEnrollment.branch.city}
                  {selectedEnrollment.branch.state ? `, ${selectedEnrollment.branch.state}` : ""}
                  {selectedEnrollment.branch.pincode ? ` - ${selectedEnrollment.branch.pincode}` : ""}
                </p>
              </div>

              {/* Contact Section */}
              {/*<div className="enrollment-modal-section">
                <h3>
                  <svg className="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
                  </svg>
                  Provider Contact
                </h3>
                
                {selectedEnrollment.provider.phone && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
                    </svg>
                    <span>{selectedEnrollment.provider.phone}</span>
                  </div>
                )}
                
                {selectedEnrollment.provider.email && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                      <polyline points="22,6 12,13 2,6" />
                    </svg>
                    <span>{selectedEnrollment.provider.email}</span>
                  </div>
                )}

                {selectedEnrollment.provider.address && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                      <circle cx="12" cy="10" r="3" />
                    </svg>
                    <span>{selectedEnrollment.provider.address}</span>
                  </div>
                )}
              </div>*/}
              <div className="enrollment-modal-section">
                <h3>
                  <svg className="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                  Provider Contact
                </h3>

                {selectedEnrollment.provider.phone && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
                    </svg>
                    <span>{selectedEnrollment.provider.phone}</span>
                  </div>
                )}

                {selectedEnrollment.provider.email && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                      <polyline points="22,6 12,13 2,6" />
                    </svg>
                    <span>{selectedEnrollment.provider.email}</span>
                  </div>
                )}

                {selectedEnrollment.provider.address && (
                  <div className="contact-row">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                      <circle cx="12" cy="10" r="3" />
                    </svg>
                    <span>{selectedEnrollment.provider.address}</span>
                  </div>
                )}
              </div>

              {/* Enrollment Info Section */}
              <div className="enrollment-modal-section">
                <h3>
                  <svg className="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="16" y1="13" x2="8" y2="13" />
                    <line x1="16" y1="17" x2="8" y2="17" />
                    <polyline points="10 9 9 9 8 9" />
                  </svg>
                  Enrollment Info
                </h3>
                
                <div className="contact-row">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                    <line x1="16" y1="2" x2="16" y2="6" />
                    <line x1="8" y1="2" x2="8" y2="6" />
                    <line x1="3" y1="10" x2="21" y2="10" />
                  </svg>
                  <span><strong>Enrolled Since:</strong> {formatDate(selectedEnrollment.enrolled_at)}</span>
                </div>
                {/*<div className="contact-row">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                  <span><strong>ID:</strong> {selectedEnrollment.enrollment_id}</span>
                </div>*/}
              </div>

              {/* Batches Section */}
              <div className="enrollment-modal-section">
                <h3>
                  <svg className="section-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                  </svg>
                  Your Batches
                </h3>
                {selectedEnrollment.batches.length > 0 ? (
                  selectedEnrollment.batches.map((batch) => (
                    <div className="modal-batch" key={batch.batch_id}>
                      <strong>{batch.name}</strong>
                      <span>
                        {formatDate(batch.start_date)} – {formatDate(batch.end_date)}
                      </span>
                    </div>
                  ))
                ) : (
                  <p className="no-batches">No batches assigned yet.</p>
                )}
              </div>

            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MyInstitutesPage;