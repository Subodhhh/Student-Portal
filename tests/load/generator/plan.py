"""Deterministic plan of the synthetic data of one stage: the same seed always yields the same entities with the same natural keys,
so a resumed or repeated run asks for exactly the same rows (the loader's idempotency starts here).

Natural keys (unique by construction, and how the loader finds a row that already exists):
  provider -> its email (unique in the database)        branch -> (provider, name)      batch -> (branch, name)
  staff    -> its email (unique in the database)        student -> its email (unique)   enrollment -> (student, branch)
  paper    -> (branch, subject, content hash; the paper service refuses an identical paper with its existing version)
All names start with the stage marker ("LT1 ...") and every e-mail is on loadtest.example (RFC 2606: never delivered; the
application's validator rejects .invalid/.test)."""
import hashlib
import itertools
import json
import random
from dataclasses import dataclass, field

STAGE1 = {
    "stage": 1, "marker": "LT1", "seed": 20261103,
    "providers": 100,
    "branches_per_provider": {1: 70, 2: 15, 0: 15},          # 100 branches: providers and branches are equal in total, not 1:1
    "batches_per_branch": (2, 3),
    "students": 1000,
    "memberships": {1: 350, 2: 400, 3: 150, 4: 100},         # exactly 2,000 enrollments: average 2.0, range 1-4
    "second_batch_share": 0.2,                                # memberships in two batches of the branch
    "subject_choice_share": 0.3,                              # memberships that choose some of their batch's subjects
    "shared_teacher_share": 0.1,                              # teachers who also teach at another provider
    # 20 questions per paper: one upload must finish well inside the gateway's 30 s proxy_read_timeout at ~200 ms per database
    # round trip (persist_validated_paper costs ~2-4 round trips per question). 1,000 per subject => 50 papers, 400 papers in all.
    "questions_per_subject": 1000, "questions_per_paper": 20,
    "academic_year": "2026-2027",
}
CANARY_STUDENTS = 5
SUBJECT_CODES = tuple(f"SUB{n:03d}" for n in range(1, 9))     # the 8 existing subjects (verified against the API at run time)
PROFILES = {                                                   # subjects a provider enables; together they cover all 8
    "NEET-I": ("SUB001", "SUB002", "SUB004"),
    "JEE-I": ("SUB001", "SUB003", "SUB004"),
    "NEET-II": ("SUB005", "SUB006", "SUB007"),
    "JEE-II": ("SUB005", "SUB006", "SUB008"),
}
PROFILE_WEIGHTS = {"NEET-I": 40, "JEE-I": 30, "NEET-II": 15, "JEE-II": 15}
DOMAIN = "loadtest.example"


@dataclass
class Provider:
    key: str
    name: str
    email: str
    profile: str
    subjects: tuple[str, ...]
    branches: list[str] = field(default_factory=list)


@dataclass
class Branch:
    key: str
    provider: str
    name: str
    city: str
    batches: list[str] = field(default_factory=list)


@dataclass
class Batch:
    key: str
    branch: str
    name: str
    subjects: tuple[str, ...]
    class_level: str
    start_date: str
    end_date: str


@dataclass
class Staff:
    key: str
    email: str
    full_name: str
    role: str                       # PROVIDER_ADMIN | TEACHER
    providers: list[str]            # role scope: the first is created with the identity, the rest are extra assignments


@dataclass
class Teaching:
    key: str
    staff: str
    branch: str
    subject: str
    batch: str | None               # None = every batch of that subject in the branch


@dataclass
class Membership:
    key: str
    student: str
    provider: str
    branch: str
    batches: list[str]
    chosen_subjects: dict[str, list[str]]     # batch -> chosen subjects (a batch missing here takes all its subjects)
    class_level: str


@dataclass
class Student:
    key: str
    email: str
    first_name: str
    last_name: str
    memberships: list[str]


@dataclass
class Paper:
    key: str
    subject: str
    branch: str
    title: str
    number: int
    first_question: int             # 1-based index within the subject


@dataclass
class Plan:
    config: dict
    providers: dict[str, Provider]
    branches: dict[str, Branch]
    batches: dict[str, Batch]
    staff: dict[str, Staff]
    teaching: dict[str, Teaching]
    students: dict[str, Student]
    memberships: dict[str, Membership]
    papers: dict[str, Paper]
    full: "Plan | None" = None          # set on a subset (the canary): the plan it was cut from

    def roles_of(self, s: Staff) -> list[str]:
        """The providers a staff member holds a role at WITHIN this plan (a subset keeps only its own providers)."""
        return [p for p in s.providers if p in self.providers]

    def fingerprint(self) -> str:
        """Identity of the FULL plan (a canary shares its full plan's fingerprint, so both use one journal)."""
        base = self.full or self
        blob = json.dumps({"config": {k: v for k, v in base.config.items()}, "summary": base.summary()}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def summary(self) -> dict:
        per_student = [len(s.memberships) for s in self.students.values()]
        return {
            "providers": len(self.providers), "providers_without_branch": sum(1 for p in self.providers.values() if not p.branches),
            "branches": len(self.branches), "batches": len(self.batches),
            "provider_subjects": sum(len(p.subjects) for p in self.providers.values()),
            "batch_subjects": sum(len(b.subjects) for b in self.batches.values()),
            "staff_identities": len(self.staff),
            "role_assignments": sum(len(self.roles_of(s)) for s in self.staff.values()),
            "teaching_assignments": len(self.teaching),
            "students": len(self.students), "enrollments": len(self.memberships),
            "memberships_min": min(per_student), "memberships_max": max(per_student),
            "memberships_avg": round(sum(per_student) / len(per_student), 3),
            "enrollment_batch_links": sum(len(m.batches) for m in self.memberships.values()),
            "papers": len(self.papers),
            "questions": len(self.papers) * self.config["questions_per_paper"],
            "questions_per_subject": {c: sum(1 for p in self.papers.values() if p.subject == c) * self.config["questions_per_paper"]
                                      for c in SUBJECT_CODES},
        }


def _weighted(rng: random.Random, weights: dict) -> str:
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def with_paper_size(config: dict, questions_per_paper: int) -> dict:
    if questions_per_paper < 1 or config["questions_per_subject"] % questions_per_paper:
        raise ValueError(f"questions per paper must divide {config['questions_per_subject']} (got {questions_per_paper})")
    return {**config, "questions_per_paper": questions_per_paper}


def build(config: dict = STAGE1) -> Plan:
    with_paper_size(config, config["questions_per_paper"])     # validates the paper size
    rng = random.Random(config["seed"])
    m = config["marker"]
    lower = m.lower()
    cities = ["Pune", "Mumbai", "Nagpur", "Nashik", "Delhi", "Jaipur", "Kota", "Chennai", "Hyderabad", "Kolkata", "Indore", "Lucknow"]

    # ---- providers and their branch counts
    counts = [n for n, k in config["branches_per_provider"].items() for _ in range(k)]
    assert len(counts) == config["providers"], "branches_per_provider must describe every provider"
    rng.shuffle(counts)
    providers: dict[str, Provider] = {}
    cycle = list(PROFILES)                     # the first providers WITH branches take each profile once: all 8 subjects covered
    for i in range(1, config["providers"] + 1):
        profile = cycle.pop(0) if cycle and counts[i - 1] else _weighted(rng, PROFILE_WEIGHTS)
        key = f"P{i:03d}"
        providers[key] = Provider(key, f"{m} Provider {i:03d}", f"{lower}.provider{i:03d}@{DOMAIN}", profile, PROFILES[profile])

    # ---- branches and batches
    branches: dict[str, Branch] = {}
    batches: dict[str, Batch] = {}
    for (pkey, p), n in zip(providers.items(), counts):
        for j in range(1, n + 1):
            bkey = f"{pkey}-B{j}"
            branch = Branch(bkey, pkey, f"{m} {p.key} Branch {j}", rng.choice(cities))
            branches[bkey] = branch
            p.branches.append(bkey)
            for k in range(1, rng.choice(config["batches_per_branch"]) + 1):
                # the first batch of a branch offers all of the provider's subjects; later ones sometimes only two
                subjects = p.subjects if k == 1 or rng.random() >= 0.3 else tuple(sorted(rng.sample(p.subjects, 2)))
                cls = "11TH" if rng.random() < 0.6 else "12TH"
                tkey = f"{bkey}-T{k}"
                batches[tkey] = Batch(tkey, bkey, f"{m} {p.profile} {cls[:2]}th {'ABCD'[k - 1]}", subjects, cls,
                                      "2026-11-15", "2027-05-31")
                branch.batches.append(tkey)
    assert len(branches) == sum(n * k for n, k in config["branches_per_provider"].items())

    # ---- staff: one Provider Admin per provider; one teacher per (provider with branches, subject), some shared across providers
    staff: dict[str, Staff] = {}
    for pkey, p in providers.items():
        skey = f"ADM-{pkey}"
        staff[skey] = Staff(skey, f"{lower}.admin.{pkey.lower()}@{DOMAIN}", f"{m} Admin {pkey}", "PROVIDER_ADMIN", [pkey])
    teacher_of: dict[tuple[str, str], str] = {}
    by_subject: dict[str, list[str]] = {}
    n_teacher = 0
    for pkey, p in providers.items():
        if not p.branches:
            continue
        for subj in p.subjects:
            pool = [t for t in by_subject.get(subj, []) if pkey not in staff[t].providers]
            if pool and rng.random() < config["shared_teacher_share"]:
                tkey = rng.choice(pool)
                staff[tkey].providers.append(pkey)
            else:
                n_teacher += 1
                tkey = f"TCH-{n_teacher:04d}"
                staff[tkey] = Staff(tkey, f"{lower}.teacher{n_teacher:04d}@{DOMAIN}", f"{m} Teacher {n_teacher:04d}", "TEACHER", [pkey])
                by_subject.setdefault(subj, []).append(tkey)
            teacher_of[(pkey, subj)] = tkey

    teaching: dict[str, Teaching] = {}
    for bkey, br in branches.items():
        for subj in providers[br.provider].subjects:
            tkey = teacher_of[(br.provider, subj)]
            with_subject = [t for t in br.batches if subj in batches[t].subjects]
            if not with_subject:
                continue
            targets = [None] if rng.random() < 0.7 else with_subject           # whole branch, or batch by batch
            for t in targets:
                key = f"{tkey}@{bkey}:{subj}:{t or 'ALL'}"
                teaching[key] = Teaching(key, tkey, bkey, subj, t)

    # ---- students and memberships (distinct providers per student; popular providers attract more students)
    active = [k for k, p in providers.items() if p.branches]
    weight = {k: 1.0 / (rank ** 0.8) for rank, k in enumerate(rng.sample(active, len(active)), start=1)}
    per_student = [n for n, k in config["memberships"].items() for _ in range(k)]
    assert len(per_student) == config["students"]
    rng.shuffle(per_student)
    students: dict[str, Student] = {}
    memberships: dict[str, Membership] = {}
    for i, k in enumerate(per_student, start=1):
        skey = f"S{i:04d}"
        chosen_providers: list[str] = []
        while len(chosen_providers) < k:
            cand = rng.choices(active, weights=[weight[a] for a in active], k=1)[0]
            if cand not in chosen_providers:
                chosen_providers.append(cand)
        student = Student(skey, f"{lower}.student{i:04d}@{DOMAIN}", f"{m}Student", f"{i:04d}", [])
        for pkey in chosen_providers:
            bkey = rng.choice(providers[pkey].branches)
            first = rng.choice(branches[bkey].batches)
            picked = [first]
            same_class = [t for t in branches[bkey].batches if t != first and batches[t].class_level == batches[first].class_level]
            if same_class and rng.random() < config["second_batch_share"]:
                picked.append(rng.choice(same_class))
            chosen: dict[str, list[str]] = {}
            subs = batches[first].subjects
            if len(subs) >= 2 and rng.random() < config["subject_choice_share"]:
                chosen[first] = sorted(rng.sample(subs, rng.randint(1, len(subs) - 1)))
            mkey = f"{skey}@{bkey}"
            memberships[mkey] = Membership(mkey, skey, pkey, bkey, picked, chosen, batches[first].class_level)
            student.memberships.append(mkey)
        students[skey] = student

    # ---- question papers: questions_per_subject per subject, in papers placed round-robin on branches offering that subject
    papers: dict[str, Paper] = {}
    per_paper = config["questions_per_paper"]
    for subj in SUBJECT_CODES:
        hosts = sorted(b for b, br in branches.items() if any(subj in batches[t].subjects for t in br.batches))
        assert hosts, f"no branch offers {subj}: the plan cannot cover all 8 subjects"
        rng.shuffle(hosts)
        for n in range(1, config["questions_per_subject"] // per_paper + 1):
            key = f"{subj}-{n:03d}"
            papers[key] = Paper(key, subj, hosts[(n - 1) % len(hosts)], f"{m} {subj} Paper {n:03d}", n, (n - 1) * per_paper + 1)
    return Plan(config, providers, branches, batches, staff, teaching, students, memberships, papers)


def canary(plan: Plan, students: int = CANARY_STUDENTS) -> Plan:
    """A small, deterministic slice of the FULL plan (same keys, so the full run later finds these rows instead of re-creating them):
    the pair of providers (one with two branches, different subject profiles) with the most students wholly inside it; their subjects,
    branches, batches, admins, teachers (roles at these providers only), teaching assignments; up to `students` of those students
    (at least one member of both providers, one with chosen subjects when available); one paper on each of two subjects hosted there."""
    two = sorted(k for k, p in plan.providers.items() if len(p.branches) == 2)
    one = sorted(k for k, p in plan.providers.items() if p.branches)
    best = None
    for a, b in itertools.product(two, one):
        if a == b or plan.providers[a].profile == plan.providers[b].profile:
            continue
        pair = {a, b}
        inside = [s for s in plan.students.values() if {plan.memberships[m].provider for m in s.memberships} <= pair]
        both = [s for s in inside if len(s.memberships) == 2]
        if both and (best is None or len(inside) > len(best[1])):
            best = (pair, inside)
    if best is None:
        raise ValueError("no provider pair yields a canary with a two-provider student")
    pair, inside = best
    chooser = [s for s in inside if any(plan.memberships[m].chosen_subjects for m in s.memberships)]
    picked = {sorted(s.key for s in inside if len(s.memberships) == 2)[0]}
    if chooser:
        picked.add(sorted(s.key for s in chooser)[0])
    for s in sorted(s.key for s in inside):
        if len(picked) >= students:
            break
        picked.add(s)
    branches = {k for p in pair for k in plan.providers[p].branches}
    papers, seen = [], set()
    for k, p in sorted(plan.papers.items()):
        if p.branch in branches and p.subject not in seen:
            papers.append(k)
            seen.add(p.subject)
        if len(papers) == 2:
            break
    sub = Plan(
        config=plan.config,
        providers={k: plan.providers[k] for k in sorted(pair)},
        branches={k: plan.branches[k] for k in sorted(branches)},
        batches={k: t for k, t in plan.batches.items() if t.branch in branches},
        staff={k: s for k, s in plan.staff.items() if set(s.providers) & pair},
        teaching={k: t for k, t in plan.teaching.items() if t.branch in branches},
        students={k: plan.students[k] for k in sorted(picked)},
        memberships={m: plan.memberships[m] for k in sorted(picked) for m in plan.students[k].memberships},
        papers={k: plan.papers[k] for k in papers},
        full=plan,
    )
    return sub
