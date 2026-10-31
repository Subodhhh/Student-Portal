from locust import HttpUser, task, between
import uuid

class InternalServicesLoad(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        # The API key you defined in your .env file (e.g., SERVICE_API_KEYS)
        self.headers = {
            "X-Internal-Api-Key": "YOUR_INTERNAL_API_KEY_HERE",
            "Content-Type": "application/json"
        }
        
        # REAL UUIDs
        self.staff_id = "00000000-0000-0000-0000-000000000000"
        self.session_id = "PASTE_A_REAL_SESSION_ID" 

    # Institute Identity: Port 8000 (or whatever port is exposed)
    @task(5)
    def introspect_session(self):
        """POST /internal/v1/introspect (Port 8000)"""
        payload = {
            "session_id": self.session_id,
            "method": "GET",
            "csrf_token": ""
        }
        self.client.post("http://localhost:8000/internal/v1/introspect", json=payload, headers=self.headers)

    # Institute Academics: Port 8000 (map to correct host port)
    @task(2)
    def retire_teaching(self):
        """POST /internal/v1/teaching/retire"""
        payload = {"staff_id": self.staff_id, "reason": "Load test retirement"}
        self.client.post("http://localhost:800X/internal/v1/teaching/retire", json=payload, headers=self.headers)

    # Institute Enrollment: Port 8000
    @task(2)
    def sync_reference(self):
        """POST /internal/v1/reference-sync"""
        payload = {"entity": "branch", "id": str(uuid.uuid4())}
        self.client.post("http://localhost:800X/internal/v1/reference-sync", json=payload, headers=self.headers)

    # Institute Identity: Port 8000
    @task(2)
    def push_audit_event(self):
        """POST /internal/v1/audit/events"""
        payload = {"action": "test.load_test", "status": "SUCCESS"}
        self.client.post("http://localhost:8000/internal/v1/audit/events", json=payload, headers=self.headers)

    # Institute Operations: Port 8000
    @task(2)
    def create_internal_notification(self):
        """POST /internal/v1/notifications"""
        payload = {"recipient_id": self.staff_id, "title": "Load Test", "message": "Testing..."}
        self.client.post("http://localhost:800X/internal/v1/notifications", json=payload, headers=self.headers)