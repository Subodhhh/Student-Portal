import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { NOTIFICATION_API_URL } from "../../services/api";

import "../../styles/notifications.css";

type NotificationMetadata = {
  test_id?: string;
  attempt_id?: string;
};

type Notification = {
  notification_id: string;
  event_type: string;
  title: string;
  message: string;
  created_at: string;
  metadata: NotificationMetadata | null;
  is_read: boolean;
  notification_recipient_id: string;
};

function NotificationsPage() {
  const navigate = useNavigate();

  const [notifications, setNotifications] = useState<
    Notification[]
  >([]);

  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchNotifications = async () => {
      try {
        const response = await fetch(
          `${NOTIFICATION_API_URL}/notifications`,
          {
            credentials: "include",
          }
        );

        if (!response.ok) {
          throw new Error(
            `Failed to fetch notifications: ${response.status}`
          );
        }

        const data = await response.json();

        const formattedNotifications: Notification[] =
          data.map((notification: any) => ({
            notification_id: notification.notification_id,
            event_type: notification.event_type,
            title: notification.title,
            message: notification.message,
            created_at: notification.created_at,
            metadata: notification.metadata,
            is_read: notification.recipient.is_read,
            notification_recipient_id:
              notification.recipient
                .notification_recipient_id,
          }));

        setNotifications(formattedNotifications);
      } catch (error) {
        console.error(
          "Failed to fetch notifications:",
          error
        );
      } finally {
        setLoading(false);
      }
    };

    fetchNotifications();
  }, []);

  async function markAsRead(
    notificationRecipientId: string
  ): Promise<boolean> {
    try {
      const response = await fetch(
        `${NOTIFICATION_API_URL}/notifications/${notificationRecipientId}/read`,
        {
          method: "PATCH",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error(
          `Failed to mark notification as read: ${response.status}`
        );
      }

      setNotifications((current) =>
        current.map((notification) =>
          notification.notification_recipient_id ===
          notificationRecipientId
            ? {
                ...notification,
                is_read: true,
              }
            : notification
        )
      );

      return true;
    } catch (error) {
      console.error(
        "Failed to mark notification as read:",
        error
      );

      return false;
    }
  }

  async function handleNotificationClick(
    notification: Notification
  ) {
    /*
     * Mark the notification as read first.
     */
    if (!notification.is_read) {
      const markedAsRead = await markAsRead(
        notification.notification_recipient_id
      );

      if (!markedAsRead) {
        return;
      }
    }

    const testId = notification.metadata?.test_id;
    const attemptId = notification.metadata?.attempt_id;

    /*
     * Navigate based on notification event type.
     */
    switch (notification.event_type) {
      case "TEST_SCHEDULED":
      case "TEST_REMINDER":
        if (testId) {
          navigate(`/tests/${testId}`);
        }
        break;

      case "TEST_STARTED":
        if (testId) {
          navigate(`/tests/${testId}/attempt`);
        }
        break;

      case "TEST_SUBMITTED":
      case "TEST_TIMEOUT":
        if (attemptId) {
          navigate(`/tests/result/${attemptId}`);
        } else if (testId) {
          navigate(`/tests/${testId}/performance`);
        }
        break;

      case "RESULT_PUBLISHED":
        if (testId) {
          navigate(`/tests/${testId}/performance`);
        } else {
          navigate("/results");
        }
        break;

      case "RANKING_UPDATED":
        navigate("/ranking");
        break;

      case "SYSTEM_ANNOUNCEMENT":
      case "SYSTEM_MAINTENANCE":
        /*
         * No dedicated announcement/maintenance page exists yet.
         * Keep the student on the Notifications page.
         */
        break;

      default:
        break;
    }
  }

  async function markAllAsRead() {
    try {
      const response = await fetch(
        `${NOTIFICATION_API_URL}/notifications/read-all`,
        {
          method: "PATCH",
          credentials: "include",
        }
      );

      if (!response.ok) {
        throw new Error(
          `Failed to mark all notifications as read: ${response.status}`
        );
      }

      setNotifications((current) =>
        current.map((notification) => ({
          ...notification,
          is_read: true,
        }))
      );
    } catch (error) {
      console.error(
        "Failed to mark all notifications as read:",
        error
      );
    }
  }

  function formatNotificationTime(
    createdAt: string
  ) {
    return new Date(createdAt).toLocaleString(
      "en-IN",
      {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }
    );
  }

  const hasUnreadNotifications = notifications.some(
    (notification) => !notification.is_read
  );

  return (
    <div className="notifications-page">
      <section className="notifications-card">
        <div className="notifications-header">
          <h2>All Notifications</h2>

          <button
            type="button"
            onClick={markAllAsRead}
            disabled={!hasUnreadNotifications}
          >
            Mark all as read
          </button>
        </div>

        <div className="notifications-list">
          {loading ? (
            <p>Loading notifications...</p>
          ) : notifications.length === 0 ? (
            <p>No notifications available.</p>
          ) : (
            notifications.map((notification) => (
              <article
                key={
                  notification.notification_recipient_id
                }
                className={`notification-item ${
                  !notification.is_read
                    ? "unread"
                    : ""
                }`}
                onClick={() =>
                  handleNotificationClick(
                    notification
                  )
                }
              >
                <div className="notification-dot" />

                <div className="notification-content">
                  <h3>{notification.title}</h3>

                  <p>{notification.message}</p>

                  <span>
                    {formatNotificationTime(
                      notification.created_at
                    )}
                  </span>
                </div>
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

export default NotificationsPage;