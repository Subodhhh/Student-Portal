# Stage N — BEFORE | AFTER report

Every number below is a measurement taken during this run, with its source. Estimates, if any, are labelled "estimate".

## 1. Environment
- Date/time (UTC), git commit, `LT_TAG`, image digests
- Target: Neon (`branch_id`, `endpoint_id`) or local (database identity) — output of `lt.sh guard check`
- Computer 1: CPU, WSL RAM, free disk, power state; k6 location (co-located | Computers 2/3)
- Services running and their memory limits

## 2. Data (per database)
| Table | Rows | Total size | Δ vs previous report |
|---|---:|---:|---:|
- Database sizes (`pg_database_size`), largest tables, index sizes
- Synthetic rows inserted in this stage (by entity), versus target

## 3. Integrity and business rules
- Validation suite: checks run / passed / failed (list every failure)
- Institute → Student projection comparison: only-in-Institute, only-in-Student, field differences (must be 0 / 0 / 0)
- Reference sync status (`sync_status`): pending, rejected

## 4. API performance (k6)
| Scenario | Max VUs reached | Duration | Requests | RPS | p50 | p95 | p99 | Error rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
- Threshold results; the step at which a stop condition fired (and which one)
- "Concurrency achieved" = the highest VU level k6 actually sustained within thresholds (k6 output attached)

## 5. Resources
- Per-container CPU / memory (cAdvisor or `docker stats` sampler), WSL available memory, swap
- PostgreSQL: connections (peak / max), cache hit ratio, locks, temp files
- Redis: memory, connected clients, ops/s
- Neon only: compute (CU-hrs), storage, network transfer from the console, before and after

## 6. Database bottlenecks
- Top queries by total time (`pg_stat_statements`), with `EXPLAIN (ANALYZE, BUFFERS)` of the worst
- Missing-index candidates (evidence only; no index is created without approval)

## 7. Application bottlenecks and observations

## 8. Comparison with the previous stage

## 9. Deviations, failures and open issues
