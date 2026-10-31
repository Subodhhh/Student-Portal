from locust import HttpUser, task, between
import uuid

class InstituteExamsLoad(HttpUser):
    # We wait longer here because exam queries are massive
    wait_time = between(2, 5)

    def on_start(self):
        # 1. COOKIE & HEADERS
        self.client.cookies.set("__Host-inst_sid", "PASTE_YOUR_FRESH_COOKIE_HERE")
        self.headers = {
            "Origin": "http://localhost:8081",
            "Content-Type": "application/json",
            "X-CSRF-Token": "PASTE_YOUR_CSRF_TOKEN_HERE" 
        }
        
        # 2. REAL UUIDs (Replace these with actual IDs!)
        self.branch_id = "00000000-0000-0000-0000-000000000000"
        self.exam_id = "00000000-0000-0000-0000-000000000000"
        self.plan_id = "00000000-0000-0000-0000-000000000000"
        self.occurrence_id = "00000000-0000-0000-0000-000000000000"
        self.global_test_id = "00000000-0000-0000-0000-000000000000"
        self.student_id = "STU00000001"

    # -------------------------------------------------------------------
    # HIGH-PRIORITY READS (Dashboard & Listing)
    # -------------------------------------------------------------------
    @task(5)
    def list_scheduled_tests(self):
        """GET /api/v1/tests"""
        self.client.get("/api/v1/tests?page_size=50", headers=self.headers, name="/api/v1/tests")

    @task(4)
    def list_branch_exams(self):
        """GET /api/v1/branches/{branch_id}/exams"""
        self.client.get(f"/api/v1/branches/{self.branch_id}/exams", headers=self.headers, name="/api/v1/branches/{id}/exams")

    @task(4)
    def list_exam_plans(self):
        """GET /api/v1/branches/{branch_id}/exam-plans"""
        self.client.get(f"/api/v1/branches/{self.branch_id}/exam-plans", headers=self.headers, name="/api/v1/branches/{id}/exam-plans")

    @task(3)
    def list_global_tests(self):
        """GET /api/v1/global-tests"""
        self.client.get("/api/v1/global-tests", headers=self.headers, name="/api/v1/global-tests")

    @task(3)
    def list_test_series(self):
        """GET /api/v1/test-series"""
        self.client.get("/api/v1/test-series", headers=self.headers, name="/api/v1/test-series")

    # -------------------------------------------------------------------
    # DEEP-DIVE READS (Heavy Database Joins)
    # -------------------------------------------------------------------
    @task(3)
    def get_exam_details(self):
        """GET /api/v1/exams/{exam_id}"""
        self.client.get(f"/api/v1/exams/{self.exam_id}", headers=self.headers, name="/api/v1/exams/{id}")

    @task(2)
    def get_exam_assignments(self):
        """GET /api/v1/exams/{exam_id}/assignments"""
        self.client.get(f"/api/v1/exams/{self.exam_id}/assignments", headers=self.headers, name="/api/v1/exams/{id}/assignments")

    @task(2)
    def get_exam_results(self):
        """GET /api/v1/exams/{exam_id}/results"""
        self.client.get(f"/api/v1/exams/{self.exam_id}/results", headers=self.headers, name="/api/v1/exams/{id}/results")

    @task(2)
    def get_global_occurrence_papers(self):
        """GET /api/v1/global-occurrences/{occurrence_id}/papers"""
        self.client.get(f"/api/v1/global-occurrences/{self.occurrence_id}/papers", headers=self.headers, name="/api/v1/global-occurrences/{id}/papers")

    # -------------------------------------------------------------------
    # CREATE & ACTION ENDPOINTS (Heavy POSTs)
    # -------------------------------------------------------------------
    @task(1)
    def check_exam_audience(self):
        """POST /api/v1/branches/{branch_id}/exam-plans/check"""
        payload = {
            "audience_type": "BRANCH",
            "batch_ids": [],
            "student_ids": []
        }
        self.client.post(f"/api/v1/branches/{self.branch_id}/exam-plans/check", json=payload, headers=self.headers, name="/api/v1/branches/{id}/exam-plans/check")

    @task(1)
    def preview_audience(self):
        """POST /api/v1/branches/{branch_id}/audience-preview"""
        payload = {
            "batch_ids": [],
            "student_ids": [self.student_id]
        }
        self.client.post(f"/api/v1/branches/{self.branch_id}/audience-preview", json=payload, headers=self.headers, name="/api/v1/branches/{id}/audience-preview")

    @task(1)
    def sync_exam_results(self):
        """POST /api/v1/exams/{exam_id}/sync"""
        # This forces the system to reach out to the Student Result service to pull grades
        self.client.post(f"/api/v1/exams/{self.exam_id}/sync", json={}, headers=self.headers, name="/api/v1/exams/{id}/sync")

    @task(1)
    def update_exam_plan(self):
        """PATCH /api/v1/exam-plans/{plan_id}"""
        payload = {"name": f"Load Test Plan {uuid.uuid4().hex[:4]}"}
        self.client.patch(f"/api/v1/exam-plans/{self.plan_id}", json=payload, headers=self.headers, name="/api/v1/exam-plans/{id} (PATCH)")