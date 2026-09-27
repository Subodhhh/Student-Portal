import PageHeader from "../../components/PageHeader";
import "../../styles/notifications.css";

type Notification = {
  id: number;
  title: string;
  message: string;
  time: string;
  unread: boolean;
};

const notifications: Notification[] = [
  {
    id: 1,
    title: "New Test Scheduled",
    message: "NEET Full Mock Test 02 has been scheduled for your batch.",
    time: "Today, 10:30 AM",
    unread: true,
  },
  {
    id: 2,
    title: "Test Result Available",
    message: "Your result for NEET Full Mock Test 01 is now available.",
    time: "Yesterday, 6:15 PM",
    unread: true,
  },
  {
    id: 3,
    title: "Test Reminder",
    message: "Your upcoming test starts tomorrow at 10:00 AM.",
    time: "Yesterday, 9:00 AM",
    unread: false,
  },
];

function NotificationsPage() {
  return (
    <div className="notifications-page">
      <PageHeader
        title="Notifications"
        description="View your latest test and account notifications."
      />

      <section className="notifications-card">
        <div className="notifications-header">
          <h2>All Notifications</h2>
          <button type="button">Mark all as read</button>
        </div>

        <div className="notifications-list">
          {notifications.map((notification) => (
            <article
              key={notification.id}
              className={`notification-item ${
                notification.unread ? "unread" : ""
              }`}
            >
              <div className="notification-dot" />

              <div className="notification-content">
                <h3>{notification.title}</h3>
                <p>{notification.message}</p>
                <span>{notification.time}</span>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

export default NotificationsPage;