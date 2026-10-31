from locust import HttpUser, task, between
import uuid

class InstitutePapersLoad(HttpUser):
    # Wait longer to give the OCR/File system time to breathe
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
        self.branch_id = "00000000-0000-0000-0000-000000000000"
        self.subject_id = "00000000-0000-0000-0000-000000000000"
        self.paper_id = "00000000-0000-0000-0000-000000000000"
        self.pv_id = "00000000-0000-0000-0000-000000000000"
        self.question_id = "00000000-0000-0000-0000-000000000000"
        self.draft_id = "00000000-0000-0000-0000-000000000000"

    # -------------------------------------------------------------------
    # GET ENDPOINTS (Reads)
    # -------------------------------------------------------------------
    @task(5)
    def list_papers(self):
        """GET /api/v1/papers"""
        self.client.get("/api/v1/papers", headers=self.headers, name="/api/v1/papers")

    @task(4)
    def list_questions(self):
        """GET /api/v1/questions"""
        self.client.get("/api/v1/questions", headers=self.headers, name="/api/v1/questions")

    @task(3)
    def get_branch_papers(self):
        """GET /api/v1/branches/{branch_id}/papers"""
        self.client.get(f"/api/v1/branches/{self.branch_id}/papers", headers=self.headers, name="/api/v1/branches/{id}/papers")

    @task(3)
    def get_paper_versions(self):
        """GET /api/v1/papers/{paper_id}/versions"""
        self.client.get(f"/api/v1/papers/{self.paper_id}/versions", headers=self.headers, name="/api/v1/papers/{id}/versions")

    @task(2)
    def get_specific_question(self):
        """GET /api/v1/questions/{question_id}"""
        self.client.get(f"/api/v1/questions/{self.question_id}", headers=self.headers, name="/api/v1/questions/{id}")

    @task(2)
    def get_question_taxonomy(self):
        """GET /api/v1/question-taxonomy"""
        self.client.get("/api/v1/question-taxonomy", headers=self.headers, name="/api/v1/question-taxonomy")

    # -------------------------------------------------------------------
    # POST ENDPOINTS (Creates & Mutations)
    # -------------------------------------------------------------------
    @task(2)
    def create_question(self):
        """POST /api/v1/questions"""
        random_id = uuid.uuid4().hex[:6]
        payload = {
            "stem": f"Load test question {random_id}?",
            "option_a": "A", "option_b": "B", "option_c": "C", "option_d": "D",
            "correct_option": "A",
            "marks_positive": 4,
            "marks_negative": 1,
            "subject_id": self.subject_id
        }
        self.client.post("/api/v1/questions", json=payload, headers=self.headers, name="/api/v1/questions (Create)")

    @task(1)
    def update_question(self):
        """PATCH /api/v1/questions/{question_id}"""
        payload = {"stem": f"Updated question {uuid.uuid4().hex[:4]}?"}
        self.client.patch(f"/api/v1/questions/{self.question_id}", json=payload, headers=self.headers, name="/api/v1/questions/{id} (PATCH)")

    @task(1)
    def approve_paper_version(self):
        """POST /api/v1/paper-versions/{pv_id}/approve"""
        self.client.post(f"/api/v1/paper-versions/{self.pv_id}/approve", json={"note": "Load test approval"}, headers=self.headers, name="/api/v1/paper-versions/{id}/approve")

    @task(1)
    def publish_paper_version(self):
        """POST /api/v1/paper-versions/{pv_id}/publish"""
        self.client.post(f"/api/v1/paper-versions/{self.pv_id}/publish", json={"note": "Load test publish"}, headers=self.headers, name="/api/v1/paper-versions/{id}/publish")

    # -------------------------------------------------------------------
    # ⚠️ DANGEROUS / HEAVY ENDPOINTS (Very low weight)
    # -------------------------------------------------------------------
    @task(1)
    def create_photo_draft(self):
        """POST /api/v1/branches/{branch_id}/subjects/{subject_id}/photo-drafts"""
        payload = {"title": f"Load Test Draft {uuid.uuid4().hex[:4]}"}
        self.client.post(f"/api/v1/branches/{self.branch_id}/subjects/{self.subject_id}/photo-drafts", json=payload, headers=self.headers, name="/api/v1/branches/{id}/subjects/{id}/photo-drafts")

    @task(1)
    def delete_photo_draft(self):
        """POST /api/v1/photo-drafts/{draft_id}/discard"""
        # We use POST /discard instead of DELETE because DELETE removes the physical file (dangerous for load tests)
        self.client.post(f"/api/v1/photo-drafts/{self.draft_id}/discard", json={}, headers=self.headers, name="/api/v1/photo-drafts/{id}/discard")