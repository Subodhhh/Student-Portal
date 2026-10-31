from locust import HttpUser, task, between
import uuid
import random
import time

class InstitutePostLoad(HttpUser):
    # We need to wait a bit longer between POSTs to avoid overwhelming the DB
    wait_time = between(2, 5)

    def on_start(self):
        # 1. YOUR FRESH SUPER ADMIN COOKIE
        self.client.cookies.set("__Host-inst_sid", "PASTE_YOUR_FRESH_COOKIE_HERE")
        
        # 2. Headers (CSRF is REQUIRED for POST requests)
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "PASTE_YOUR_CSRF_TOKEN_HERE" 
        }
        
        # 3. REAL UUIDs FOR TESTING (Replace these!)
        self.real_provider_id = "00000000-0000-0000-0000-000000000000"
        self.real_staff_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # 1. HIGH VOLUME, SAFE ENDPOINTS (Forgot Password)
    # -------------------------------------------------------------------
    @task(5)
    def forgot_password(self):
        """Simulates users requesting a password reset"""
        # Generates a random email so it doesn't rate-limit a single account
        random_email = f"user_{uuid.uuid4().hex[:6]}@example.com"
        payload = {"identifier": random_email}
        
        # Note: Your API returns 200 even if the email doesn't exist (to prevent enumeration)
        self.client.post("/api/v1/auth/forgot-password", json=payload, headers=self.headers, name="/api/v1/auth/forgot-password")

    # -------------------------------------------------------------------
    # 2. CREATE ENDPOINTS (Staff & Assignments)
    # -------------------------------------------------------------------
    @task(3)
    def create_new_staff(self):
        """Creates a new staff member with a random email"""
        random_id = uuid.uuid4().hex[:8]
        payload = {
            "full_name": f"Load Test {random_id}",
            "email": f"load_{random_id}@example.com",
            "assignment": {
                "role": "TEACHER",
                "provider_id": self.real_provider_id
            }
        }
        # catch_response=True allows us to mark 409 Conflicts as "success" (since it means the DB is protecting against duplicates)
        with self.client.post("/api/v1/staff", json=payload, headers=self.headers, catch_response=True, name="/api/v1/staff (Create)") as response:
            if response.status_code == 201:
                response.success()
            elif response.status_code == 409:
                response.success() # Duplicate email, but DB worked!
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"Failed: {response.status_code}")

    @task(2)
    def add_assignment(self):
        """Adds a role to an existing staff member"""
        payload = {
            "role": "BRANCH_ADMIN",
            "provider_id": self.real_provider_id,
            # You need a real branch_id here
            "branch_id": "PUT_A_REAL_BRANCH_UUID_HERE" 
        }
        self.client.post(f"/api/v1/staff/{self.real_staff_id}/assignments", json=payload, headers=self.headers, name="/api/v1/staff/{id}/assignments")

    # -------------------------------------------------------------------
    # 3. STATE MUTATIONS (Deactivate / Reactivate)
    # -------------------------------------------------------------------
    @task(1)
    def deactivate_staff(self):
        """Deactivates a staff member. (Will fail after the first success)"""
        payload = {"reason": "Load test deactivation"}
        self.client.post(f"/api/v1/staff/{self.real_staff_id}/deactivate", json=payload, headers=self.headers, name="/api/v1/staff/{id}/deactivate")

    @task(1)
    def reactivate_staff(self):
        """Reactivates a staff member. (Will fail if they are already active)"""
        payload = {"reason": "Load test reactivation"}
        self.client.post(f"/api/v1/staff/{self.real_staff_id}/reactivate", json=payload, headers=self.headers, name="/api/v1/staff/{id}/reactivate")

    # -------------------------------------------------------------------
    # 4. DESTRUCTIVE ENDPOINTS (Logout & Change Password)
    # -------------------------------------------------------------------
    # Note: We give these a very low weight so they don't destroy the session instantly.
    
    @task(1)
    def resend_verification(self):
        self.client.post(f"/api/v1/staff/{self.real_staff_id}/resend-verification", json={}, headers=self.headers, name="/api/v1/staff/{id}/resend-verification")

    @task(1)
    def logout(self):
        """Logs the user out. The next request will fail with 401!"""
        with self.client.post("/api/v1/auth/logout", json={}, headers=self.headers, catch_response=True, name="/api/v1/auth/logout") as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                # We are already logged out (the session was destroyed by a previous request)
                response.success()
            else:
                response.failure(f"Logout failed: {response.status_code}")