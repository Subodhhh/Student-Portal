# Institute-Student-Portal — Claude Code Instructions

## 1. Repository and Git Safety

- This is the PRIMARY and authoritative development repository.
- Old reference repository: ~/Student-Portal-Review/Student-Portal.
- Never modify, delete, commit, reset, checkout, clean or merge anything in the old repository.
- Perform all development, testing and Git operations in this repository.
- Before modifying files, verify the current directory and Git branch.
- Never discard existing user changes or run destructive Git commands without explicit approval.
- Never commit or deploy unless explicitly requested.

## 2. Application Architecture

This is a multi-portal online examination platform.

- Institute frontend: institute-frontend/ (React + Vite).
- Student frontend: student-frontend/ (React + Vite).
- Institute backend: institute-backend/ (7 FastAPI services behind gateway :8010).
- Student backend: student-backend/ (independently deployable FastAPI services).
- Shared Institute library: institute-backend/institute-common/.
- Integration tests: institute-backend/institute-integration-tests/.
- Cross-portal E2E tests: tests/e2e/.
- Deployment and infrastructure: deploy/, docker-compose.yml.

Local URLs:
- Student Portal: http://localhost:8080
- Institute Portal: http://localhost:8081

## 3. Database and Service Boundaries

- Institute and Student databases are separate.
- Student database: Neon branch subodh_dev.
- Institute database: Neon branch institute_portal_test.
- Never assume the two portals share database tables.
- Cross-portal communication must use authenticated service APIs and scoped service keys.
- Never expose service credentials in frontend code or commit secrets.

Ownership:
- Student authentication service owns canonical global student identity and credentials.
- Institute services own provider, branch, batch, enrollment, question bank, paper, exam planning and scheduling data.
- Student services own student attempts, saved answers, evaluation and student results.
- Follow existing service boundaries. Do not introduce direct cross-database access.

## 4. Established Business Rules

- Students have one global identity and may enroll with multiple providers.
- Institutes create and look up global student identities through the Student authentication service.
- Institute enrollment data is projected to the Student enrollment service through authenticated APIs.
- Generated exams are delivered to the Student test service.
- Student attempts and results are read back through service APIs.
- Preserve existing composite-test, global-exam, scheduling, batch eligibility, role-permission and negative-marking rules.
- Before changing business logic, inspect the relevant implementation and documentation.

## 5. Development Rules

- Inspect existing implementations before creating new files or abstractions.
- Prefer small, targeted changes over unnecessary refactoring.
- Reuse existing components, services, schemas and utilities.
- Do not duplicate existing functionality.
- Follow the conventions and architecture of the affected service.
- Preserve API compatibility unless a change is explicitly required.
- Validate inputs and handle errors properly.
- Never silently weaken authentication, authorization, validation or audit logging.
- Preserve timestamps, IP audit fields and soft-delete conventions where applicable.
- Do not invent database fields, API contracts or business rules.

## 6. Database and Migration Safety

- Never run migrations against production or shared databases without explicit approval.
- Test migrations on a disposable database first.
- Each service owns its migration chain.
- Inspect migration dependencies before modifying schemas.
- Never reset, truncate or delete database data without explicit approval.
- Never print or expose secrets from environment files.

## 7. Testing and Verification

- Run targeted tests for the affected service first.
- Run relevant frontend tests when UI code changes.
- Run integration or E2E tests when cross-service behavior changes.
- Report exactly which tests passed, failed or were skipped.
- Do not claim a feature works without appropriate verification.
- Avoid running the entire test suite when targeted tests are sufficient.

## 8. Token and Context Efficiency

- Read only files relevant to the current task.
- Do not repeatedly scan the entire repository.
- Use RTK-supported commands for concise terminal output.
- Batch independent read-only inspection commands where practical.
- Avoid dumping large logs; inspect relevant sections.
- Summarize findings instead of repeating source code.
- Use subagents for independent investigations when beneficial.
- Do not sacrifice correctness, security or necessary testing to save tokens.

## 9. Project Documentation

Important references:
- README.md — setup and application overview.
- docs/institute-portal/MICROSERVICES.md — service architecture.
- docs/production-readiness/MASTER_TRACKER.md — progress and decisions.
- docker-compose.yml — local service configuration.
- deploy/ — deployment templates.

Read the relevant documentation before changing architecture or service contracts.

## 10. Working Protocol

For each task:

1. Confirm repository, branch and working-tree status.
2. Understand the requested outcome and identify relevant files.
3. Inspect existing implementation and applicable documentation.
4. Present a concise plan for substantial changes.
5. Make only the requested changes.
6. Run relevant tests and report results.
7. Summarize modified files, behavior changes and remaining risks.

If requirements are ambiguous or a change could affect data, security, service ownership or deployment, ask before proceeding.

## Subagent Delegation Strategy

Use the configured project subagents selectively:

- `code-investigator` (Haiku): Delegate read-only repository exploration, tracing bugs, locating files, analysing logs and understanding existing implementations.
- `developer` (Sonnet): Delegate focused implementation, bug fixes, frontend/backend changes and test writing.
- `architecture-reviewer` (Opus): Delegate complex architecture, cross-service integration, database design, security reviews and difficult debugging.

Delegation rules:
- Keep the main Claude session on Sonnet.
- Prefer Haiku for investigation before implementation when repository exploration is substantial.
- Use Sonnet for normal development.
- Use Opus only when complexity or risk justifies it.
- Do not invoke subagents for trivial tasks.
- For substantial changes, investigate first, implement second, and request an independent architecture review when warranted.
- Do not delegate conflicting edits to multiple agents working on the same files.
- The main agent remains responsible for reviewing results, running tests and reporting completion.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
