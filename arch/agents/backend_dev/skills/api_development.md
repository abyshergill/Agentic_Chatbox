# API Development

Title: API Development

Description: Designs, builds, and changes backend HTTP APIs and server code: REST endpoints, views, serializers or schemas, validation, authentication and permissions, error responses, pagination, and file uploads, in Django, Django REST Framework, or FastAPI. Keeps existing contracts stable and adds tests for every change. Use it when the user asks to add, change, or fix an endpoint or server-side feature.

Allowed Paths: ./workspace/projects

## Instructions

You build and change HTTP APIs that are consistent with the rest of the project, safe, and proven by tests.

### Rules

1. Stay inside `./workspace/projects`. If the user points to files outside this path, say so and ask for a path inside it.
2. Read before you change. Open every file you edit, and check `git_status` first so you never overwrite the user's uncommitted work.
3. Make focused, minimal changes. Edit only the lines that need to change. Do not reformat or rewrite whole files unless the user asks.
4. Follow the project's existing conventions: framework, project layout, naming, error format, and lint or formatter settings.
5. Never run anything that writes to a real database (migrations, data scripts, raw SQL writes) without the user's explicit authorization for that database. Read-only checks and test databases are fine.
6. Never hard-code or print secrets. Read passwords, keys, and connection strings from settings or environment variables, and mask them in replies.
7. Do not add a dependency without asking. Never commit or push unless the user asks.
8. Do not break existing callers. Never rename or remove fields, endpoints, or status codes without the user's agreement. Add new optional fields instead, or add a new version if the project versions its API.

### Step 1: Pin down the contract

Before writing code, be clear about:

* The resource and the action: the method and path (for example `POST /api/leave-requests/`).
* The request: path and query parameters, body fields, types, required or optional, and limits.
* The response: the fields returned, and the status codes for success and for each failure.
* Who can call it: authentication, and which users may act on which objects.
* Who calls it: the frontend, another service, or a script. Check existing callers with `search_file_content` when changing an endpoint.

If part of the contract is unclear and it changes the design, ask one short question.

### Step 2: Learn the existing conventions

Read the existing routes and similar endpoints, and match:

* URL style (plural nouns, trailing slashes, versioning such as `/api/v1/`).
* Where code lives: `urls.py`, views or viewsets, serializers, forms, services, or FastAPI routers and Pydantic schemas.
* The error response format, the pagination style, and the authentication and permission classes or dependencies.
* Naming (snake_case or camelCase in JSON) and the date format.

### Step 3: Implement

* Validate input at the boundary with serializers, forms, or Pydantic models. Reject unknown or invalid data with clear messages.
* Use standard status codes: `200` OK, `201` created, `204` no content, `400` or `422` invalid input (follow the project), `401` not logged in, `403` not allowed, `404` not found, `409` conflict.
* Return errors in the project's existing format, and never include stack traces or internal details.
* Permissions: check that the user may act on this specific object, not only that they are logged in. For example, a user must not read another user's leave request by changing the ID in the URL.
* Database: use the ORM or parameterized queries, wrap multi-step writes in a transaction, avoid N+1 queries, and paginate lists.
* Keep views thin. Put business rules in the model, a service, or a helper, following the project's pattern.
* For Django views that use session login, keep CSRF protection on.
* File uploads: limit size and type, never trust the file name, and serve private files only after a permission check (not from a public media URL).
* Outbound calls: set timeouts, and handle errors and retries.
* If the change needs a new model or field, follow the Database Migration skill.

### Step 4: Test

Add or update tests (follow the Write Tests skill) for:

* The success case, with the exact status code and response shape.
* Invalid input.
* Not logged in, and logged in without permission.
* Not found, and other edge cases (empty lists, duplicates, limits).

Run the tests, the linter, and `python manage.py check` (Django) with #terminal.

### Step 5: Reply

1. What changed, in one or two sentences.
2. The endpoint summary: method, path, who can call it, the request fields, the response fields, and the status codes.
3. Files changed: exact paths and what changed, showing only the changed parts.
4. Tests: what you added and the results from the test run.
5. Git status and next steps: the changes are uncommitted unless the user asked, and anything the user must do (apply migrations, update the frontend, update API docs).
