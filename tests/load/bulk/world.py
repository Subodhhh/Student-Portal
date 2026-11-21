"""The Stage 2 dataset as a pure function of (profile, anchor): same input, same rows, same IDs, so a load can resume by row count.

Only the small reference entities live in memory (about 200 bytes per enrollment). Exams, attempts, answers and results are derived
on demand from hashes, so the large tables stream with constant memory. Rules copied from the application are marked with their source."""
import bisect
import hashlib
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generator.plan import DOMAIN, PROFILE_WEIGHTS, PROFILES, SUBJECT_CODES  # noqa: E402
from generator.questions import TOPICS  # noqa: E402

SUBJECT_NAMES = dict(zip(SUBJECT_CODES, ("Chemistry-I", "Biology-I", "Mathematics-I", "Physics-I", "Physics-II", "Chemistry-II",
                                         "Biology-II", "Mathematics-II")))
STAGE2 = {
    "stage": 2, "marker": "LT2", "seed": 20261104, "student_base": 100000,
    "providers": 10000, "branches_per_provider": {1: 7000, 2: 1500, 0: 1500}, "students": 50000,
    "memberships": {1: 17500, 2: 20000, 3: 7500, 4: 5000}, "second_batch_share": 0.2,
    "tiers": {"L": (0.01, 0.40), "M": (0.09, 0.40), "S": (0.90, 0.20)},      # share of branches, share of enrollments
    "batches_per_class": {"L": 3, "M": 2, "S": 2}, "classes": {"L": 2, "M": 2, "S": 1},
    "weekly_every": {"L": 1, "M": 2, "S": 4}, "mock_every": {"L": 4, "M": 4, "S": 0},     # weeks between tests; 0 = none
    "history_weeks": 16, "upcoming_weeks": 2, "questions_per_subject": 50000, "questions_per_paper": 50,
    "weekly_minutes": 60, "mock_minutes": 180, "window_extra_minutes": 180,
    "attendance": 0.78, "auto_submit_share": 0.10, "answered_share": 0.75, "cancelled_share": 0.015,
    "academic_year": "2026-2027", "batch_start": "2026-06-01", "batch_end": "2027-03-31",
}


def scaled(config: dict, fraction: float) -> dict:
    """The same shape at `fraction` of the volume; counts stay exact and the 8 subjects keep whole papers."""
    def n(v):
        return max(1, round(v * fraction))
    per_paper = config["questions_per_paper"]
    return {**config, "providers": n(config["providers"]), "students": n(config["students"]),
            "branches_per_provider": {k: n(v) for k, v in config["branches_per_provider"].items()},
            "memberships": {k: n(v) for k, v in config["memberships"].items()},
            "questions_per_subject": n(config["questions_per_subject"] // per_paper) * per_paper}


class Ids:
    """Deterministic ids: 16 hash bytes shaped as a UUID. The seed and marker make them unique to this dataset."""

    def __init__(self, config):
        self.prefix = f"{config['seed']}|{config['marker']}|".encode()

    def __call__(self, *parts) -> str:
        h = hashlib.blake2b(self.prefix + "|".join(map(str, parts)).encode(), digest_size=16).hexdigest()
        return f"{h[:8]}-{h[8:12]}-5{h[13:16]}-8{h[17:20]}-{h[20:]}"


def _unit(*parts) -> float:
    return int.from_bytes(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=6).digest(), "big") / 2 ** 48


@dataclass(slots=True)
class Provider:
    index: int
    id: str
    profile: str
    branches: list


@dataclass(slots=True)
class Branch:
    index: int
    id: str
    provider: int
    tier: str
    name: str
    city: str
    classes: tuple
    batches: list


@dataclass(slots=True)
class Batch:
    index: int
    id: str
    branch: int
    class_level: str
    name: str


@dataclass(slots=True)
class Membership:
    index: int
    id: str
    student: int
    branch: int
    batches: tuple
    class_level: str


@dataclass(slots=True)
class Paper:
    index: int
    id: str
    version_id: str
    subject: str
    branch: int
    number: int
    first_question: int


@dataclass(slots=True)
class Exam:
    key: str
    plan_id: str
    exam_id: str
    version_id: str
    job_id: str
    branch: int
    class_level: str
    kind: str
    name: str
    papers: tuple
    minutes: int
    start: datetime
    end: datetime
    cancelled: bool


def question(config: dict, subject: str, number: int) -> dict:
    """One question of a subject, the same every time. Shape follows generator/questions.py; stems are padded to real paper length."""
    digest = hashlib.blake2b(f"{config['seed']}:{subject}:{number}".encode(), digest_size=8).digest()
    a, b = 11 + digest[0] % 85, 11 + digest[1] % 85
    right = a * b
    options = [right, right + a, right + b, right - a]
    pivot = digest[2] % 4
    options = options[pivot:] + options[:pivot]
    topic = TOPICS[subject][number % len(TOPICS[subject])]
    stem = (f"Q{number:06d} ({topic}, {SUBJECT_NAMES[subject]}): A coaching assessment presents a structured numerical problem in which "
            f"two independent measured quantities take the values {a} and {b}. Using the standard method taught in this chapter, "
            f"determine their product and select the single correct option.")
    return {"stem": stem, "options": [str(x) for x in options], "correct": "ABCD"[options.index(right)], "topic": topic,
            "explanation": f"{a} x {b} = {right}"}


class World:
    def __init__(self, config: dict, anchor: datetime):
        self.config, self.anchor = config, anchor
        self.uid = Ids(config)
        self.subject_ids = {c: self.uid("subject", c) for c in SUBJECT_CODES}    # replaced by the target's own ids when it has them
        self.password_hash = None      # a pre-computed Argon2 hash from the operator; None = no password (sessions are seeded instead)
        self.created = anchor - timedelta(weeks=config["history_weeks"] + 4)
        rng = random.Random(config["seed"])
        self._organizations(rng)
        self._students(rng)
        self._papers(rng)
        self._staff()
        self._correct: dict[int, str] = {}

    # ------------------------------------------------------------- organizations
    def _organizations(self, rng):
        c = self.config
        counts = [n for n, k in c["branches_per_provider"].items() for _ in range(k)]
        rng.shuffle(counts)
        self.providers = [Provider(i, self.uid("provider", i), "", []) for i in range(1, len(counts) + 1)]
        owners = [p.index - 1 for p, n in zip(self.providers, counts) for _ in range(n)]
        rng.shuffle(owners)
        total = len(owners)
        large, medium = max(1, round(total * c["tiers"]["L"][0])), max(1, round(total * c["tiers"]["M"][0]))
        self.branches, self.batches = [], []
        cities = ["Pune", "Mumbai", "Nagpur", "Nashik", "Delhi", "Jaipur", "Kota", "Chennai", "Hyderabad", "Kolkata", "Indore", "Lucknow"]
        cycle = list(PROFILES)                           # paper-owning branches take every profile first, so all 8 subjects are covered
        for i, owner in enumerate(owners):
            tier = "L" if i < large else "M" if i < large + medium else "S"
            p = self.providers[owner]
            if not p.profile:
                p.profile = cycle.pop(0) if cycle else rng.choices(list(PROFILE_WEIGHTS), weights=list(PROFILE_WEIGHTS.values()))[0]
            n = len(p.branches) + 1
            classes = ("11TH", "12TH")[:c["classes"][tier]] if tier != "S" else (("11TH", "12TH")[rng.random() >= 0.6],)
            br = Branch(i, self.uid("branch", i), owner, tier, f"{c['marker']} P{p.index:05d} Branch {n}", rng.choice(cities), classes, [])
            self.branches.append(br)
            p.branches.append(i)
            for level in classes:
                for k in range(c["batches_per_class"][tier]):
                    t = len(self.batches)
                    self.batches.append(Batch(t, self.uid("batch", t), i, level, f"{c['marker']} {p.profile} {level[:2]}th {'ABC'[k]} B{i:05d}"))
                    br.batches.append(t)
        for p in self.providers:
            p.profile = p.profile or rng.choices(list(PROFILE_WEIGHTS), weights=list(PROFILE_WEIGHTS.values()))[0]

    def subjects_of(self, branch: Branch) -> tuple:
        return PROFILES[self.providers[branch.provider].profile]

    @staticmethod
    def subjects_of_provider(provider: Provider) -> tuple:
        return PROFILES[provider.profile]

    # ------------------------------------------------------------- students
    def _students(self, rng):
        c = self.config
        tier_count = {t: sum(b.tier == t for b in self.branches) or 1 for t in "LMS"}
        total, cumulative = 0.0, []
        for b in self.branches:
            total += c["tiers"][b.tier][1] / tier_count[b.tier]
            cumulative.append(total)
        per_student = [n for n, k in c["memberships"].items() for _ in range(k)]
        rng.shuffle(per_student)
        self.student_count = len(per_student)
        self.memberships = []
        self.by_class: dict[tuple, list] = {}
        limit = len({b.provider for b in self.branches})
        for s, want in enumerate(per_student, start=1):
            seen = set()
            while len(seen) < min(want, limit):
                b = self.branches[bisect.bisect_left(cumulative, rng.random() * total)]
                if b.provider in seen:
                    continue
                seen.add(b.provider)
                first = self.batches[rng.choice(b.batches)]
                picked = [first.index]
                same = [t for t in b.batches if t != first.index and self.batches[t].class_level == first.class_level]
                if same and rng.random() < c["second_batch_share"]:
                    picked.append(rng.choice(same))
                i = len(self.memberships)
                eid = self.uid("enrollment", i)
                m = Membership(i, eid, s, b.index, tuple(picked), first.class_level)
                self.memberships.append(m)
                self.by_class.setdefault((b.index, first.class_level), []).append(i)

    def student_id(self, s: int) -> str:
        return f"STU{self.config['student_base'] + s:06d}"      # app format; the allocator is moved past these (student_ids.py)

    # ------------------------------------------------------------- papers
    def _papers(self, rng):
        c = self.config
        per = c["questions_per_paper"]
        self.papers, by_subject = [], {}
        self.pool: dict[tuple, list] = {}
        hosts_all = [b for b in self.branches if b.tier != "S"]
        for subject in SUBJECT_CODES:
            hosts = sorted(b.index for b in hosts_all if subject in self.subjects_of(b))
            assert hosts, f"no paper-owning branch offers {subject}"
            rng.shuffle(hosts)
            for n in range(c["questions_per_subject"] // per):
                i = len(self.papers)
                p = Paper(i, self.uid("paper", i), self.uid("paper-version", i), subject, hosts[n % len(hosts)], n + 1, n * per + 1)
                self.papers.append(p)
                by_subject.setdefault(subject, []).append(i)
                self.pool.setdefault((self.branches[p.branch].provider, subject), []).append(i)
        self.shares = []                     # exam_generation.authorized_source: a provider uses its own papers or papers shared with it
        for pr in self.providers:
            if not pr.branches:
                continue
            for subject in PROFILES[pr.profile]:
                if (pr.index - 1, subject) not in self.pool:
                    papers = by_subject[subject]
                    start = int(_unit(self.config["seed"], pr.index, subject) * len(papers))
                    chosen = [papers[(start + k) % len(papers)] for k in range(min(2, len(papers)))]
                    self.pool[(pr.index - 1, subject)] = chosen
                    self.shares += [(p, pr.index - 1) for p in chosen]

    # ------------------------------------------------------------- staff
    def _staff(self):
        self.staff = [("PLATFORM", self.uid("staff", "platform"), "SUPER_ADMIN", None, None)]
        for p in self.providers:
            self.staff.append((f"admin.p{p.index:05d}", self.uid("staff", "admin", p.index), "PROVIDER_ADMIN", p.index - 1, None))
            if p.branches:
                for subject in PROFILES[p.profile]:
                    self.staff.append((f"teacher.p{p.index:05d}.{subject.lower()}", self.uid("staff", "teacher", p.index, subject), "TEACHER",
                                       p.index - 1, None))
        for b in self.branches:
            if b.tier != "S":
                self.staff.append((f"branchadmin.b{b.index:05d}", self.uid("staff", "branch-admin", b.index), "BRANCH_ADMIN", b.provider, b.index))
        self.platform_staff = self.staff[0][1]

    def teacher(self, provider: int, subject: str) -> str:
        return self.uid("staff", "teacher", provider + 1, subject)

    def actor(self, provider: int) -> str:
        return self.uid("staff", "admin", provider + 1)

    # ------------------------------------------------------------- exams
    def exams(self):
        c = self.config
        history, after = c["history_weeks"], c["upcoming_weeks"]
        for b in self.branches:
            subjects = self.subjects_of(b)
            for level in b.classes:
                if (b.index, level) not in self.by_class:
                    continue
                ordinal = 0
                for week in range(-history, after):
                    plans = []
                    if week % c["weekly_every"][b.tier] == 0:
                        plans.append("WEEKLY")
                    if c["mock_every"][b.tier] and (week + 1) % c["mock_every"][b.tier] == 0:
                        plans.append("MOCK")
                    for kind in plans:
                        ordinal += 1
                        key = f"{b.index}:{level}:{ordinal}"
                        if kind == "WEEKLY":
                            subject = subjects[ordinal % len(subjects)]
                            pool = self.pool[(b.provider, subject)]
                            papers, minutes = (pool[ordinal % len(pool)],), c["weekly_minutes"]
                        else:
                            papers = tuple(self.pool[(b.provider, s)][ordinal % len(self.pool[(b.provider, s)])] for s in subjects)
                            minutes = c["mock_minutes"]
                        day = (b.index + (kind == "MOCK") * 2) % 6
                        start = self.anchor + timedelta(weeks=week, days=day, minutes=270 + (b.index % 8) * 30)
                        end = start + timedelta(minutes=minutes + c["window_extra_minutes"])
                        name = f"{c['marker']} {b.name[len(c['marker']) + 1:]} {level} {kind.title()} {ordinal}"
                        yield Exam(key, self.uid("plan", key), self.uid("exam", key), self.uid("exam-version", key), self.uid("job", key),
                                   b.index, level, kind, name, papers, minutes, start, end, _unit("cancel", c["seed"], key) < c["cancelled_share"])

    def audience(self, e: Exam) -> list:
        """(student, enrollment id, batch id), one row per student, as exam_generation.resolve_audience gives for these batches."""
        return [(m.student, m.id, self.batches[m.batches[0]].id) for m in map(self.memberships.__getitem__, self.by_class[(e.branch, e.class_level)])]

    def exam_questions(self, e: Exam):
        """(order, paper, number) in delivery order (outbox.build_payload: paper position, then question position)."""
        order = 0
        for p in e.papers:
            paper = self.papers[p]
            for k in range(self.config["questions_per_paper"]):
                order += 1
                yield order, paper, paper.first_question + k

    def correct_of(self, paper: Paper, number: int) -> str:
        key = paper.subject + str(number)
        got = self._correct.get(key)
        if got is None:
            got = self._correct[key] = question(self.config, paper.subject, number)["correct"]
        return got

    def outcome(self, e: Exam, student: int):
        """None = missed; otherwise (started, submitted, status, reason, answers). answers = [(order, letter)]. Scored like reevaluation.score."""
        c = self.config
        if _unit("attend", c["seed"], e.key, student) >= c["attendance"]:
            return None
        rng = random.Random(f"{c['seed']}|{e.key}|{student}")
        started = e.start + timedelta(minutes=rng.randint(0, c["window_extra_minutes"] - 1))
        expires = started + timedelta(minutes=e.minutes)
        auto = rng.random() < c["auto_submit_share"]
        submitted = expires if auto else started + timedelta(minutes=e.minutes * rng.uniform(0.6, 1.0))
        ability = 0.35 + 0.55 * _unit("ability", c["seed"], student)
        answers = []
        for order, paper, number in self.exam_questions(e):
            if rng.random() < c["answered_share"]:
                right = self.correct_of(paper, number)
                letter = right if rng.random() < ability else "ABCD"[("ABCD".index(right) + rng.randint(1, 3)) % 4]
                answers.append((order, letter, right))
        return started, submitted, expires, "AUTO_SUBMITTED" if auto else "SUBMITTED", "TIME_EXPIRED" if auto else "STUDENT", answers

    @staticmethod
    def score(total_questions: int, answers: list) -> dict:
        correct = sum(a[1] == a[2] for a in answers)
        attempted = len(answers)
        incorrect = attempted - correct
        return {"total_questions": total_questions, "attempted": attempted, "correct": correct, "incorrect": incorrect,
                "unanswered": total_questions - attempted, "total_marks": total_questions * 4, "obtained": correct * 4 - incorrect,
                "accuracy": round(correct / attempted * 100, 2) if attempted else 0.0}

    def email(self, kind: str) -> str:
        return f"{self.config['marker'].lower()}.{kind}@{DOMAIN}"
