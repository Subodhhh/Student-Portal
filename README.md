# Online examination platform — Student Portal + Institute Portal

| Portal | URL (local) | Code | Backend |
|---|---|---|---|
| Student Portal | http://localhost:8080 | `frontend/` | `backend/student-*-service` (FastAPI), Neon branch **subodh_dev** |
| Institute Portal | http://localhost:8081 | `institute-frontend/` | 7 FastAPI services behind the gateway on :8010 (`backend/institute-*`), Neon branch **institute_portal_test** |

The two databases are separate. The portals talk only through authenticated service APIs (scoped keys): the Institute creates and looks
up global student identities in the Student authentication service, pushes a read projection of its providers/branches/batches/enrollments
to the Student enrollment service, delivers generated exams to the Student test service and reads attempts/results back. Details:
[docs/institute-portal/MICROSERVICES.md](docs/institute-portal/MICROSERVICES.md).

## Run

```bash
cp backend/student-services.env.example   backend/student-services.env     # DATABASE_URL of subodh_dev (never commit)
cp backend/institute-services.env.example backend/institute-services.env   # DATABASE_URL of institute_portal_test (never commit)
# root .env: one random key per service contract (INSTITUTE_*_KEY, STUDENT_*_SERVICE_KEY) — see docker-compose.yml
docker compose up -d --build
```

Migrations are applied per service with `alembic upgrade head` (Student services: their own chains; Institute: identity, organization,
academics, enrollment, paper, exam, operations). Test every migration on a disposable copy first (`pg_dump --schema-only` into a local
PostgreSQL, then upgrade + `alembic check`).

Create an Institute Super Admin: `docker compose exec -e INSTITUTE_ADMIN_PASSWORD=... institute-identity-service python -m inst_identity.cli create-super-admin you@example.com "Your Name"`.
Students are created by an institute (Students & enrollments) and set their password with "Forgot password" (in the local stack the link
is written to the `student-authentication-service` log because `EMAIL_BACKEND=console`).

## Verify

```bash
python3 scripts/e2e/platform_e2e.py <STAFF_CODE> <PASSWORD>     # cross-portal black-box flow (lookup, enrollment, papers, exams, attempts, composite, roles)
python3 scripts/e2e/ui_walkthrough.py ws://127.0.0.1:3011/ qa.json <STAFF_CODE> <PASSWORD> out/   # every page per role in Chromium
cd backend/institute-integration-tests && python -m pytest -q    # add TEST_DATABASE_URL=<disposable PG> for the PostgreSQL run
```

Progress and decisions: [docs/production-readiness/MASTER_TRACKER.md](docs/production-readiness/MASTER_TRACKER.md).
