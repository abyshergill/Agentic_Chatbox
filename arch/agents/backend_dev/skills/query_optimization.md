# Query Optimization

Title: Query Optimization

Description: Finds and fixes slow database access in backend code: N+1 queries, missing indexes, loading too much data, unpaginated lists, and slow SQL, using the ORM's tools and query plans (EXPLAIN). Measures before and after, and routes index or schema changes through Database Migration. Use it when an endpoint, page, report, or query is slow, or runs too many queries.
Allowed Paths: ./workspace/projects, ./workspace/database

## Instructions

You make database access faster by measuring first, fixing the biggest cause, and measuring again.

### Rules

1. Stay inside `./workspace/projects` and `./workspace/database`. If the user points to files outside this path, say so and ask for a path inside it.
2. Read before you change. Open every file you edit, and check `git_status` first so you never overwrite the user's uncommitted work.
3. Make focused, minimal changes. Edit only the lines that need to change. Do not reformat or rewrite whole files unless the user asks.
4. Follow the project's existing conventions: framework, project layout, naming, error format, and lint or formatter settings.
5. Never run anything that writes to a real database (migrations, data scripts, raw SQL writes) without the user's explicit authorization for that database. Read-only checks and test databases are fine.
6. Never hard-code or print secrets. Read passwords, keys, and connection strings from settings or environment variables, and mask them in replies.
7. Do not add a dependency without asking. Never commit or push unless the user asks.
8. Measure before and after. Report only numbers from a measurement (query counts, timings, or query plans from a tool result). Never estimate a speed-up.
9. Use only read-only checks on real databases. `EXPLAIN ANALYZE` runs the query, so use it only on `SELECT` statements, and never on a production database without the user's agreement.

### Step 1: Define the problem

* Which endpoint, page, report, or query is slow, how slow, and with how much data.
* Measure against data of realistic size. A query that is fast on 100 rows can be slow on a million.

### Step 2: Measure

* Count the queries: Django Debug Toolbar (if installed), `django.db.connection.queries` with `DEBUG = True`, `assertNumQueries` in a test, or SQLAlchemy's `echo=True`.
* Time the slow part, and get the query plan: `QuerySet.explain()` in Django, or `EXPLAIN` (or `EXPLAIN QUERY PLAN` in SQLite) on the SQL.
* Record the query count, the slowest queries, and the total time.

### Step 3: Find the causes

Common causes, roughly in order of impact:

* N+1 queries: one query per row when looping over related objects. Fix with `select_related` (foreign keys) or `prefetch_related` (many-to-many and reverse relations) in Django, or `joinedload` or `selectinload` in SQLAlchemy.
* Missing indexes on columns used in `WHERE`, `ORDER BY`, or `JOIN`. The query plan shows a full table scan.
* Loading too much: all columns when a few are needed (use `only`, `values`, or `values_list`), or whole tables without pagination.
* Work done in Python that the database can do: filtering, counting, or summing in loops instead of `filter`, `aggregate`, or `annotate`.
* Queries inside loops, and repeated identical queries that could be cached for the request.
* Slow patterns: `LIKE '%text%'` on large tables, functions applied to indexed columns, `count()` on huge tables just to check existence (use `exists()`), and `OFFSET` pagination deep into large tables.

### Step 4: Fix

* Fix the biggest cause first, with a focused change. Keep the results exactly the same.
* Index and other schema changes go through the Database Migration skill (create the migration, validate it, and apply it only when authorized).
* Add a test that locks in the improvement where practical (for example `assertNumQueries`).

### Step 5: Measure again and reply

1. Before and after numbers from the measurements: query count, time, and plan changes (for example "full scan to index scan"), with the data size used.
2. The causes found, most important first, with `file:line`.
3. Files changed, showing only the changed parts, and any migration created (applied or not).
4. Remaining opportunities you did not change, with their likely impact.
5. Git status: the changes are uncommitted unless the user asked you to commit.
