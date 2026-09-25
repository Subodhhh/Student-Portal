import PageHeader from "../../components/PageHeader";
import "../../styles/profile.css";

function ProfilePage() {
  return (
    <div className="profile-page">
      <PageHeader
        title="Profile"
        description="View and manage your student account information."
      />

      <section className="profile-card">
        <div className="profile-card-header">
          <div className="profile-avatar">S</div>

          <div>
            <h2>Student</h2>
            <p>Student ID: STU001</p>
          </div>

          <button>Edit Profile</button>
        </div>

        <div className="profile-divider" />

        <div className="profile-details">
          <div className="profile-field">
            <span>Full Name</span>
            <strong>Student</strong>
          </div>

          <div className="profile-field">
            <span>Student ID</span>
            <strong>STU001</strong>
          </div>

          <div className="profile-field">
            <span>Email</span>
            <strong>student@example.com</strong>
          </div>

          <div className="profile-field">
            <span>Mobile Number</span>
            <strong>+91 98765 43210</strong>
          </div>

          <div className="profile-field">
            <span>Date of Birth</span>
            <strong>15 June 2005</strong>
          </div>

          <div className="profile-field">
            <span>Gender</span>
            <strong>Male</strong>
          </div>
        </div>
      </section>
    </div>
  );
}

export default ProfilePage;