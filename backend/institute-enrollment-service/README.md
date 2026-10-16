# Institute Enrollment Service

Provider-student enrollments, batch membership, history, transfers, eligibility; reuses the canonical Student identity through the Student authentication service API.

* Entry point: `inst_enrollment.main:app` (port 8000 inside the network). Health: `/health`, readiness: `/ready`. API docs: `/api/enrollment/docs`.
* Shared code: `../institute-common` (models, security, ownership guard). This service may **write only the tables it owns** (`institute_common/ownership.py`).
* Migrations: `alembic upgrade head` (own version table `inst_enrollment_alembic_version`; baseline adopts existing tables, forward-only).
* Tests: the cross-service suite in `../institute-integration-tests` exercises this service through the gateway.
* Configuration: see `.env.example`.
