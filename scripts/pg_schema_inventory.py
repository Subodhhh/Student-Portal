#!/usr/bin/env python3
"""Print a normalised inventory of a PostgreSQL schema (tables, columns, constraints, indexes, triggers) for diffing two databases.
Usage: pg_schema_inventory.py URL [--skip TABLE_REGEX]   (read-only; refuses non-local hosts)"""
import re
import sys

import psycopg

url, skip = sys.argv[1], None
if "--skip" in sys.argv:
    skip = re.compile(sys.argv[sys.argv.index("--skip") + 1])
m = re.match(r"^postgresql(\+\w+)?://[^@/]*@?(?P<host>[^:/]+)", url)
if not m or m["host"] not in {"127.0.0.1", "localhost", "::1"}:
    sys.exit("refusing non-local host")
url = re.sub(r"^postgresql\+\w+", "postgresql", url)
Q = {
    "column": "select table_name, column_name || ' ' || data_type || ' ' || is_nullable || ' ' || coalesce(column_default, '') from information_schema.columns where table_schema = 'public'",
    "constraint": "select conrelid::regclass::text, conname || ' ' || pg_get_constraintdef(oid) from pg_constraint where connamespace = 'public'::regnamespace",
    "index": "select tablename, regexp_replace(indexdef, 'CREATE (UNIQUE )?INDEX \\S+ ON', 'INDEX \\1 ON') || ' /' || indexname from pg_indexes where schemaname = 'public'",
    "trigger": "select tgrelid::regclass::text, tgname || ' ' || pg_get_triggerdef(oid) from pg_trigger where not tgisinternal",
}
with psycopg.connect(url) as conn:
    out = []
    for kind, q in Q.items():
        for table, text in conn.execute(q):
            if skip and skip.search(table):
                continue
            out.append(f"{table}\t{kind}\t{text}")
print("\n".join(sorted(out)))
