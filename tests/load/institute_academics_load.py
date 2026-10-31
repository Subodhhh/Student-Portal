from locust import HttpUser, task, between
import uuid

class InstituteAcademicsLoad(HttpUser):
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
        self.batch_id = "00000000-0000-0000-0000-000000000000"
        self.subject_id = "00000000-0000-0000-0000-000000000000"
        self.staff_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # GET ENDPOINTS (Reads)
    # -------------------------------------------------------------------
    @task(4)
    def list_subjects(self):
        """GET /api/v1/subjects"""
        self.client.get("/api/v1/subjects", headers=self.headers, name="/api/v1/subjects")

    @task(3)
    def get_branch_batches(self):
        """GET /api/v1/branches/{branch_id}/batches"""
        self.client.get(f"/api/v1/branches/{self.branch_id}/batches", headers=self.headers, name="/api/v1/branches/{id}/batches")

    @task(3)
    def get_batch_subjects(self):
        """GET /api/v1/batches/{batch_id}/subjects"""
        self.client.get(f"/api/v1/batches/{self.batch_id}/subjects", headers=self.headers, name="/api/v1/batches/{id}/subjects")

    @task(2)
    def list_teaching(self):
        """GET /api/v1/teaching"""
        self.client.get("/api/v1/teaching", headers=self.headers, name="/api/v1/teaching")

    @task(2)
    def get_provider_subjects(self):
        """GET /api/v1/providers/{provider_id}/subjects"""
        self.client.get(f"/api/v1/providers/{self.provider_id}/subjects", headers=self.headers, name="/api/v1/providers/{id}/subjects")

    # -------------------------------------------------------------------
    # POST ENDPOINTS (Creates - Generates unique names)
    # -------------------------------------------------------------------
    @task(2)
    def create_subject(self):
        """POST /api/v1/subjects"""
        random_code = f"SUB{uuid.uuid4().hex[:4].upper()}"
        payload = {
            "code": random_code,
            "name": f"Load Test Subject {random_code}"
        }
        self.client.post("/api/v1/subjects", json=payload, headers=self.headers, name="/api/v1/subjects (Create)")

    @task(2)
    def create_batch(self):
        """POST /api/v1/branches/{branch_id}/batches"""
        random_id = uuid.uuid4().hex[:4]
        payload = {"name": f"Load Test Batch {random_id}"}
        self.client.post(f"/api/v1/branches/{self.branch_id}/batches", json=payload, headers=self.headers, name="/api/v1/branches/{id}/batches (Create)")

    @task(1)
    def create_teaching_assignment(self):
        """POST /api/v1/teaching"""
        payload = {
            "staff_id": self.staff_id,
            "branch_id": self.branch_id,
            "subject_id": self.subject_id,
            "batch_id": self.batch_id
        }
        self.client.post("/api/v1/teaching", json=payload, headers=self.headers, name="/api/v1/teaching (Create)")

    # -------------------------------------------------------------------
    # PATCH & PUT ENDPOINTS (Updates & Mappings)
    # -------------------------------------------------------------------
    @task(1)
    def update_subject(self):
        """PATCH /api/v1/subjects/{subject_id}"""
        random_name = f"Updated Subject {uuid.uuid4().hex[:4]}"
        payload = {"name": random_name}
        self.client.patch(f"/api/v1/subjects/{self.subject_id}", json=payload, headers=self.headers, name="/api/v1/subjects/{id} (PATCH)")

    @task(1)
    def update_batch(self):
        """PATCH /api/v1/batches/{batch_id}"""
        random_name = f"Updated Batch {uuid.uuid4().hex[:4]}"
        payload = {"name": random_name}
        self.client.patch(f"/api/v1/batches/{self.batch_id}", json=payload, headers=self.headers, name="/api/v1/batches/{id} (PATCH)")

    @task(1)
    def map_subject_to_batch(self):
        """PUT /api/v1/batches/{batch_id}/subjects/{subject_id}"""
        # Assuming empty body or specific payload structure
        self.client.put(f"/api/v1/batches/{self.batch_id}/subjects/{self.subject_id}", json={}, headers=self.headers, name="/api/v1/batches/{id}/subjects/{id} (PUT)")

    @task(1)
    def map_subject_to_provider(self):
        """PUT /api/v1/providers/{provider_id}/subjects/{subject_id}"""
        self.client.put(f"/api/v1/providers/{self.provider_id}/subjects/{self.subject_id}", json={}, headers=self.headers, name="/api/v1/providers/{id}/subjects/{id} (PUT)")