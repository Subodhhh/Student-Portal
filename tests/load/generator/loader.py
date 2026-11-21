"""Stage 1 loader: creates the deterministic plan (or its canary subset) through the REAL Institute APIs, so every business rule,
audit entry, Student identity creation and the Institute -> Student reference sync run exactly as in production.

Phases (each waits for the previous one): subjects check -> journal/target verification -> providers -> provider subjects ->
branches -> batches -> batch subjects -> staff (+ role assignments) -> teaching -> students & enrollments -> papers (upload + approve).
Before every phase the stack binding is re-verified (`phase_check`); a phase is recorded as complete only if none of its items failed.

Duplicate prevention
  * Creates the DATABASE de-duplicates (provider/staff e-mail, role assignment, teaching, subject mappings, student e-mail) may be
    re-sent by the client: a duplicate is answered with a known 409 and treated as "already there".
  * Creates it can NOT de-duplicate (branches, batches, enrollments, paper uploads) are never re-sent blindly. After an uncertain
    outcome (no response, 502/503/504, 500) the server is polled for the row for up to `poll_budget` seconds (default 180). If it
    appears, it is used; if not, the item is recorded as UNCERTAIN and NOT re-sent in this run (the original request may still be
    running). The next run looks on the server first and creates it only if it is still absent.
  * Branch and batch names are looked up (one cached listing per parent) before every create.
The journal is verified against the server at start-up: IDs it lists must exist (a reset branch is detected) and the target must
not hold marker rows unknown to the journal (unless adopting them is explicitly requested)."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .api import Api, ApiError
from .journal import Journal
from .plan import SUBJECT_CODES, Plan
from .questions import paper_csv

log = logging.getLogger("lt.loader")
READY = {"APPROVED", "PUBLISHED"}


class AbortRun(Exception):
    pass


class Uncertain(ApiError):
    def __init__(self, what: str, seconds: float):
        super().__init__(0, "UNCERTAIN", f"{what}: outcome unknown after {seconds:.0f} s of checking; not re-sent in this run "
                                          "(the next run checks the server first)")


@dataclass
class Progress:
    phase: str = ""
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    blocked: int = 0                                     # waiting for a parent that failed earlier (not a new server error)
    started: float = field(default_factory=time.monotonic)
    recent: list = field(default_factory=list)          # outcome of the last operations (True = failure)

    def line(self) -> str:
        n = self.done + self.skipped + self.failed + self.blocked
        rate = n / max(time.monotonic() - self.started, 1e-6)
        eta = (self.total - n) / rate if rate > 0 else float("nan")
        return (f"[{self.phase}] {n}/{self.total} (created {self.done}, already there {self.skipped}, failed {self.failed}, "
                f"blocked {self.blocked}) {rate:.2f}/s ETA {eta / 60:.1f} min")


class Loader:
    def __init__(self, api: Api, plan: Plan, journal: Journal, *, workers: int = 3, max_failure_rate: float = 0.02,
                 max_failures: int | None = None, report=print, report_every: float = 10.0,
                 phase_check: Callable[[], list[str]] | None = None, poll_interval: float = 10.0, poll_budget: float = 180.0):
        self.api, self.plan, self.j = api, plan, journal
        self.scope = "canary" if plan.full is not None else "full"
        self.workers, self.max_failure_rate, self.max_failures = workers, max_failure_rate, max_failures
        self.report, self.report_every = report, report_every
        self.phase_check = phase_check
        self.poll_interval, self.poll_budget = poll_interval, poll_budget
        self.subject_id: dict[str, str] = {}
        self._children: dict[tuple, dict[str, str]] = {}
        self.progress = Progress()
        self.failures_this_run = 0

    # ------------------------------------------------------------------ orchestration
    def run(self, *, adopt_existing: bool = False) -> dict:
        t0 = time.monotonic()
        self.check_subjects()
        self.verify_target(adopt_existing)
        p = self.plan
        self._phase("providers", list(p.providers.values()), self.ensure_provider)
        self._phase("provider_subjects", [(pk, s) for pk, pr in p.providers.items() for s in pr.subjects], self.ensure_provider_subject)
        self._phase("branches", list(p.branches.values()), self.ensure_branch)
        self._phase("batches", list(p.batches.values()), self.ensure_batch)
        self._phase("batch_subjects", [(bk, s) for bk, b in p.batches.items() for s in b.subjects], self.ensure_batch_subject)
        self._phase("staff", list(p.staff.values()), self.ensure_staff)
        self._phase("teaching", list(p.teaching.values()), self.ensure_teaching)
        self._phase("students", list(p.students.values()), self.ensure_student)       # a student's memberships run in order
        self._phase("papers", list(p.papers.values()), self.ensure_paper)
        self.j.flush()
        out = {"scope": self.scope, "api_calls": self.api.calls, "seconds": round(time.monotonic() - t0, 1),
               "done": {k: len(v) for k, v in self.j.data["done"].items()}, "failed": self.j.failures(),
               "uncertain": {k: sorted(v) for k, v in self.j.data["uncertain"].items() if v},
               "timings": self.api.timing_report()}
        self.j.note_run({"finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         **{k: out[k] for k in ("scope", "api_calls", "seconds")}})
        return out

    def _phase(self, name: str, items: list, fn) -> None:
        if self.phase_check:
            problems = self.phase_check()
            if problems:
                self.j.flush()
                raise AbortRun(f"stack binding changed before phase {name}: " + "; ".join(problems))
        self.progress = pr = Progress(phase=name, total=len(items))
        self.report(f"phase {name}: {len(items)} items")
        last = time.monotonic()

        def one(item):
            try:
                return fn(item), None
            except Exception as e:  # noqa: BLE001 - any per-item error is recorded, never crashes the run or loses the journal
                return None, e

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(one, it): it for it in items}
            for fut in as_completed(futures):
                created, err = fut.result()
                blocked = isinstance(err, ApiError) and err.code == "DEPENDENCY_MISSING"
                if err is not None:
                    item = futures[fut]
                    key = item.key if hasattr(item, "key") else ":".join(map(str, item))
                    uncertain = isinstance(err, ApiError) and err.uncertain
                    text = str(err) if isinstance(err, ApiError) else f"{type(err).__name__}: unexpected error (see loader.log)"
                    self.j.fail(name, key, text, uncertain=uncertain)
                    if blocked:                          # a consequence of an earlier failure: recorded, not counted again
                        pr.blocked += 1
                        log.warning("%s %s blocked: %s", name, key, text)
                    else:
                        pr.failed += 1
                        self.failures_this_run += 1
                        log.error("%s %s failed: %s", name, key, text)
                    if not isinstance(err, ApiError):
                        log.error("%s %s unexpected %s", name, key, type(err).__name__, exc_info=(type(err), err, err.__traceback__))
                else:
                    pr.done += bool(created)
                    pr.skipped += not created
                if not blocked:
                    pr.recent = (pr.recent + [err is not None])[-200:]
                reason = None
                if self.max_failures is not None and self.failures_this_run >= self.max_failures:
                    reason = f"{self.failures_this_run} failure(s) reached --max-failures {self.max_failures} in phase {name}"
                elif len(pr.recent) >= 50 and sum(pr.recent) / len(pr.recent) > self.max_failure_rate:
                    reason = f"failure rate {sum(pr.recent)}/{len(pr.recent)} above {self.max_failure_rate:.0%} in phase {name}"
                if reason:
                    for f in futures:
                        f.cancel()
                    self.j.flush()
                    raise AbortRun(reason + ": stopped")
                if time.monotonic() - last >= self.report_every:
                    self.report(pr.line())
                    last = time.monotonic()
        self.report(pr.line())
        if pr.failed or pr.blocked:
            self.report(f"phase {name}: {pr.failed} failed and {pr.blocked} blocked item(s) recorded in the journal; "
                        "the phase is NOT complete")
        else:
            self.j.complete_phase(self.scope, name, len(items))

    # ------------------------------------------------------------------ start-up checks
    def check_subjects(self) -> None:
        found = {s["code"]: s for s in self.api.all_pages("/subjects")}
        missing = [c for c in SUBJECT_CODES if c not in found or found[c].get("status") != "ACTIVE"]
        if missing:
            raise AbortRun(f"subjects missing or inactive in the target: {', '.join(missing)} (the loader never creates subjects)")
        self.subject_id = {c: str(found[c]["subject_id"]) for c in SUBJECT_CODES}
        self.report("subjects verified: " + ", ".join(f"{c}={found[c]['name']}" for c in SUBJECT_CODES))

    def verify_target(self, adopt_existing: bool) -> None:
        """Refuse a stale journal (IDs it lists are gone: e.g. the branch was reset) and a target holding marker rows the journal
        does not know (another run's data), unless adopting them is requested (server-side checks still prevent duplicates)."""
        marker = self.plan.config["marker"]
        on_server = {r["name"]: str(r["provider_id"]) for r in self.api.all_pages("/providers", q=f"{marker} Provider")
                     if r["name"].startswith(f"{marker} Provider ")}
        journaled = self.j.ids("providers")
        gone = sorted(k for k, pid in journaled.items() if pid not in set(on_server.values()))
        if gone:
            raise AbortRun(f"stale journal: {len(gone)} provider(s) it lists no longer exist on the target (branch reset?). "
                           "Move the journal aside and start again; nothing was written")
        stale = []
        for kind, probe in (("branches", lambda i: self.api.get(f"/branches/{i}")), ("staff", lambda i: self.api.get(f"/staff/{i}")),
                            ("papers", lambda i: self.api.get(f"/paper-versions/{i['paper_version_id']}"))):
            for key, value in sorted(self.j.ids(kind).items())[:3]:
                try:
                    probe(value)
                except ApiError as e:
                    if e.status == 404:
                        stale.append(f"{kind} {key}")
                    else:
                        raise
        base = self.plan.full or self.plan
        for key, tid in sorted(self.j.ids("batches").items())[:3]:
            bid = self.j.get("branches", base.batches[key].branch)
            if not bid or tid not in {str(r["batch_id"]) for r in self.api.all_pages(f"/branches/{bid}/batches")}:
                stale.append(f"batches {key}")
        if stale:
            raise AbortRun(f"stale journal: {', '.join(stale)} no longer exist on the target. Move the journal aside; nothing was written")
        unknown = sorted(set(on_server.values()) - set(journaled.values()))
        if unknown and not adopt_existing:
            raise AbortRun(f"the target already holds {len(unknown)} '{marker} Provider' row(s) this journal does not know. Use the "
                           "journal of the run that created them, reset the branch, or pass --adopt-existing; nothing was written")
        self.report(f"target verified: {len(journaled)} journaled provider(s) present"
                    + (f", {len(unknown)} existing adopted" if unknown else ""))

    # ------------------------------------------------------------------ helpers
    def _need(self, kind: str, key: str) -> str:
        v = self.j.get(kind, key)
        if v is None:
            raise ApiError(422, "DEPENDENCY_MISSING", f"{kind} {key} was not created")
        return v

    def _children_of(self, path: str, id_field: str, parent: str, refresh: bool = False) -> dict[str, str]:
        ck = (path, parent)
        if refresh or ck not in self._children:
            self._children[ck] = {row["name"]: str(row[id_field]) for row in self.api.all_pages(path)}
        return self._children[ck]

    def _safe_create(self, kind: str, key: str, find: Callable[[bool], object], create: Callable[[], object], *, check_first: bool):
        """Create a row the database cannot de-duplicate. find(fresh) -> existing value or None; create() -> new value.
        Never re-sends after an uncertain outcome: polls find() for up to poll_budget seconds, then gives up as UNCERTAIN."""
        if check_first or self.j.is_uncertain(kind, key):
            found = find(self.j.is_uncertain(kind, key))
            if found:
                return found, False
        try:
            return create(), True
        except ApiError as e:
            if not e.uncertain:
                raise
            log.warning("%s %s: uncertain outcome (%s); checking the server before anything else", kind, key, e.code)
        self.j.mark_uncertain(kind, key)                    # survives a crash: the next run looks before it creates
        polls = max(1, int(self.poll_budget // self.poll_interval))
        for _ in range(polls):
            self.api.sleep(self.poll_interval)
            found = find(True)
            if found:
                return found, False
        raise Uncertain(f"{kind} {key}", polls * self.poll_interval)

    # ------------------------------------------------------------------ entities
    def ensure_provider(self, p) -> bool:
        if self.j.get("providers", p.key):
            return False

        def find():
            return next((str(r["provider_id"]) for r in self.api.all_pages("/providers", q=p.name) if r["name"] == p.name), None)
        pid = find()
        created = False
        if not pid:
            try:     # e-mail unique in the database: a re-sent create is answered PROVIDER_EXISTS
                pid = str(self.api.post("/providers", {"name": p.name, "email": p.email, "phone": None, "address": None})["provider_id"])
                created = True
            except ApiError as e:
                if e.code != "PROVIDER_EXISTS" or not (pid := find()):
                    raise
        self.j.put("providers", p.key, pid)
        return created

    def ensure_provider_subject(self, item) -> bool:
        pkey, code = item
        key = f"{pkey}:{code}"
        if self.j.get("provider_subjects", key):
            return False
        self.api.put(f"/providers/{self._need('providers', pkey)}/subjects/{self.subject_id[code]}")   # idempotent
        self.j.put("provider_subjects", key, True)
        return True

    def _ensure_child(self, kind: str, key: str, path: str, id_field: str, parent: str, name: str, body: dict) -> tuple[str, bool]:
        def find(fresh: bool):
            return self._children_of(path, id_field, parent, refresh=fresh).get(name)

        def create():
            new = str(self.api.post(path, body, retry=False)[id_field])
            self._children_of(path, id_field, parent)[name] = new
            return new
        return self._safe_create(kind, key, find, create, check_first=True)

    def ensure_branch(self, b) -> bool:
        if self.j.get("branches", b.key):
            return False
        pid = self._need("providers", b.provider)
        bid, created = self._ensure_child("branches", b.key, f"/providers/{pid}/branches", "branch_id", pid, b.name,
                                          {"name": b.name, "city": b.city, "state": "Maharashtra", "address": None, "pincode": None})
        self.j.put("branches", b.key, bid)
        return created

    def ensure_batch(self, t) -> bool:
        if self.j.get("batches", t.key):
            return False
        bid = self._need("branches", t.branch)
        tid, created = self._ensure_child("batches", t.key, f"/branches/{bid}/batches", "batch_id", bid, t.name, {
            "name": t.name, "description": f"{self.plan.config['marker']} synthetic batch", "start_date": t.start_date,
            "end_date": t.end_date, "default_class_level": t.class_level, "default_academic_year": self.plan.config["academic_year"]})
        self.j.put("batches", t.key, tid)
        return created

    def ensure_batch_subject(self, item) -> bool:
        tkey, code = item
        key = f"{tkey}:{code}"
        if self.j.get("batch_subjects", key):
            return False
        self.api.put(f"/batches/{self._need('batches', tkey)}/subjects/{self.subject_id[code]}")       # idempotent
        self.j.put("batch_subjects", key, True)
        return True

    def ensure_staff(self, s) -> bool:
        """The global identity (e-mail unique) and its roles at the providers of THIS plan (a canary holds a subset; the full run
        adds the remaining roles to the same identity)."""
        scope = self.plan.roles_of(s)
        if not scope:
            return False
        created = False
        sid = self.j.get("staff", s.key)
        if not sid:
            def lookup():
                found = self.api.get("/staff/lookup", email=s.email)
                return str(found[0]["staff_id"]) if found else None
            sid = lookup()
            if not sid:
                first = {"role": s.role, "provider_id": self._need("providers", scope[0]), "branch_id": None}
                try:
                    sid = str(self.api.post("/staff", {"full_name": s.full_name, "email": s.email, "phone": None, "assignment": first})["staff_id"])
                    created = True
                    self.j.put("roles", f"{s.key}:{scope[0]}", True)
                except ApiError as e:
                    if e.code != "STAFF_EXISTS" or not (sid := lookup()):
                        raise
            self.j.put("staff", s.key, sid)
        for pkey in scope:
            if self.j.get("roles", f"{s.key}:{pkey}"):
                continue
            try:     # partial unique index per (staff, role, provider): a re-sent assignment is answered ASSIGNMENT_EXISTS
                self.api.post(f"/staff/{sid}/assignments", {"role": s.role, "provider_id": self._need("providers", pkey), "branch_id": None})
            except ApiError as e:
                if e.code != "ASSIGNMENT_EXISTS":
                    raise
            self.j.put("roles", f"{s.key}:{pkey}", True)
        return created

    def ensure_teaching(self, t) -> bool:
        if self.j.get("teaching", t.key):
            return False
        body = {"staff_id": self._need("staff", t.staff), "branch_id": self._need("branches", t.branch),
                "subject_id": self.subject_id[t.subject], "batch_id": self._need("batches", t.batch) if t.batch else None}
        created = True
        try:         # partial unique indexes: a re-sent assignment is answered ASSIGNMENT_EXISTS
            self.api.post("/teaching", body)
        except ApiError as e:
            if e.code != "ASSIGNMENT_EXISTS":
                raise
            created = False
        self.j.put("teaching", t.key, True)
        return created

    def ensure_student(self, s) -> bool:
        """All memberships of one student, in order: the first enrollment creates the global identity, the others link it."""
        created_any = False
        student_id = self.j.get("students", s.key)
        for mkey in s.memberships:
            if self.j.get("enrollments", mkey):
                continue
            m = self.plan.memberships[mkey]
            branch_id = self._need("branches", m.branch)
            body = {"batch_ids": [self._need("batches", t) for t in m.batches],
                    "batch_subjects": [{"batch_id": self._need("batches", t), "subject_ids": [self.subject_id[c] for c in subs]}
                                       for t, subs in m.chosen_subjects.items()],
                    "academic": {"class_level": m.class_level, "academic_year": self.plan.config["academic_year"]}}

            def lookup() -> str | None:
                hits = self.api.post("/students/lookup", {"branch_id": branch_id, "email": s.email})["matches"]
                return hits[0]["student_id"] if hits else None

            def find(_fresh: bool):
                sid = student_id or lookup()
                if not sid:
                    return None
                row = next((r for r in self.api.all_pages(f"/branches/{branch_id}/enrollments", status="ACTIVE")
                            if r["student_id"] == sid), None)
                return (row["enrollment_id"], sid) if row else None

            def create():
                sid = student_id or lookup()
                try:
                    if sid:
                        res = self.api.post(f"/branches/{branch_id}/enrollments", {**body, "student_id": sid}, retry=False)
                    else:
                        try:
                            res = self.api.post(f"/branches/{branch_id}/enrollments",
                                                {**body, "new_student": {"first_name": s.first_name, "last_name": s.last_name,
                                                                         "email": s.email, "phone": None}}, retry=False)
                        except ApiError as e:
                            if e.code != "STUDENT_EXISTS" or not (sid := lookup()):
                                raise
                            res = self.api.post(f"/branches/{branch_id}/enrollments", {**body, "student_id": sid}, retry=False)
                    return res["enrollment_id"], res["student_id"]
                except ApiError as e:
                    if e.code != "ALREADY_ENROLLED":
                        raise
                    found = find(True)
                    if not found:
                        raise ApiError(409, "ALREADY_ENROLLED", "reported enrolled but not on the ACTIVE roster") from None
                    return found

            (enrollment_id, student_id), created = self._safe_create("enrollments", mkey, find, create, check_first=False)
            created_any |= created
            self.j.put("students", s.key, student_id)
            self.j.put("enrollments", mkey, {"enrollment_id": enrollment_id, "student_id": student_id})
        return created_any

    def ensure_paper(self, p) -> bool:
        done = self.j.get("papers", p.key)
        if done and done.get("status") in READY:
            return False
        cfg = self.plan.config
        branch_id, subject_id = self._need("branches", p.branch), self.subject_id[p.subject]
        created = False
        pv = done["paper_version_id"] if done else None
        if not pv:
            data = paper_csv(cfg["marker"], p.subject, p.first_question, cfg["questions_per_paper"], cfg["seed"])

            def find(_fresh: bool):
                for row in self.api.all_pages(f"/branches/{branch_id}/papers", subject_id=subject_id):
                    v = row.get("latest_version") or {}
                    if row["title"] == p.title and v.get("status") not in (None, "WITHDRAWN", "SUPERSEDED"):
                        return str(v["paper_version_id"])
                return None

            def create():
                try:
                    return str(self.api.post(f"/branches/{branch_id}/subjects/{subject_id}/papers/upload", json=None, retry=False,
                                             data={"title": p.title}, files={"file": (f"{p.key}.csv", data, "text/csv")})["paper_version_id"])
                except ApiError as e:
                    if e.code == "DUPLICATE_PAPER" and e.detail.get("paper_version_id"):
                        return str(e.detail["paper_version_id"])
                    raise
            pv, created = self._safe_create("papers", p.key, find, create, check_first=False)
            self.j.put("papers", p.key, {"paper_version_id": pv, "status": "VALIDATED"})
        try:
            status = self.api.post(f"/paper-versions/{pv}/approve", {"note": f"{cfg['marker']} synthetic paper"})["status"]
        except ApiError as e:
            if e.code != "INVALID_TRANSITION":
                raise
            latest = self.api.get(f"/paper-versions/{pv}").get("latest_version") or {}
            status = latest.get("status") if str(latest.get("paper_version_id")) == str(pv) else None
            if status not in READY:
                raise
        self.j.put("papers", p.key, {"paper_version_id": pv, "status": status})
        return created
