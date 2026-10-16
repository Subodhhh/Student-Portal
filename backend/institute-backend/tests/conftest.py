import os
from uuid import uuid4

os.environ.update(
    APP_ENV="dev",
    DATABASE_URL="sqlite://",
    REDIS_URL="redis://localhost:6379",
    CORS_ORIGINS="http://localhost:5174",
    SESSION_COOKIE_SECURE="false",
    ARGON2_TIME_COST="1",
    ARGON2_MEMORY_COST_KIB="1024",
    ARGON2_PARALLELISM="1",
    FRONTEND_BASE_URL="http://localhost:5174",
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core import email, redis_store  # noqa: E402
from app.core.passwords import hash_password  # noqa: E402
from app.database import Base, get_db, utcnow  # noqa: E402
from app.main import app  # noqa: E402
from app import models as _models  # noqa: E402,F401
from app.models.org import Branch, Provider  # noqa: E402
from app.models.staff import Staff, StaffRoleAssignment  # noqa: E402

ORIGIN = {"Origin": "http://localhost:5174"}
PASSWORD = "correct horse battery staple"


# Opt-in: run the whole suite against a REAL (disposable!) PostgreSQL that already has the migrated schema,
# e.g. TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5439/inst_verify. Every table is truncated
# before each test, so never point this at a database holding data you care about.
TEST_PG = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def db_factory():
    if TEST_PG:
        from sqlalchemy import inspect, text
        engine = create_engine(TEST_PG)
        names = [t for t in inspect(engine).get_table_names() if "alembic_version" not in t]
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        engine.dispose()
        return
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def outbox():
    sender = email.MemoryEmailSender()
    email.set_sender(sender)
    yield sender.outbox
    email.set_sender(None)


@pytest.fixture()
def client(db_factory, outbox):
    redis_store.set_client(fakeredis.FakeRedis(decode_responses=True))

    def override():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app, base_url="http://localhost") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def world(db_factory):
    """Two providers with two branches each, plus helpers to create staff."""
    with db_factory() as db:
        w = {}
        for key in ("p1", "p2"):
            p = Provider(name=f"Provider {key}", email=f"{key}@example.com")
            db.add(p)
            db.flush()
            w[key] = p.provider_id
            for bkey in ("b1", "b2"):
                b = Branch(provider_id=p.provider_id, name=f"{key}-{bkey}")
                db.add(b)
                db.flush()
                w[f"{key}{bkey}"] = b.branch_id
        db.commit()
    return w


@pytest.fixture()
def make_staff(db_factory):
    counter = {"n": 0}

    def _make(roles=(), *, code=None, verified=True, active=True, password=PASSWORD, email_addr=None):
        counter["n"] += 1
        n = counter["n"]
        with db_factory() as db:
            s = Staff(
                staff_code=code or f"STF{n:06d}", email=email_addr or f"staff{n}@example.com",
                full_name=f"Staff Member {n}", password_hash=hash_password(password) if password else None,
                email_verified_at=utcnow() if verified else None,
                status="ACTIVE" if active else "INACTIVE",
            )
            db.add(s)
            db.flush()
            for role, provider, branch in roles:
                db.add(StaffRoleAssignment(staff_id=s.staff_id, role=role.value, provider_id=provider, branch_id=branch))
            db.commit()
            return s.staff_id, s.staff_code

    return _make


@pytest.fixture()
def login(client, outbox):
    """login(code) -> dict with csrf header; handles MFA via the outbox."""
    def _login(code, password=PASSWORD, http=None):
        c = http or client
        r = c.post("/api/v1/auth/login", json={"staff_code": code, "password": password}, headers=ORIGIN)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] == "MFA_REQUIRED":
            otp = outbox[-1].body.split("code is ")[1][:6]
            r = c.post("/api/v1/auth/mfa/verify", json={"challenge_id": body["challenge_id"], "otp": otp}, headers=ORIGIN)
            assert r.status_code == 200, r.text
            body = r.json()
        return {**ORIGIN, "X-CSRF-Token": body["me"]["csrf_token"]}

    return _login


def new_id():
    return uuid4()
