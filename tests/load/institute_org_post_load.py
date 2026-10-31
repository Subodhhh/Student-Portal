from locust import HttpUser, task, between
import uuid

class InstituteOrganizationPostLoad(HttpUser):
    # Wait between 2 and 4 seconds to let the database breathe
    wait_time = between(2, 4)

    def on_start(self):
        # 1. YOUR FRESH SUPER ADMIN COOKIE
        self.client.cookies.set("__Host-inst_sid", "PASTE_YOUR_FRESH_COOKIE_HERE")
        
        # 2. Headers (CSRF is REQUIRED for POST requests)
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "PASTE_YOUR_CSRF_TOKEN_HERE" 
        }
        
        # 3. REAL UUIDs (Replace these with actual IDs from your database)
        self.provider_id = "00000000-0000-0000-0000-000000000000"
        self.branch_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # PROVIDER ENDPOINTS
    # -------------------------------------------------------------------
    @task(4)
    def create_provider(self):
        """POST /api/v1/providers"""
        random_id = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Load Test Provider {random_id}",
            "email": f"provider_{random_id}@example.com"
        }
        
        # We use catch_response to handle any unique constraint errors gracefully
        with self.client.post("/api/v1/providers", json=payload, headers=self.headers, catch_response=True, name="/api/v1/providers (Create)") as response:
            if response.status_code == 201:
                response.success()
            elif response.status_code == 409:
                response.success() # Handled by the DB (duplicate name/email)
            else:
                response.failure(f"Failed: {response.status_code}")

    @task(2)
    def deactivate_provider(self):
        """POST /api/v1/providers/{provider_id}/deactivate"""
        payload = {"reason": "Load test deactivation"}
        self.client.post(f"/api/v1/providers/{self.provider_id}/deactivate", json=payload, headers=self.headers, name="/api/v1/providers/{id}/deactivate")

    @task(2)
    def activate_provider(self):
        """POST /api/v1/providers/{provider_id}/activate"""
        payload = {"reason": "Load test activation"}
        self.client.post(f"/api/v1/providers/{self.provider_id}/activate", json=payload, headers=self.headers, name="/api/v1/providers/{id}/activate")

    # -------------------------------------------------------------------
    # BRANCH ENDPOINTS
    # -------------------------------------------------------------------
    @task(3)
    def create_branch(self):
        """POST /api/v1/providers/{provider_id}/branches"""
        random_id = uuid.uuid4().hex[:4]
        payload = {
            "name": f"Load Test Branch {random_id}",
            "city": "Mumbai",
            "state": "MH"
        }
        self.client.post(f"/api/v1/providers/{self.provider_id}/branches", json=payload, headers=self.headers, name="/api/v1/providers/{id}/branches (Create)")

    @task(2)
    def deactivate_branch(self):
        """POST /api/v1/branches/{branch_id}/deactivate"""
        payload = {"reason": "Load test deactivation"}
        self.client.post(f"/api/v1/branches/{self.branch_id}/deactivate", json=payload, headers=self.headers, name="/api/v1/branches/{id}/deactivate")

    @task(2)
    def activate_branch(self):
        """POST /api/v1/branches/{branch_id}/activate"""
        payload = {"reason": "Load test activation"}
        self.client.post(f"/api/v1/branches/{self.branch_id}/activate", json=payload, headers=self.headers, name="/api/v1/branches/{id}/activate")