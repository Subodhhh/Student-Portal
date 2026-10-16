# Institute Academics Service

Subjects, provider/batch subjects, batches, teacher-subject assignments.

* Entry point: `inst_academics.main:app` (port 8000 inside the network). Health: `/health`, readiness: `/ready`. API docs: `/api/academics/docs`.
* Shared code: `../institute-common` (models, security, ownership guard). This service may **write only the tables it owns** (`institute_common/ownership.py`).
* Migrations: `alembic upgrade head` (own version table `inst_academics_alembic_version`; baseline adopts existing tables, forward-only).
* Tests: the cross-service suite in `../institute-integration-tests` exercises this service through the gateway.
* Configuration: see `.env.example`.
