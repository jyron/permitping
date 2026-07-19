# Permit Ping

Track building permits across city portals. Free lookup by city or ZIP; paid daily monitoring with email alerts on status changes.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in ADMIN_TOKEN at minimum
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000. With `SMTP_HOST` empty, emails print to the console (the account-management link arrives in the "monitor started" email). Feed datasets sync automatically shortly after boot.

## Architecture

Two source classes, declared per city in `app/registry.py`:

- **feed** — the city publishes its permit dataset. Synced on the scheduler into the canonical `permits` table (ArcGIS: full snapshot; Socrata: capped newest-first window, since those cities publish millions of historical rows); lookups read the local store (one SQL query, zero outbound HTTP), with a live query as fallback for permits outside the window.
- **portal** — the city only offers interactive lookup. Fetched live per lookup, written through to the store, which doubles as a short TTL cache.

The visitor enters a city name or ZIP code plus a permit number; `app/services/locations.py` resolves the jurisdiction, `app/services/lookup.py` picks the store-vs-live path.

Layout:

- `app/registry.py` — municipality registry: source class, ZIPs, adapter config. Onboarding a city = one entry here.
- `app/sync/` — dataset pulls for feed cities (idempotent; snapshot swap or windowed upsert per source).
- `app/services/` — business logic: lookup, location resolution, monitors, plan limits, email, the daily scheduler cycle (sync feeds → check monitors).
- `app/services/adapters/` — fetch mechanics per source type: `arcgis.py` (JSON feeds), `aca.py` (Accela portals), `accela_api.py` (Accela's official JSON API, activates when `ACCELA_APP_ID` is set, portal fallback otherwise).
- `app/db/` — SQLAlchemy models plus `repo.py`, the only module that touches queries. `DATABASE_URL` switches SQLite ↔ Postgres.
- `app/routers/` — HTTP layer only.
- `static/` — plain HTML/CSS/JS, no templating engine.

## Data sources

| City | Class | Freshness |
|---|---|---|
| Mesa, AZ | portal (Accela) | Real-time |
| Chandler, AZ | portal (Accela) | Real-time |
| Goodyear, AZ | portal (Accela) | Real-time |
| Tempe, AZ | feed (ArcGIS) | ~Daily, per city's publish cadence |
| Phoenix, AZ | feed (ArcGIS) | ~Daily; last ~2 years of permits |
| New York, NY | feed (Socrata) | ~Daily; newest window synced, older permits live-fallback |
| Chicago, IL | feed (Socrata) | ~Daily; newest window synced, older permits live-fallback |
| Los Angeles, CA | feed (Socrata) | ~Daily; newest window synced, older permits live-fallback |

No API keys required. Optional: an Accela Construct API key (free registration at developer.accela.com) upgrades the Accela portal cities from HTML parsing to the vendor's JSON API — set `ACCELA_APP_ID`/`ACCELA_APP_SECRET`. A free `SOCRATA_APP_TOKEN` keeps the Socrata portals from throttling datacenter IPs.

## Deploy

Push to the git repo; Railway picks up changes automatically (Procfile included). Set env vars in Railway: `DATABASE_URL` (Postgres), `BASE_URL`, SMTP settings, `ADMIN_TOKEN`. Optional: `POSTHOG_API_KEY` (+ `POSTHOG_HOST` if not US cloud) enables PostHog analytics, client and server side; leave empty to disable. Never commit `.env`.

## Admin

```bash
curl -X POST localhost:8000/api/admin/run-sync   -H "X-Admin-Token: $ADMIN_TOKEN"
curl -X POST localhost:8000/api/admin/run-checks -H "X-Admin-Token: $ADMIN_TOKEN"
```
