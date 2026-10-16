# Institute Question and Paper Service

Question bank, versioned papers, CSV/XLSX extraction, private file storage, paper sharing.

* Entry point: `inst_paper.main:app` (port 8000 inside the network). Health: `/health`, readiness: `/ready`. API docs: `/api/paper/docs`.
* Shared code: `../institute-common` (models, security, ownership guard). This service may **write only the tables it owns** (`institute_common/ownership.py`).
* Migrations: `alembic upgrade head` (own version table `inst_paper_alembic_version`; baseline adopts existing tables, forward-only).
* Tests: the cross-service suite in `../institute-integration-tests` exercises this service through the gateway.
* Configuration: see `.env.example`.
