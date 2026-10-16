# Institute Identity and Access Service

Staff identities, roles, sessions, MFA, password policy, audit log (single writer) and the introspection API used by every other service.

* Entry point: `inst_identity.main:app` (port 8000 inside the network). Health: `/health`, readiness: `/ready`. API docs: `/api/identity/docs`.
* Shared code: `../institute-common` (models, security, ownership guard). This service may **write only the tables it owns** (`institute_common/ownership.py`).
* Migrations: `alembic upgrade head` (own version table `inst_identity_alembic_version`; baseline adopts existing tables, forward-only).
* Tests: the cross-service suite in `../institute-integration-tests` exercises this service through the gateway.
* Configuration: see `.env.example`.
