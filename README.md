# Retie — a simple, secure, self-hosted URL shortener

Retie is a standalone URL shortener you install and operate yourself.
Every installation is fully independent: your server, your domain, your
database, your user accounts, your analytics. It talks to no external
service and never phones home.

**Core principles:** simple readable Django, security by design, one-command
deployment, and no unnecessary infrastructure.

## Features

- Short links on **your own domain** (`SITE_URL`-driven, no hardcoded domains).
- Authenticated users only — registration, login, logout, password change,
  password reset, profile.
- Each user sees and manages **only their own** links and analytics.
- Custom aliases (validated, reserved application routes protected) or
  automatically generated cryptographically-secure short codes (`secrets`).
- Optional titles and expiration dates. Deactivated and expired links stop
  redirecting with a clean 404.
- Click analytics: total clicks, 30-day churn chart, referrers, coarse device
  categories, recent activity. **No IP addresses are stored.**
- REST API at `/api/v1/` documented as OpenAPI 3 by drf-spectacular and served
  as a live docs portal (plus MCP for agents) by apiwarden at `/api/docs/`.
- Modern responsive UI: Django templates + HTMX partial updates, Tailwind
  CSS, Lucide icons, Chart.js, light/dark themes.
- Docker Compose deployment with PostgreSQL and Gunicorn.

## Technology stack

| Layer      | Choice                                              |
|------------|-----------------------------------------------------|
| Backend    | Python 3.12+, Django 5.2 LTS, Django REST Framework |
| Database   | PostgreSQL (psycopg 3)                              |
| Frontend   | Django templates, HTMX, Tailwind CSS, Lucide, Chart.js |
| Docs       | drf-spectacular (schema) + apiwarden (docs portal, MCP) |
| Agents     | MCP server at `/mcp/` (Streamable HTTP, API-key auth) + docs MCP at `/api/docs/mcp` |
| Serving    | Gunicorn + WhiteNoise (static), Docker Compose      |
| Tests      | pytest + pytest-django                              |

## Project layout

```
config/       settings, URLs, WSGI, the /api/ URL module
accounts/     registration, login/logout, profile, password flows
links/        Link model, forms, dashboard views, public redirect, REST API
analytics/    ClickEvent model, click recording, aggregation queries
templates/    site templates (pages/, links/, registration/, errors)
static/       css (source + built), js, vendored vendor/ libraries
tests/        pytest suite
```
The compiled `static/css/app.css` is committed, so local development and
Docker deploys don't require Node unless you change the templates/CSS source.

## Quick start (local development)

1. **Prerequisites:** Python 3.12+, PostgreSQL, Docker (for the DB container).

2. Start PostgreSQL any way you like; the quickest is:

   ```bash
   docker run -d --name retie-pg -e POSTGRES_PASSWORD=postgres \
     -e POSTGRES_DB=shortener -p 5432:5432 postgres:14.4
   ```

3. Create a virtualenv and install dependencies:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

4. Configure the environment:

   ```bash
   cp .env.example .env     # defaults work for local dev except the password
   # edit DATABASE_URL=postgres://postgres:postgres@localhost:5432/shortener
   ```

5. Migrate and run:

   ```bash
   .venv/bin/python manage.py migrate
   .venv/bin/python manage.py runserver
   ```

   Visit http://localhost:8000. Register from the landing page, then use the
   dashboard. See also `DEBUG=true` in `.env` for local error pages.

6. Static files are provided pre-built (`static/css/app.css` is committed), so
   no Node tooling is needed. To rebuild CSS after template changes:

   ```bash
   # with NodeJS installed:
   npm install && npm run build-css
   # or with the standalone Tailwind CLI (download for your OS from
   # github.com/tailwindlabs/tailwindcss/releases, e.g. tailwindcss-macos-arm64):
   curl -sL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.8/tailwindcss-macos-arm64
   chmod +x tailwindcss
   ./tailwindcss -i static/css/src/input.css -o static/css/app.css --minify
   ```

7. Create an administrator:

   ```bash
   .venv/bin/python manage.py createsuperuser
   ```

## Deploying on your own server

Assumptions: a Linux VPS or dedicated box with Docker + Docker Compose, a
domain you control, and (for HTTPS) a reverse proxy such as nginx/Caddy.

### 1. DNS

Point an `A`/`AAAA` (or `CNAME`) record of your subdomain — e.g.
`links.example.com` — at your server. Only this hostname needs to resolve;
there is no CDN or global platform involved.

### 2. Environment

```bash
scp .env.example server:/srv/retie/.env
```

Edit `.env` on the server:
- `SECRET_KEY` — generate: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`
- `ALLOWED_HOSTS=links.example.com`
- `CSRF_TRUSTED_ORIGINS=https://links.example.com`
- `SITE_URL=https://links.example.com`
- `POSTGRES_PASSWORD=<strong password>`, `POSTGRES_DB=shortener`
- Once HTTPS works: `SECURE_SSL_REDIRECT=true`, `SESSION_COOKIE_SECURE=true`,
  `CSRF_COOKIE_SECURE=true`, `SECURE_HSTS_SECONDS=31536000`

### 3. Start

```bash
docker compose up -d --build
```

The app container runs migrations, collects static files, then serves with
Gunicorn on port 8000 (or `$APP_PORT`). The database volume persists at
`pgdata`. PostgreSQL itself is **not** exposed to the host or internet.

### 4. Reverse proxy & HTTPS

Docker does not provide TLS. Put a proxy in front, e.g. nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name links.example.com;

    # Certificates from Let's Encrypt (certbot) or any CA.
    ssl_certificate     /etc/letsencrypt/live/links.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/links.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        # Only include this if the app container is behind YOUR trusted proxy:
        # (SECURE_PROXY_SSL_HEADER must be set in .env for Django to honour it)
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Let's Encrypt issuance is standard via `certbot --nginx`. With Caddy:

```
links.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

When running behind a proxy you control, set
`SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https` in `.env` — this makes
Django trust the `X-Forwarded-Proto` header and generate correct absolute
URLs for redirects and email links. Only do this on a proxy you operate;
blind trust of forwarded headers would allow protocol spoofing.

### 5. Custom domain

Everything is driven by `.env`. To run another, independent instance on
`go.example.org`, deploy the same code there with different
`SITE_URL`/`ALLOWED_HOSTS`/database/`SECRET_KEY`. The two installations share
nothing: no code-level coupling, no central accounts, no shared short codes.

### 6. Migrations

```bash
docker compose exec app python manage.py migrate
```

(This runs automatically on container start; run it manually after changing
models in a customized install.)

### 7. Backups & restore

```bash
# Backup: dumps the whole DB to a timestamped file on the host
docker compose exec postgres pg_dump -U postgres shortener > backup_$(date +%F).sql

# Restore into an empty database
docker compose exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS shortener;"
docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE shortener;"
docker compose exec -T postgres psql -U postgres shortener < backup_2026-10-18.sql

# Off-server copy (recommended)
scp server:/srv/retie/backup_2026-10-18.sql ./
```

Also back up `.env` (contains `SECRET_KEY` — losing it invalidates sessions
and password reset tokens) or keep it in your secrets manager.

Also back up `.env` (contains `SECRET_KEY` — losing it invalidates sessions
and password reset tokens) or keep it in your secrets manager.

### Trash & retention

Deleting a dashboard link moves it to **Trash** (status filter), where it
can be restored with one click; trashed links stop existing publicly
immediately. Purge them for good on a schedule once the retention window
passes:

```
0 3 * * * cd /srv/retie && .venv/bin/python manage.py purge_trash --days 30
```

(or `docker compose run --rm app python manage.py purge_trash --days 30`).

### 8. Updating dependencies

Pinned in `requirements.txt`. To update: bump a pin, run
`.venv/bin/pip install -r requirements-dev.txt`, run the test suite, then
commit the new pins. Frontend libraries are vendored under `static/vendor/`
(record versions in the commit message; sources: htmx from
unpkg.com/htmx.org, Chart.js from cdn.jsdelivr.net, Lucide from
unpkg.com/lucide).

## Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session/CSRF signing secret; required in production |
| `DEBUG` | Django debug flag; always `false` in production |
| `ALLOWED_HOSTS` | Comma-separated hosts Django may serve |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated `scheme://host` origins trusted for CSRF (HTTPS setups) |
| `DATABASE_URL` | PostgreSQL DSN (`postgres://user:pass@host:5432/db`) |
| `SITE_URL` | Canonical public URL used to build short links — **must include the scheme** (`https://…`); startup fails otherwise |
| `BLOCKED_DESTINATIONS` | Comma-separated dest hosts (with their subdomains) that may never be shortenable |

### CSV import / export

| Method | Path | Description |
|---|---|---|
| GET | `/dashboard/links/export.csv` | All your links as CSV (backup / migration away easy) |
| GET/POST | `/dashboard/links/import/` | Bulk-create from a CSV upload (bit.ly/short.io headers understood) |
| `TIME_ZONE` | e.g. `Europe/Berlin` |
| `SECURE_SSL_REDIRECT` | Only after HTTPS works |
| `SECURE_HSTS_SECONDS` | e.g. 31536000 once HTTPS confirmed |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | `true` for HTTPS deployments |
| `SECURE_PROXY_SSL_HEADER` | `HTTP_X_FORWARDED_PROTO,https` — **only with a trusted proxy** |
| `EMAIL_BACKEND` | `console` (default, dev) or `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | SMTP settings for password reset mail |
| `DEFAULT_FROM_EMAIL` | From address of reset emails |
| `REGISTRATION_ENABLED` | `false` closes signups (single-operator installs) |
| `LANDING_ENABLED` | `false` turns `/` into an app-only doorway (dashboard/login, no marketing page) |
| `APIWARDEN_TOKEN` | Optional shared token gating `/api/docs/` (unset = public docs) |
| `POSTGRES_DB`, `POSTGRES_PASSWORD` | Passed to the Postgres container; the app derives `DATABASE_URL` from them |
| `GUNICORN_WORKERS`, `APP_PORT` | Serving knobs |

## API

Base: `/api/v1/` — interactive documentation at `/api/docs/`, served by
[apiwarden](https://pypi.org/project/apiwarden/) from the committed OpenAPI
spec (`api-docs/retie/openapi.yaml`). The raw schema is also available at
`/api/schema/`. Auth: session cookie (browser) or an API key.

### API keys

Every user can self-serve an API key with username+password — no admin step
required:

```bash
# Obtain a key (throttled to 10/hour; same key is returned while it lives)
KEY=$(curl -s -X POST https://links.example.com/api/v1/token/ \
     -H 'Content-Type: application/json' \
     -d '{"username":"alice","password":"…"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Use it — both schemes are accepted
curl -H "Authorization: Bearer $KEY" https://links.example.com/api/v1/links/
curl -H "Authorization: Token $KEY" https://links.example.com/api/v1/links/

# Revoke it
curl -X DELETE -H "Authorization: Bearer $KEY" https://links.example.com/api/v1/token/
```

The profile page offers the same life cycle (create → shown once → revoke).
A key authenticates every `/api/v1/` endpoint; changing your password does
not revoke it, so revoke explicitly via `DELETE /api/v1/token/`.

The docs portal Try-it panel sends `Authorization: Bearer <key>` — paste the
key there and calls to `SITE_URL` work from the browser.

Because the docs are served from a spec file, they can also be read by agents:
`/api/docs/mcp` is an MCP server (`list_apis`, `search_operations`,
`get_operation`, …), and `/api/docs/index.json`, `/api/docs/llms.txt`,
`/api/docs/types/retie.ts` expose the same content as data. That is why the
spec is regenerated on container start — edit the API and the docs follow.
After changing API code locally, refresh the spec with:

```bash
.venv/bin/python manage.py spectacular --file api-docs/retie/openapi.yaml
```

Optional: set `APIWARDEN_TOKEN` in `.env` to require a shared token on every
request to `/api/docs/`; unset, the docs portal is public.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/links/` | Paginated list of your links (?q=&status=&page=) |
| POST | `/api/v1/links/` | Create a link |
| GET/PATCH/PUT/DELETE | `/api/v1/links/{code}/` | Retrieve / update / delete |
| POST | `/api/v1/links/{code}/activate/` and `/deactivate/` | Toggle delivery |
| GET | `/api/v1/links/{code}/analytics/` | Click stats for one link |
| GET | `/api/v1/stats/` | Dashboard totals |
| GET | `/api/v1/me/` | Current profile |
| POST / DELETE | `/api/v1/token/` | Obtain / revoke an API key |

Example:

```bash
KEY=$(curl -s -X POST https://links.example.com/api/v1/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"…"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -H "Authorization: Bearer $KEY" https://links.example.com/api/v1/links/ \
     -d 'destination=https://example.com/article' -d 'code=title1'
```

(Operators can also mint a key directly with
`.venv/bin/python manage.py drf_create_token alice`.)

## MCP server (agent access)

`/mcp/` implements the Model Context Protocol (Streamable HTTP transport) so
AI agents — Claude Desktop, Claude Code, Cursor, OpenCode — can create and
manage short links directly, with the same rules as the REST API:

- **Auth is an API key**: send `Authorization: Bearer <key>` (or `Token <key>`)
  with every request; unauthenticated requests get 401.
- **Ownership isolation**: a key only ever sees and touches its own user's
  links; the tools are user-scoped end to end.
- **Same validation**: destinations must be http(s), aliases 2–31 chars of
  a-z/0-9/hyphen, reserved aliases rejected.
- No new dependencies — it is plain JSON-RPC 2.0 over HTTP POST.

Tools exposed: `create_link`, `list_links`, `get_link`, `update_link`,
`delete_link`, `get_link_analytics`, `get_dashboard_stats`.

Client configuration (Claude Desktop / any MCP client that supports HTTP
servers):

```json
{
  "mcpServers": {
    "retie": {
      "type": "http",
      "url": "https://links.example.com/mcp/",
      "headers": { "Authorization": "Bearer YOUR_API_KEY" }
    }
  }
}
```

Or with Claude Code:

```bash
claude mcp add --transport http retie https://links.example.com/mcp/ \
  --header "Authorization: Bearer YOUR_API_KEY"
```

Handshake smoke test without a client:

```bash
KEY=...
curl -X POST https://links.example.com/mcp/ \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2025-03-26"}}'
```

(A second, docs-only MCP surface lives at `/api/docs/mcp` — it exposes the
API documentation for agents to read, never your data.)

## Tests

```bash
DATABASE_URL=postgres://postgres:postgres@localhost:5432/shortener \
  .venv/bin/python -m pytest tests/
```

pytest-django creates and destroys a dedicated `test_shortener` database.

## Security notes & limitations (be honest about them)

- **URL validation prevents malformed schemes, not malicious content.** A
  short link can still point anywhere its owner wants; opening destinations
  is at the visitor's own risk. There is no server-side URL fetching.
- **Access control:** every link-management and analytics view filters by the
  logged-in user; the site's HTML/API returns 404s for other users' codes.
  Anonymous link creation is impossible.
- **Cookies:** `HttpOnly`, `SameSite=Lax`; `Secure` in HTTPS deployments via env. CSRF is enforced on all unsafe requests (incl. by HTMX via `X-CSRFToken`).
- **Headers:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  tight `Referrer-Policy`, HSTS once HTTPS is live.
- **Rate limiting is per-worker.** Login, registration, and link creation are
  throttled using Django's LocMem cache — the counter lives inside each
  Gunicorn worker, so effective limits scale with the worker count, and
  nothing survives restarts. Sufficient for a single-server install; for
  tighter guarantees, front with a real rate limiter at the proxy.
- **Click counts are not unique visitors.** They include bots, chat-app link
  previews, and other automated traffic; no IP storage or fingerprinting is
  attempted to filter them out.
- **No telemetry, no external calls** of any kind required for core operation.
- The redirect is a plain 302 (never cached browserside) so stats stay usable.
- Deleting a link cascades to its click records (documented policy: analytics
  never outlive the link they describe).
