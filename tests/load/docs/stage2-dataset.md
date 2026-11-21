# Stage 2 dataset

`tests/load/bulk/` builds the Stage 2 dataset as a pure function of (seed, fraction, anchor date), writes it with COPY into the guarded local
databases, and validates it read-only. The same input always gives the same rows and IDs.

## Commands (`LT_TARGET=local tests/load/lt.sh stage2 <command> [--fraction F]`)
| Command | Effect |
|---|---|
| `schema` | Builds both schemas from the repository's migration chains; refuses a database that already has tables |
| `plan` | Counts the rows per table; touches no database |
| `load` | COPY, committed every 50,000 rows; a rerun continues from each table's committed row count |
| `validate` | Read-only checks; writes `results/stage2/state-<fraction>-validation.json` |
The default is the 1 % pilot. Above 5 % needs `--approved-full`. Set `LT_SYNTH_PASSWORD_HASH` to a pre-computed Argon2 hash for password logins;
without it passwords stay empty and sessions must be seeded.

## Loading from Computer 2
PostgreSQL stays on Computer 1; Computer 2 generates the rows and streams COPY over the LAN. The pilot's `lt_student` and `lt_institute` are never a
target: `prepare --pair stage2|lantest` creates separate databases on the same server, with their own env files and pinned identities.
| Step | Where | What |
|---|---|---|
| `stage2 prepare --pair P`, `stage2 bundle` | Computer 1 | Databases, migrations, pins; `results/bundle/` code zip and secrets zip (URLs, password hash) |
| `python -m bulk fingerprint --fraction 0.01 --anchor 2026-11-04` | Both | Must print the same value, proving both generate the same rows |
| `python -m bulk load --pair P` with `LT_PG_CONNECT_HOST=<LAN ip>:15432` | Computer 2 | The same loader; the state file stays on Computer 2 and a rerun resumes |
| `stage2 drop --pair lantest` | Computer 1 | Removes the disposable test pair; no other pair can be dropped |
`LT_PG_CONNECT_HOST` accepts private addresses only, and the guard still requires the pinned identity on every run. Computer 1 reaches the Compose
loopback port through a Windows port forward limited by a firewall rule to Computer 2's address; remove both after the session.

## Shape
| Entity | Rule |
|---|---|
| Providers, branches | 10,000 each: 70 % of providers with 1 branch, 15 % with 2, 15 % with none |
| Branch tiers | Large 1 % of branches (40 % of enrollments), medium 9 % (40 %), small 90 % (20 %) |
| Batches | 3 per class in large branches, 2 in medium and small; large and medium teach 11th and 12th, small one class |
| Students, enrollments | 50,000 / 100,000: 1-4 enrollments at distinct providers; 20 % of enrollments hold a second batch of their class |
| Staff | 1 super admin, 1 Provider Admin per provider, 1 teacher per provider subject, 1 Branch Admin per large or medium branch |
| Questions, papers | 50,000 per subject in 50-question papers, owned by large and medium branches; others use `paper_shares` |
| Exams | Past 16 weeks and next 2. Large: weekly + 4-weekly mock per class; medium: fortnightly + mock; small: 4-weekly |
| Test size | Weekly: one paper, 50 questions, 60 minutes. Mock: one paper per provider subject, 150 questions, 180 minutes |
| Outcomes | 78 % attend (10 % of them auto-submit on time), 22 % miss; 75 % of questions answered; 1.5 % of exams cancelled |
| Notifications | `TEST_SCHEDULED`, result and cancellation notices, as the worker would have delivered them; older than 7 days read |

Not generated: Global Tests, composite requests, weekly series rows, paper uploads and stored files, audit logs, billing, enrollment subject
choices, inactive enrollments, live or in-progress attempts (k6 creates those).

## Application checks (disposable fixture only)
`python -m bulk.schema <scratch dir>` starts a throwaway PostgreSQL on 127.0.0.1:55439 with empty template schemas. `bulk/appcheck/` then runs the
real service code against a dataset there: `run_student.py` drives each Student service in its own virtualenv, and `institute_routes.py` runs Institute
routes plus `build_payload`, audience resolution, generation replay and the worker steps. The scripts refuse any other database.
