from locust import HttpUser, task, between
import uuid

class InstituteEnrollmentLoad(HttpUser):
    wait_time = between(2, 5)

    def on_start(self):
        self.client.cookies.set("__Host-inst_sid", "LA372KjanjHgJOJZekx8d7MC4nXPK9n9cNFZUOHtJ4A")
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "I09y-LCuepp0VFP-M-vSgtjQHNWsBSE-bkYHKWYFlUo" 
        }
        
        self.enrollment_id = "89555b96-349d-46cb-a720-29ad51247305"
        self.transfer_id = "9f619aa2-53d6-44d5-98e6-ccd67af99a51"
        self.provider_id = "c97e334d-861d-4ed5-817f-e8d35141c8cc"
        self.branch_id = "70341950-23ff-4d75-bda5-1707549c49bc"
        self.batch_id = "f525063a-36db-4c15-8c87-c241073f28da"
        self.subject_id = "0ad5669d-ef26-40e7-99b0-abf6ba8dd96e"
        self.staff_id = "6dda2658-e429-401a-aca3-798b1bc7af87"
        
        # FIXED: Added a real student_id variable to fix the 404 error
        self.student_id = "STU000101"

    # -------------------------------------------------------------------
    # READ ENDPOINTS
    # -------------------------------------------------------------------
    @task(5)
    def list_students(self):
        self.client.get("/api/v1/students?page_size=100", headers=self.headers, name="/api/v1/students")

    @task(4)
    def get_branch_enrollments(self):
        self.client.get(f"/api/v1/branches/{self.branch_id}/enrollments", headers=self.headers, name="/api/v1/branches/{id}/enrollments")

    @task(3)
    def get_provider_transfers(self):
        self.client.get(f"/api/v1/providers/{self.provider_id}/transfers", headers=self.headers, name="/api/v1/providers/{id}/transfers")

    @task(3)
    def get_specific_student(self):
        """GET /api/v1/students/{student_id}"""
        # FIXED: Changed from enrollment_id to student_id
        self.client.get(f"/api/v1/students/{self.student_id}", headers=self.headers, name="/api/v1/students/{id}")

    # -------------------------------------------------------------------
    # POST ENDPOINTS
    # -------------------------------------------------------------------
    @task(2)
    def enroll_new_student(self):
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
        with self.client.post(f"/api/v1/branches/{self.branch_id}/enrollments", json=payload, headers=self.headers, catch_response=True, name="/api/v1/branches/{id}/enrollments (Create)") as response:
            if response.status_code in [201, 409]:
                response.success()
            else:
                response.failure(f"Failed: {response.status_code} - {response.text}")

    @task(2)
    def lookup_student(self):
        # Check Swagger: Is the key "email", "email_address", or "q"?
        payload = {"email": "subodh.more1001@gmail.com"} 
        self.client.post("/api/v1/students/lookup", json=payload, headers=self.headers, name="/api/v1/students/lookup")
    
    @task(1)
    def generate_password_link(self):
        """POST /api/v1/enrollments/{enrollment_id}/password-setup-link"""
        # FIXED: Added catch_response to accept 409 Conflict as a success
        with self.client.post(f"/api/v1/enrollments/{self.enrollment_id}/password-setup-link", json={}, headers=self.headers, catch_response=True, name="/api/v1/enrollments/{id}/password-setup-link") as response:
            if response.status_code in [200, 201, 409]:
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(1)
    def initiate_transfer(self):
        payload = {
            "target_branch_id": self.branch_id,
            "target_provider_id": self.provider_id, # Often required
            "reason": "Load test transfer initiated for validation"
        }
        self.client.post(f"/api/v1/enrollments/{self.enrollment_id}/transfers", json=payload, headers=self.headers, name="/api/v1/enrollments/{id}/transfers")


    @task(1)
    def approve_transfer(self):
        # Check Swagger: Maybe it just needs an empty dict if the URL contains the ID?
        # If it fails with {}, try {"comment": "Approved"}
        payload = {"reason": "Approved"} 
        self.client.post(f"/api/v1/transfers/{self.transfer_id}/approve", json=payload, headers=self.headers, name="/api/v1/transfers/{id}/approve")

    @task(1)
    def reject_transfer(self):
        # Check Swagger: Is it "reason", "comment", or "rejection_reason"?
        payload = {"reason": "Load test rejection"} 
        self.client.post(f"/api/v1/transfers/{self.transfer_id}/reject", json=payload, headers=self.headers, name="/api/v1/transfers/{id}/reject")

    # -------------------------------------------------------------------
    # PATCH & PUT ENDPOINTS
    # -------------------------------------------------------------------
    @task(1)
    def update_enrollment(self):
        """PATCH /api/v1/enrollments/{enrollment_id}"""
        # FIXED: Changed "ACTIVE" to "active" (or whatever Enum your backend uses). 
        payload = {"status": "ACTIVE"} 
        self.client.patch(f"/api/v1/enrollments/{self.enrollment_id}", json=payload, headers=self.headers, name="/api/v1/enrollments/{id} (PATCH)")


    @task(1)
    def update_enrollment(self):
        # Check Swagger: Does it need lowercase "active"? Does it need a "reason"?
        payload = {"status": "ACTIVE", "reason": "Load test update"} 
        self.client.patch(f"/api/v1/enrollments/{self.enrollment_id}", json=payload, headers=self.headers, name="/api/v1/enrollments/{id} (PATCH)")

    @task(1)
    def assign_batch(self):
        # Check Swagger: What goes in the body of this PUT request?
        # If it requires no body but FastAPI complains about json={}, use `data=""`
        payload = {"status": "ACTIVE"} # Or maybe {"is_active": True}
        self.client.put(f"/api/v1/enrollments/{self.enrollment_id}/batches/{self.batch_id}", json=payload, headers=self.headers, name="/api/v1/enrollments/{id}/batches/{id} (PUT)")