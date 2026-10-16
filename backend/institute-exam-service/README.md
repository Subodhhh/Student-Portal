# Institute Exam Management Service

Exam plans, generation jobs, immutable exams, assignments, outbox delivery to the Student services, result corrections, worker.

* Entry point: `inst_exam.main:app` (port 8000 inside the network). Health: `/health`, readiness: `/ready`. API docs: `/api/exam/docs`.
* Shared code: `../institute-common` (models, security, ownership guard). This service may **write only the tables it owns** (`institute_common/ownership.py`).
* Migrations: `alembic upgrade head` (own version table `inst_exam_alembic_version`; baseline adopts existing tables, forward-only).
* Tests: the cross-service suite in `../institute-integration-tests` exercises this service through the gateway.
* Configuration: see `.env.example`.
