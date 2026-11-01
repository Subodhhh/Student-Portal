from locust import HttpUser, task, between

class InstituteIdentityGetLoad(HttpUser):
    # Wait between 1 and 3 seconds between requests
    wait_time = between(1, 3)

    def on_start(self):
        # 1. YOUR FRESH SESSION COOKIE
        self.client.cookies.set("__Host-inst_sid", "XI1LKFDlNIjasvfN25bG3OaCoii3mdBOInvlmjyuMaw")
        
        # 2. Headers (Origin is required for CORS/CSRF checks)
        self.headers = {
            "Origin": "http://localhost:8081",
            "Accept": "application/json"
        }
        
        # 3. REAL STAFF UUID
        # Replace with an actual staff UUID from your database
        self.real_staff_id = "7caa8f88-28e4-4423-bcd0-b2e4e27e6973"

    @task(5)
    def get_my_profile(self):
        """GET /api/v1/auth/me"""
        self.client.get("/api/v1/auth/me", headers=self.headers, name="/api/v1/auth/me")

    @task(4)
    def list_all_staff(self):
        """GET /api/v1/staff"""
        self.client.get("/api/v1/staff", headers=self.headers, name="/api/v1/staff")

    @task(3)
    def view_audit_logs(self):
        """GET /api/v1/audit-logs"""
        self.client.get("/api/v1/audit-logs?page_size=50", headers=self.headers, name="/api/v1/audit-logs")

    @task(2)
    def staff_lookup(self):
        """GET /api/v1/staff/lookup"""
        # MUST pass the 'email' parameter, as defined in the FastAPI backend
        params = {
            "email": subodh.more1001@gmail.com  # <-- REPLACE with a REAL email from your database
        }
        
        self.client.get(
            "/api/v1/staff/lookup", 
            params=params, # <-- This tells Locust to append ?email=...
            headers=self.headers, 
            name="/api/v1/staff/lookup"
        )

    @task(2)
    def get_specific_staff(self):
        """GET /api/v1/staff/{staff_id}"""
        self.client.get(f"/api/v1/staff/{self.real_staff_id}", headers=self.headers, name="/api/v1/staff/{id}")