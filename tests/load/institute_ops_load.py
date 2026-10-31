from locust import HttpUser, task, between
import uuid

class InstituteOperationsLoad(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        # 1. COOKIE & HEADERS
        self.client.cookies.set("__Host-inst_sid", "PASTE_YOUR_FRESH_COOKIE_HERE")
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "PASTE_YOUR_CSRF_TOKEN_HERE" 
        }
        
        # 2. REAL UUIDs (Replace these!)
        self.provider_id = "00000000-0000-0000-0000-000000000000"
        self.branch_id = "00000000-0000-0000-0000-000000000000"
        self.invoice_id = "00000000-0000-0000-0000-000000000000"
        self.ticket_id = "00000000-0000-0000-0000-000000000000"
        self.notification_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # HIGH VOLUME READS (What the browser polls constantly)
    # -------------------------------------------------------------------
    @task(6)
    def check_unread_notifications(self):
        """GET /api/v1/notifications/unread-count (Polled every few seconds by frontend)"""
        self.client.get("/api/v1/notifications/unread-count", headers=self.headers, name="/api/v1/notifications/unread-count")

    @task(5)
    def load_dashboard(self):
        """GET /api/v1/dashboard"""
        self.client.get("/api/v1/dashboard", headers=self.headers, name="/api/v1/dashboard")

    @task(4)
    def check_system_health(self):
        """GET /api/v1/system/health (The healthcheck endpoint)"""
        self.client.get("/api/v1/system/health", headers=self.headers, name="/api/v1/system/health")

    @task(4)
    def list_notifications(self):
        """GET /api/v1/notifications"""
        self.client.get("/api/v1/notifications?page_size=50", headers=self.headers, name="/api/v1/notifications")

    # -------------------------------------------------------------------
    # MODERATE READS
    # -------------------------------------------------------------------
    @task(3)
    def list_support_tickets(self):
        """GET /api/v1/support/tickets"""
        self.client.get("/api/v1/support/tickets", headers=self.headers, name="/api/v1/support/tickets")

    @task(3)
    def get_provider_subscription(self):
        """GET /api/v1/providers/{provider_id}/subscription"""
        self.client.get(f"/api/v1/providers/{self.provider_id}/subscription", headers=self.headers, name="/api/v1/providers/{id}/subscription")

    @task(2)
    def get_provider_invoices(self):
        """GET /api/v1/providers/{provider_id}/invoices"""
        self.client.get(f"/api/v1/providers/{self.provider_id}/invoices", headers=self.headers, name="/api/v1/providers/{id}/invoices")

    @task(2)
    def get_platform_settings(self):
        """GET /api/v1/settings/platform"""
        self.client.get("/api/v1/settings/platform", headers=self.headers, name="/api/v1/settings/platform")

    # -------------------------------------------------------------------
    # WRITE ENDPOINTS (Support & Notifications)
    # -------------------------------------------------------------------
    @task(2)
    def mark_all_notifications_read(self):
        """POST /api/v1/notifications/read-all"""
        self.client.post("/api/v1/notifications/read-all", json={}, headers=self.headers, name="/api/v1/notifications/read-all")

    @task(2)
    def create_support_ticket(self):
        """POST /api/v1/support/tickets"""
        payload = {
            "subject": f"Load Test Ticket {uuid.uuid4().hex[:4]}",
            "description": "This is a simulated load test ticket.",
            "priority": "LOW"
        }
        self.client.post("/api/v1/support/tickets", json=payload, headers=self.headers, name="/api/v1/support/tickets (Create)")

    @task(1)
    def reply_to_ticket(self):
        """POST /api/v1/support/tickets/{ticket_id}/messages"""
        payload = {"message": f"Simulated agent reply {uuid.uuid4().hex[:4]}"}
        self.client.post(f"/api/v1/support/tickets/{self.ticket_id}/messages", json=payload, headers=self.headers, name="/api/v1/support/tickets/{id}/messages")

    @task(1)
    def update_ticket_status(self):
        """PATCH /api/v1/support/tickets/{ticket_id}"""
        payload = {"status": "IN_PROGRESS"}
        self.client.patch(f"/api/v1/support/tickets/{self.ticket_id}", json=payload, headers=self.headers, name="/api/v1/support/tickets/{id} (PATCH)")