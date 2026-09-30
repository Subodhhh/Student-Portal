import { useEffect, useState } from "react";
import PageHeader from "../../components/PageHeader";
import { PROFILE_API_URL } from "../../services/api";
import "../../styles/profile.css";

type StudentProfile = {
  student_id: string;
  first_name: string;
  last_name: string;
  date_of_birth: string | null;
  phone: string | null;
  email: string;
};

function ProfilePage() {
  const [profile, setProfile] = useState<StudentProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const [formData, setFormData] = useState({
    first_name: "",
    last_name: "",
    date_of_birth: "",
    phone: "",
    email: "",
  });

  useEffect(() => {
    loadProfile();
  }, []);

  const loadProfile = async () => {
    try {
      const response = await fetch(`${PROFILE_API_URL}/profile`, {
        credentials: "include",
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "Failed to load profile");
      }

      const data: StudentProfile = await response.json();

      setProfile(data);

      setFormData({
        first_name: data.first_name,
        last_name: data.last_name,
        date_of_birth: data.date_of_birth || "",
        phone: data.phone || "",
        email: data.email,
      });
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Failed to load profile"
      );
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const { name, value } = event.target;

    setFormData((previous) => ({
      ...previous,
      [name]: value,
    }));
  };

  const handleEdit = () => {
    if (!profile) {
      return;
    }

    setFormData({
      first_name: profile.first_name,
      last_name: profile.last_name,
      date_of_birth: profile.date_of_birth || "",
      phone: profile.phone || "",
      email: profile.email,
    });

    setEditing(true);
    setError("");
  };

  const handleCancel = () => {
    if (!profile) {
      return;
    }

    setFormData({
      first_name: profile.first_name,
      last_name: profile.last_name,
      date_of_birth: profile.date_of_birth || "",
      phone: profile.phone || "",
      email: profile.email,
    });

    setEditing(false);
    setError("");
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");

    try {
      const response = await fetch(`${PROFILE_API_URL}/profile`, {
        method: "PUT",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          first_name: formData.first_name,
          last_name: formData.last_name,
          date_of_birth: formData.date_of_birth || null,
          phone: formData.phone || null,
          email: formData.email,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Failed to update profile");
      }

      setProfile(data);
      setEditing(false);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Failed to update profile"
      );
    } finally {
      setSaving(false);
    }
  };

  const formatDate = (date: string | null) => {
    if (!date) {
      return "Not provided";
    }

    return new Date(date).toLocaleDateString("en-IN", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  };

  if (loading) {
    return (
      <div className="profile-page">
        <PageHeader
          title="Profile"
          description="View and manage your student account information."
        />

        <section className="profile-card">
          <p>Loading profile...</p>
        </section>
      </div>
    );
  }

  if (error && !profile) {
    return (
      <div className="profile-page">
        <PageHeader
          title="Profile"
          description="View and manage your student account information."
        />

        <section className="profile-card">
          <p>Unable to load your profile.</p>
          <p>{error}</p>
        </section>
      </div>
    );
  }

  if (!profile) {
    return null;
  }

  const fullName = `${profile.first_name} ${profile.last_name}`;

  const avatarLetter = profile.first_name
    ? profile.first_name.charAt(0).toUpperCase()
    : "S";

  return (
    <div className="profile-page">
      <PageHeader
        title="Profile"
        description="View and manage your student account information."
      />

      <section className="profile-card">
        <div className="profile-card-header">
          <div className="profile-avatar">{avatarLetter}</div>

          <div>
            <h2>{fullName}</h2>
            <p>Student ID: {profile.student_id}</p>
          </div>

          {!editing && (
            <button onClick={handleEdit}>
              Edit Profile
            </button>
          )}
        </div>

        <div className="profile-divider" />

        {editing ? (
          <div className="profile-details">
            <div className="profile-field">
              <span>First Name</span>
              <input
                type="text"
                name="first_name"
                value={formData.first_name}
                onChange={handleChange}
              />
            </div>

            <div className="profile-field">
              <span>Last Name</span>
              <input
                type="text"
                name="last_name"
                value={formData.last_name}
                onChange={handleChange}
              />
            </div>

            <div className="profile-field">
              <span>Email</span>
              <input
                type="email"
                name="email"
                value={formData.email}
                onChange={handleChange}
              />
            </div>

            <div className="profile-field">
              <span>Mobile Number</span>
              <input
                type="tel"
                name="phone"
                value={formData.phone}
                onChange={handleChange}
              />
            </div>

            <div className="profile-field">
              <span>Date of Birth</span>
              <input
                type="date"
                name="date_of_birth"
                value={formData.date_of_birth}
                onChange={handleChange}
              />
            </div>

            {error && (
              <p className="profile-error">
                {error}
              </p>
            )}

            <div className="profile-actions">
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? "Saving..." : "Save Changes"}
              </button>

              <button
                type="button"
                onClick={handleCancel}
                disabled={saving}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="profile-details">
            <div className="profile-field">
              <span>Full Name</span>
              <strong>{fullName}</strong>
            </div>

            <div className="profile-field">
              <span>Student ID</span>
              <strong>{profile.student_id}</strong>
            </div>

            <div className="profile-field">
              <span>Email</span>
              <strong>{profile.email}</strong>
            </div>

            <div className="profile-field">
              <span>Mobile Number</span>
              <strong>{profile.phone || "Not provided"}</strong>
            </div>

            <div className="profile-field">
              <span>Date of Birth</span>
              <strong>
                {formatDate(profile.date_of_birth)}
              </strong>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default ProfilePage;