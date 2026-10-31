from locust import HttpUser, task, between
import uuid

class InstituteOrganizationLoad(HttpUser):
    # Wait between 1 and 3 seconds between requests
    wait_time = between(1, 3)

    def on_start(self):
        # 1. YOUR FRESH SUPER ADMIN COOKIE
        self.client.cookies.set("__Host-inst_sid", "PASTE_YOUR_FRESH_COOKIE_HERE")
        
        # 2. Headers (CSRF is REQUIRED for PATCH requests)
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "PASTE_YOUR_CSRF_TOKEN_HERE" 
        }
        
        # 3. REAL UUIDs (Replace these with actual IDs from your database)
        self.provider_id = "00000000-0000-0000-0000-000000000000"
        self.branch_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # READ ENDPOINTS (Heavy load)
    # -------------------------------------------------------------------
    @task(5)
    def list_providers(self):
        """GET /api/v1/providers"""
        self.client.get("/api/v1/providers?page_size=100", headers=self.headers, name="/api/v1/providers")

    @task(4)
    def get_provider_branches(self):
        """GET /api/v1/providers/{provider_id}/branches"""
        self.client.get(f"/api/v1/providers/{self.provider_id}/branches", headers=self.headers, name="/api/v1/providers/{id}/branches")

    @task(3)
    def get_specific_provider(self):
        """GET /api/v1/providers/{provider_id}"""
        self.client.get(f"/api/v1/providers/{self.provider_id}", headers=self.headers, name="/api/v1/providers/{id}")

    @task(3)
    def get_specific_branch(self):
        """GET /api/v1/branches/{branch_id}"""
        self.client.get(f"/api/v1/branches/{self.branch_id}", headers=self.headers, name="/api/v1/branches/{id}")

    # -------------------------------------------------------------------
    # WRITE ENDPOINTS (Low load - mutates data)
    # -------------------------------------------------------------------
    @task(1)
    def update_provider_name(self):
        """PATCH /api/v1/providers/{provider_id}"""
        # We use a random name so we don't get 409 Conflicts if the DB requires unique names
        random_suffix = uuid.uuid4().hex[:4]
        payload = {"name": f"Load Test Provider {random_suffix}"}
        
        self.client.patch(
            f"/api/v1/providers/{self.provider_id}", 
            json=payload, 
            headers=self.headers, 
            name="/api/v1/providers/{id} (PATCH)"
        )

    @task(1)
    def update_branch_city(self):
        """PATCH /api/v1/branches/{branch_id}"""
        random_city = f"City_{uuid.uuid4().hex[:4]}"
        payload = {"city": random_city}
        
        self.client.patch(
            f"/api/v1/branches/{self.branch_id}", 
            json=payload, 
            headers=self.headers, 
            name="/api/v1/branches/{id} (PATCH)"
        )