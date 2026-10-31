from locust import HttpUser, task, between
import uuid

class InstituteEnrollmentLoad(HttpUser):
    # Enrollment writes are heavy, so we wait a bit longer
    wait_time = between(2, 5)

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
        self.enrollment_id = "00000000-0000-0000-0000-000000000000"
        self.batch_id = "00000000-0000-0000-0000-000000000000"
        self.transfer_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # READ ENDPOINTS (Heavy load)
    # -------------------------------------------------------------------
    @task(5)
    def list_students(self):
        """GET /api/v1/students"""
        self.client.get("/api/v1/students?page_size=100", headers=self.headers, name="/api/v1/students")

    @task(4)
    def get_branch_enrollments(self):
        """GET /api/v1/branches/{branch_id}/enrollments"""
        self.client.get(f"/api/v1/branches/{self.branch_id}/enrollments", headers=self.headers, name="/api/v1/branches/{id}/enrollments")

    @task(3)
    def get_provider_transfers(self):
        """GET /api/v1/providers/{provider_id}/transfers"""
        self.client.get(f"/api/v1/providers/{self.provider_id}/transfers", headers=self.headers, name="/api/v1/providers/{id}/transfers")

    @task(3)
    def get_specific_student(self):
        """GET /api/v1/students/{student_id}"""
        # Using a real student ID from your database
        self.client.get(f"/api/v1/students/{self.enrollment_id}", headers=self.headers, name="/api/v1/students/{id}")

    # -------------------------------------------------------------------
    # POST ENDPOINTS (Creates & Actions)
    # -------------------------------------------------------------------
    @task(2)
    def enroll_new_student(self):
        """POST /api/v1/branches/{branch_id}/enrollments"""
        random_id = uuid.uuid4().hex[:6]
        payload = {
            "new_student": {
                "first_name": f"Load_{random_id}",
                "last_name": "Test",
                "email": f"load_{random_id}@example.com"
            },
            "batch_ids": [self.batch_id],
            "academic": {
                "class_level": "11th",
                "academic_year": "2026-2027"
            }
        }
        # We use catch_response because creating a student calls the external Student Auth service
        with self.client.post(f"/api/v1/branches/{self.branch_id}/enrollments", json=payload, headers=self.headers, catch_response=True, name="/api/v1/branches/{id}/enrollments (Create)") as response:
            if response.status_code in [201, 409]:
                response.success()
            else:
                response.failure(f"Failed: {response.status_code}")

    @task(2)
    def lookup_student(self):
        """POST /api/v1/students/lookup"""
        payload = {"email": "test@example.com"}
        self.client.post("/api/v1/students/lookup", json=payload, headers=self.headers, name="/api/v1/students/lookup")

    @task(1)
    def generate_password_link(self):
        """POST /api/v1/enrollments/{enrollment_id}/password-setup-link"""
        self.client.post(f"/api/v1/enrollments/{self.enrollment_id}/password-setup-link", json={}, headers=self.headers, name="/api/v1/enrollments/{id}/password-setup-link")

    @task(1)
    def initiate_transfer(self):
        """POST /api/v1/enrollments/{enrollment_id}/transfers"""
        payload = {
            "target_branch_id": self.branch_id,
            "reason": "Load test transfer"
        }
        self.client.post(f"/api/v1/enrollments/{self.enrollment_id}/transfers", json=payload, headers=self.headers, name="/api/v1/enrollments/{id}/transfers")

    @task(1)
    def approve_transfer(self):
        """POST /api/v1/transfers/{transfer_id}/approve"""
        self.client.post(f"/api/v1/transfers/{self.transfer_id}/approve", json={}, headers=self.headers, name="/api/v1/transfers/{id}/approve")

    @task(1)
    def reject_transfer(self):
        """POST /api/v1/transfers/{transfer_id}/reject"""
        payload = {"reason": "Load test rejection"}
        self.client.post(f"/api/v1/transfers/{self.transfer_id}/reject", json=payload, headers=self.headers, name="/api/v1/transfers/{id}/reject")

    # -------------------------------------------------------------------
    # PATCH & PUT ENDPOINTS
    # -------------------------------------------------------------------
    @task(1)
    def update_enrollment(self):
        """PATCH /api/v1/enrollments/{enrollment_id}"""
        payload = {"status": "ACTIVE"}
        self.client.patch(f"/api/v1/enrollments/{self.enrollment_id}", json=payload, headers=self.headers, name="/api/v1/enrollments/{id} (PATCH)")

    @task(1)
    def update_academic_profile(self):
        """PATCH /api/v1/enrollments/{enrollment_id}/academic-profile"""
        payload = {
            "class_level": "12th",
            "academic_year": "2026-2027"
        }
        self.client.patch(f"/api/v1/enrollments/{self.enrollment_id}/academic-profile", json=payload, headers=self.headers, name="/api/v1/enrollments/{id}/academic-profile (PATCH)")

    @task(1)
    def assign_batch(self):
        """PUT /api/v1/enrollments/{enrollment_id}/batches/{batch_id}"""
        self.client.put(f"/api/v1/enrollments/{self.enrollment_id}/batches/{self.batch_id}", json={}, headers=self.headers, name="/api/v1/enrollments/{id}/batches/{id} (PUT)")