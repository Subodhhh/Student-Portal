"""In-process fake of the Institute API (httpx.MockTransport) for the loader tests: no network, no database, no containers.
It mirrors the request schemas (unknown fields rejected, like the services' Strict models), the uniqueness and business rules the
loader relies on, session cookie + CSRF + MFA, and validates uploaded papers with the REAL paper-service CSV validator.
Fault injection: `lose_response_after[kind] = n` commits the n-th create of `kind` and then drops the response (transport error),
`expire_session_after = n` invalidates the session after n authenticated requests, `fail[kind] = (status, code)` rejects creates."""
import hashlib
import json
import re
import sys
import uuid
from collections import defaultdict
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "institute-backend/institute-paper-service"))
from inst_paper.services import extraction  # noqa: E402  (the application's own validator; stdlib only)

SUBJECTS = {f"SUB{n:03d}": name for n, name in enumerate(
    ["Chemistry-I", "Biology-I", "Mathematics-I", "Physics-I", "Physics-II", "Chemistry-II", "Biology-II", "Mathematics-II"], start=1)}
FIELDS = {
    "login": {"staff_code", "password"}, "mfa": {"challenge_id", "otp"},
    "provider": {"name", "email", "phone", "address"}, "branch": {"name", "address", "city", "state", "pincode"},
    "batch": {"name", "description", "start_date", "end_date", "default_class_level", "default_academic_year", "default_other_description"},
    "staff": {"full_name", "email", "phone", "assignment"}, "assignment": {"role", "provider_id", "branch_id"},
    "teaching": {"staff_id", "branch_id", "subject_id", "batch_id"}, "lookup": {"branch_id", "email", "phone"},
    "enroll": {"student_id", "new_student", "confirm_distinct_person", "batch_ids", "batch_subjects", "academic"},
    "new_student": {"first_name", "last_name", "email", "phone"}, "batch_subjects": {"batch_id", "subject_ids"},
    "academic": {"class_level", "academic_year", "other_class_description"}, "note": {"note"},
}
OTP = "246810"
PASSWORD = "correct horse battery staple"


class Lost(Exception):
    pass


def err(status: int, code: str, message: str = "", **extra) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"code": code, "message": message or code, **extra}})


class FakeInstitute:
    def __init__(self, missing_subjects=()):
        self.subjects = {c: {"subject_id": str(uuid.uuid4()), "code": c, "name": n, "status": "ACTIVE"}
                         for c, n in SUBJECTS.items() if c not in missing_subjects}
        self.providers, self.branches, self.batches, self.staff, self.students, self.enrollments = {}, {}, {}, {}, {}, {}
        self.papers = {}                                       # paper_version_id -> {branch, subject, hash, status, questions}
        self.provider_subjects, self.batch_subjects, self.roles, self.teaching = set(), set(), set(), set()
        self.creates = defaultdict(int)
        self.lose_response_after: dict[str, int] = {}
        self.expire_session_after: int | None = None
        self.fail: dict[str, tuple[int, str]] = {}
        self.sessions: dict[str, str] = {}                     # sid -> csrf
        self.challenges: set[str] = set()
        self.authed_requests = 0
        self.unsafe_without_csrf = 0
        self.next_student = 708
        self.seen_bodies: list[bytes] = []
        # gateway faults for creates the database cannot de-duplicate: kind -> {"at": n, "mode": after_commit | before_commit |
        # no_commit, "status": 504, "visible_after": k}. before_commit = the upstream is still working: the row appears only after
        # k further GET requests (a re-sent create meanwhile WOULD create a duplicate, as the real service would).
        self.gateway: dict[str, dict] = {}
        self.attempts = defaultdict(int)                       # POST attempts per kind (a blind re-send shows up here)
        self.pending: list[list] = []
        self.malformed: dict[str, int] = {}                    # kind -> attempt that commits but answers an unexpected body

    # ------------------------------------------------------------------ transport
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.seen_bodies.append(req.content)
        self._tick_pending()                                   # time passes with every request the client makes
        path = req.url.path.removeprefix("/api/v1")
        try:
            if path == "/auth/login":
                return self._login(json.loads(req.content))
            if path == "/auth/mfa/verify":
                return self._mfa(json.loads(req.content))
            sid = req.headers.get("cookie", "").partition("inst_sid=")[2].split(";")[0]
            if sid not in self.sessions:
                return err(401, "UNAUTHENTICATED")
            self.authed_requests += 1
            if self.expire_session_after and self.authed_requests >= self.expire_session_after:
                self.sessions.pop(sid)
                self.expire_session_after = None
                return err(401, "UNAUTHENTICATED")
            if req.method != "GET" and req.headers.get("x-csrf-token") != self.sessions[sid]:
                self.unsafe_without_csrf += 1
                return err(403, "CSRF_FAILED")
            return self.route(req, path)
        except Lost:
            raise httpx.ReadTimeout("response lost after commit", request=req)

    def _created(self, kind: str) -> None:
        self.creates[kind] += 1
        if self.lose_response_after.get(kind) == self.creates[kind]:
            raise Lost()

    def _tick_pending(self) -> None:
        still = []
        for item in self.pending:
            item[0] -= 1
            if item[0] <= 0:
                try:
                    item[1]()                                  # the slow upstream request finally commits
                except Lost:
                    pass
            else:
                still.append(item)
        self.pending = still

    def _gated(self, kind: str, commit) -> httpx.Response:
        """commit() applies the create and returns its success response; the configured gateway fault decides what the client sees."""
        self.attempts[kind] += 1
        g = self.gateway.get(kind)
        if g and g["at"] == self.attempts[kind]:
            status = g.get("status", 504)
            if g["mode"] == "after_commit":
                commit()
            elif g["mode"] == "before_commit":
                self.pending.append([g.get("visible_after", 2), commit])
            return httpx.Response(status, text="<html>gateway timeout</html>")
        if self.malformed.get(kind) == self.attempts[kind]:
            commit()
            return httpx.Response(201, json={"unexpected": True})
        return commit()

    @staticmethod
    def _strict(body: dict, kind: str) -> httpx.Response | None:
        extra = set(body) - FIELDS[kind]
        return err(422, "VALIDATION", f"unexpected fields {sorted(extra)}") if extra else None

    def _login(self, body):
        if (bad := self._strict(body, "login")):
            return bad
        if body["password"] != PASSWORD or body["staff_code"] != "STF000001":
            return err(401, "INVALID_CREDENTIALS")
        cid = f"challenge-{uuid.uuid4().hex}"
        self.challenges.add(cid)
        return httpx.Response(200, json={"status": "MFA_REQUIRED", "challenge_id": cid})

    def _mfa(self, body):
        if (bad := self._strict(body, "mfa")):
            return bad
        if body["challenge_id"] not in self.challenges or body["otp"] != OTP:
            return err(401, "INVALID_OTP")
        sid, csrf = uuid.uuid4().hex, uuid.uuid4().hex
        self.sessions[sid] = csrf
        return httpx.Response(200, json={"status": "AUTHENTICATED", "me": {"staff_code": "STF000001", "primary_role": "SUPER_ADMIN",
                                                                             "csrf_token": csrf}},
                              headers={"set-cookie": f"inst_sid={sid}; HttpOnly; Path=/; SameSite=strict"})

    # ------------------------------------------------------------------ routes
    def route(self, req: httpx.Request, path: str) -> httpx.Response:
        m, q = req.method, req.url.params
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else {}
        page = lambda items: httpx.Response(200, json={"items": items[(int(q.get("page", 1)) - 1) * 100:][:int(q.get("page_size", 25))],  # noqa: E731
                                                       "total": len(items), "page": int(q.get("page", 1)), "page_size": int(q.get("page_size", 25))})
        if m == "GET" and path == "/subjects":
            return page(list(self.subjects.values()))
        if path == "/providers":
            if m == "GET":
                ql = (q.get("q") or "").lower()
                return page([{"provider_id": k, **v} for k, v in self.providers.items() if ql in v["name"].lower()])
            if (bad := self._strict(body, "provider")):
                return bad
            if any(p["email"] == body["email"].lower() for p in self.providers.values()):
                return err(409, "PROVIDER_EXISTS")
            if "provider" in self.fail:
                return err(*self.fail["provider"])
            pid = str(uuid.uuid4())
            self.providers[pid] = {"name": body["name"], "email": body["email"].lower(), "status": "ACTIVE"}
            self._created("provider")
            return httpx.Response(201, json={"provider_id": pid, **self.providers[pid]})
        if (mt := re.fullmatch(r"/providers/([^/]+)/subjects/([^/]+)", path)) and m == "PUT":
            sid = mt[2]
            if mt[1] not in self.providers or sid not in {s["subject_id"] for s in self.subjects.values()}:
                return err(404, "NOT_FOUND")
            self.provider_subjects.add((mt[1], sid))
            return httpx.Response(200, json={"subject_id": sid})
        if mt := re.fullmatch(r"/providers/([^/]+)/branches", path):
            pid = mt[1]
            if pid not in self.providers:
                return err(404, "NOT_FOUND")
            if m == "GET":
                return page([{"branch_id": k, **v} for k, v in self.branches.items() if v["provider_id"] == pid])
            if (bad := self._strict(body, "branch")):
                return bad

            def commit():
                bid = str(uuid.uuid4())
                self.branches[bid] = {"provider_id": pid, "name": body["name"], "status": "ACTIVE"}
                self._created("branch")
                return httpx.Response(201, json={"branch_id": bid, **self.branches[bid]})
            return self._gated("branch", commit)
        if (mt := re.fullmatch(r"/branches/([^/]+)", path)) and m == "GET":
            return httpx.Response(200, json={"branch_id": mt[1], **self.branches[mt[1]]}) if mt[1] in self.branches else err(404, "NOT_FOUND")
        if (mt := re.fullmatch(r"/staff/([^/]+)", path)) and m == "GET" and mt[1] != "lookup":
            return httpx.Response(200, json={"staff_id": mt[1], **self.staff[mt[1]]}) if mt[1] in self.staff else err(404, "NOT_FOUND")
        if (mt := re.fullmatch(r"/branches/([^/]+)/papers", path)) and m == "GET":
            rows = [{"paper_id": pv, "title": p["title"], "subject_id": p["subject"],
                     "latest_version": {"paper_version_id": pv, "status": p["status"]}}
                    for pv, p in self.papers.items() if p["branch"] == mt[1] and (not q.get("subject_id") or p["subject"] == q["subject_id"])]
            return page(rows)
        if mt := re.fullmatch(r"/branches/([^/]+)/batches", path):
            bid = mt[1]
            if bid not in self.branches:
                return err(404, "NOT_FOUND")
            if m == "GET":
                return page([{"batch_id": k, **v} for k, v in self.batches.items() if v["branch_id"] == bid])
            if (bad := self._strict(body, "batch")):
                return bad
            if body["end_date"] < body["start_date"] or body["default_class_level"] not in {"11TH", "12TH"}:
                return err(422, "INVALID")
            def commit():
                tid = str(uuid.uuid4())
                self.batches[tid] = {"branch_id": bid, "name": body["name"], "status": "ACTIVE", "class": body["default_class_level"]}
                self._created("batch")
                return httpx.Response(201, json={"batch_id": tid, **self.batches[tid]})
            return self._gated("batch", commit)
        if (mt := re.fullmatch(r"/batches/([^/]+)/subjects/([^/]+)", path)) and m == "PUT":
            tid, sid = mt[1], mt[2]
            if tid not in self.batches:
                return err(404, "NOT_FOUND")
            if (self.branches[self.batches[tid]["branch_id"]]["provider_id"], sid) not in self.provider_subjects:
                return err(422, "SUBJECT_NOT_ENABLED")
            self.batch_subjects.add((tid, sid))
            return httpx.Response(200, json={"subject_id": sid})
        if path == "/staff/lookup" and m == "GET":
            hit = [{"staff_id": k, "staff_code": v["code"], "full_name": v["name"]} for k, v in self.staff.items() if v["email"] == q["email"].lower()]
            return httpx.Response(200, json=hit)
        if path == "/staff" and m == "POST":
            if (bad := self._strict(body, "staff")) or (bad := self._strict(body["assignment"], "assignment")):
                return bad
            if any(s["email"] == body["email"].lower() for s in self.staff.values()):
                return err(409, "STAFF_EXISTS")
            sid = str(uuid.uuid4())
            self.staff[sid] = {"email": body["email"].lower(), "name": body["full_name"], "code": f"STF{len(self.staff) + 100:06d}"}
            a = body["assignment"]
            self.roles.add((sid, a["role"], a["provider_id"]))
            self._created("staff")
            return httpx.Response(201, json={"staff_id": sid})
        if (mt := re.fullmatch(r"/staff/([^/]+)/assignments", path)) and m == "POST":
            if (bad := self._strict(body, "assignment")):
                return bad
            key = (mt[1], body["role"], body["provider_id"])
            if key in self.roles:
                return err(409, "ASSIGNMENT_EXISTS")
            self.roles.add(key)
            return httpx.Response(201, json={"assignment_id": str(uuid.uuid4())})
        if path == "/teaching" and m == "POST":
            if (bad := self._strict(body, "teaching")):
                return bad
            br = self.branches[body["branch_id"]]
            if (body["staff_id"], "TEACHER", br["provider_id"]) not in self.roles:
                return err(422, "NOT_A_TEACHER")
            if (br["provider_id"], body["subject_id"]) not in self.provider_subjects:
                return err(422, "SUBJECT_NOT_ENABLED")
            if body["batch_id"] and self.batches[body["batch_id"]]["branch_id"] != body["branch_id"]:
                return err(422, "INVALID_BATCH")
            key = (body["staff_id"], body["branch_id"], body["subject_id"], body["batch_id"])
            if key in self.teaching:
                return err(409, "ASSIGNMENT_EXISTS")
            self.teaching.add(key)
            self._created("teaching")
            return httpx.Response(201, json={"teaching_id": str(uuid.uuid4())})
        if path == "/students/lookup" and m == "POST":
            if (bad := self._strict(body, "lookup")):
                return bad
            return httpx.Response(200, json={"matches": [{"student_id": k, "name": "x"} for k, v in self.students.items()
                                                         if v["email"] == (body.get("email") or "").lower()]})
        if mt := re.fullmatch(r"/branches/([^/]+)/enrollments", path):
            return self._enroll(mt[1], m, body, q, page)
        if (mt := re.fullmatch(r"/branches/([^/]+)/subjects/([^/]+)/papers/upload", path)) and m == "POST":
            return self._upload(mt[1], mt[2], req)
        if (mt := re.fullmatch(r"/paper-versions/([^/]+)/approve", path)) and m == "POST":
            if (bad := self._strict(body, "note")):
                return bad
            pv = self.papers.get(mt[1])
            if not pv:
                return err(404, "NOT_FOUND")
            if pv["status"] != "VALIDATED":
                return err(409, "INVALID_TRANSITION")
            pv["status"] = "APPROVED"
            self._created("approve")
            return httpx.Response(200, json={"paper_version_id": mt[1], "status": "APPROVED"})
        if (mt := re.fullmatch(r"/paper-versions/([^/]+)", path)) and m == "GET":
            pv = self.papers.get(mt[1])
            return httpx.Response(200, json={"latest_version": {"paper_version_id": mt[1], "status": pv["status"]}}) if pv else err(404, "NOT_FOUND")
        return err(404, "NO_ROUTE", f"{m} {path}")

    def _enroll(self, branch_id, m, body, q, page):
        if branch_id not in self.branches:
            return err(404, "NOT_FOUND")
        if m == "GET":
            rows = [{"enrollment_id": k, **v} for k, v in self.enrollments.items() if v["branch_id"] == branch_id
                    and (not q.get("status") or v["status"] == q["status"])]
            return page(rows)
        for kind, part in (("enroll", body), ("academic", body.get("academic", {})), ("new_student", body.get("new_student") or {})):
            if (bad := self._strict(part, kind)):
                return bad
        for bs in body.get("batch_subjects", []):
            if (bad := self._strict(bs, "batch_subjects")):
                return bad
        if bool(body.get("student_id")) == bool(body.get("new_student")):
            return err(422, "VALIDATION", "exactly one of student_id / new_student")
        for t in body["batch_ids"]:
            if t not in self.batches or self.batches[t]["branch_id"] != branch_id:
                return err(422, "INVALID_BATCH")
        for bs in body.get("batch_subjects", []):
            if bs["batch_id"] not in body["batch_ids"] or not {(bs["batch_id"], s) for s in bs["subject_ids"]} <= self.batch_subjects:
                return err(422, "INVALID_SUBJECT")
        if not re.fullmatch(r"\d{4}-\d{4}", body["academic"]["academic_year"]):
            return err(422, "VALIDATION")
        if "enrollment" in self.fail:
            return err(*self.fail["enrollment"])

        def commit():          # the whole request as the service runs it (its duplicate check is NOT a database constraint)
            if body.get("new_student"):
                ns = body["new_student"]
                if any(s["email"] == ns["email"].lower() for s in self.students.values()):
                    return err(409, "STUDENT_EXISTS")
                student_id = f"STU{self.next_student:06d}"
                self.next_student += 1
                self.students[student_id] = {"email": ns["email"].lower()}
            else:
                student_id = body["student_id"]
                if student_id not in self.students:
                    return err(404, "STUDENT_NOT_FOUND")
            if any(e["student_id"] == student_id and e["branch_id"] == branch_id and e["status"] == "ACTIVE" for e in self.enrollments.values()):
                return err(409, "ALREADY_ENROLLED")
            eid = str(uuid.uuid4())
            self.enrollments[eid] = {"branch_id": branch_id, "provider_id": self.branches[branch_id]["provider_id"], "student_id": student_id,
                                     "status": "ACTIVE", "batch_ids": list(body["batch_ids"]),
                                     "chosen": {bs["batch_id"]: bs["subject_ids"] for bs in body.get("batch_subjects", [])},
                                     "class_level": body["academic"]["class_level"]}
            self._created("enrollment")
            return httpx.Response(201, json={"enrollment_id": eid, **self.enrollments[eid]})
        return self._gated("enrollment", commit)

    def _upload(self, branch_id, subject_id, req):
        msg = BytesParser(policy=email_policy).parsebytes(b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.content)
        parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
        if set(parts) - {"title", "file", "paper_id", "answer_key"}:
            return err(422, "VALIDATION")
        data = parts["file"].get_payload(decode=True)
        if (self.branches[branch_id]["provider_id"], subject_id) not in self.provider_subjects:
            return err(422, "SUBJECT_NOT_ENABLED")
        result = extraction.extract_csv(data, 500)
        if not result.ok:
            return err(422, "EXTRACTION_FAILED", errors=result.errors[:5])
        h = hashlib.sha256(data).hexdigest()
        title = parts["title"].get_content().strip()

        def commit():          # the identical-paper check runs inside the request: two overlapping uploads both pass it
            for pv, p in self.papers.items():
                if (p["branch"], p["subject"], p["hash"]) == (branch_id, subject_id, h):
                    return err(409, "DUPLICATE_PAPER", paper_version_id=pv)
            pv = str(uuid.uuid4())
            self.papers[pv] = {"branch": branch_id, "subject": subject_id, "hash": h, "status": "VALIDATED", "title": title,
                               "questions": result.questions}
            self._created("paper")
            return httpx.Response(201, json={"paper_version_id": pv, "status": "VALIDATED", "question_count": len(result.questions)})
        return self._gated("paper", commit)
