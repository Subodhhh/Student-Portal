# Load-test environment (Computer 1)

An isolated copy of the platform for load testing: an **override** of the root `docker-compose.yml` (which is never edited), its
own project (`student-portal-loadtest`), containers (`lt-*`), images (`loadtest/<service>:<git-sha>`), loopback-only ports (18xxx),
network, volumes, Redis, service keys and env files. The dev stack (`student-portal`) is not touched by anything here.

Budget: ₹0. Free/open-source only: Docker, PostgreSQL 18, Prometheus, Grafana OSS, cAdvisor, exporters, k6 OSS.

## Layout
| Path | Purpose |
|---|---|
| `lt.sh` | The only entry point (pins project, override, env files; runs the branch guard before any database use; never `down -v`/prune) |
| `compose.loadtest.yml` | The override. Every service is opt-in through a profile (an operating mode) and has a memory limit |
| `config/*.env.example`, `config/init_env.py` | Templates, and the helper the OPERATOR runs to create the real git-ignored `*.env` files (hidden prompts, random keys, 0600) |
| `config/targets.json` | Branch-guard targets: expected Neon branch/endpoint IDs, local database identities, dev deny-list. IDs only |
| `guard/` | Branch guard + tests |
| `postgres/initdb/` | First-start script of the local cluster: two databases, two owner roles, no cross-database access |
| `monitoring/` | Prometheus (scrape + k6 remote write, native histograms), Grafana provisioning, host-side Docker stats sampler (cAdvisor is disabled: no per-container data on Docker Desktop) |
| `k6/` | k6 scripts (`smoke.js`: health only; `health.js`, `browse.js`, `run.ps1`: Stage 2 baseline from Computer 2, API only) |
| `generator/` | Stage 1 loader: deterministic plan, CSV papers, API client, resumable loader, preflight, fake API + tests (`docs/stage1-loader.md`) |
| `bulk/` | Stage 2 dataset: deterministic generator, resumable COPY loader, read-only validator, application checks (`docs/stage2-dataset.md`) |
| `validate/` | Read-only Stage 1 validation (plan vs actual, business rules, Institute → Student projection) + tests |
| `reports/TEMPLATE.md`, `docs/stage1-checklist.md` | Report structure and the Stage 1 runbook |
| `test_compose_isolation.py` | Proves (from rendered models only) that nothing collides with the dev stack |

## One-time setup (operator)
```bash
python3 -m venv tests/load/.venv && tests/load/.venv/bin/pip install -r tests/load/requirements.txt
cp tests/load/config/compose.env.example tests/load/config/compose.env          # then set NEW random keys
cp tests/load/config/monitoring.env.example tests/load/config/monitoring.env    # Grafana admin password
# Stage 1 (Neon):  cp config/neon/{student,institute}.env.example -> *.env and paste the load_test_* connection strings yourself
# Stages 2-3:      cp config/local/{student,institute,postgres}.env.example -> *.env with new local passwords
```

## Branch guard
```bash
LT_TARGET=neon tests/load/lt.sh guard identify neon-student            # prints the IDs the server reports (never the URL)
LT_TARGET=neon tests/load/lt.sh guard pin neon-student --confirm <branch_id>   # only after the operator confirmed them
LT_TARGET=neon tests/load/lt.sh guard check neon                       # both targets; exit 1 = refused
```
Refuses: unpinned targets, any other branch/endpoint/project, the two development branches (deny-list, even if pinned by
mistake), a `MIGRATION_DATABASE_URL` on another database, both targets being the same database, a local target answering from
Neon, and a local database that was recreated (cluster system identifier + database OID changed). All sessions are read-only.

## Operating modes (never all at once on 3.9 GB WSL)
| Mode | Command | Approx. RAM |
|---|---|---|
| Local PostgreSQL only (generation, validation) | `LT_TARGET=local lt.sh up db` | ≤ 768 MB |
| Application against the target (guard first) | `LT_TARGET=neon\|local lt.sh up app` | ~1.4–2 GB (limits sum 3.6 GB) |
| Monitoring | `LT_TARGET=… lt.sh up monitoring` + `lt.sh sampler start` | ≤ 0.7 GB (Grafana ≤ 256 MiB) |
| Co-located k6 | `LT_TARGET=… lt.sh k6 smoke.js` | ≤ 768 MB |
| Stop everything of this project (volumes kept) | `LT_TARGET=… lt.sh stop` | — |

`student-profile-service` and `student-ranking-service` are in the `optional` profile; the frontends are never started (their
bundles call the DEV ports).

## Safety rules
* No real env file is created by tooling; connection strings and passwords are never printed (the guard redacts errors).
* No migrations run here: `MIGRATION_DATABASE_URL` stays empty; local schemas are restored from the Neon load-test branches.
* Every Neon session starts with `lt.sh guard check neon`; every local session with `lt.sh guard check local`.
* Group B operations (Neon writes, `pg_stat_statements` on Neon, stopping the dev stack, bulk loading, pausing the reference
  sync) require explicit approval before they are run.
