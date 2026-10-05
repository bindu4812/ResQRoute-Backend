# ResQRoute API

**Low-Bandwidth Emergency Evacuation Route Exchange** — backend only.

ResQRoute keeps emergency information (alerts, road status, evacuation routes,
shelters, locations, messages) available and trustworthy when normal internet
or communication infrastructure is unavailable. It is a hackathon MVP built
with **FastAPI + MongoDB (PyMongo)** — no Django, no Flask, no SQL, no external
AI services.

## Features

- JWT authentication (register / login / logout / me) with `RESPONTER`,
  `VOLUNTEER` and `SHELTER` roles
- Secure password hashing (PBKDF2-HMAC-SHA256 + random salt, never plain text)
- Emergency alerts with **duplicate detection** (`messageId`) and filters
  (`type`, `status`, `priority`, `sourceRole`, `verificationStatus`, `roadId`,
  `freshness`)
- Evacuation routes with **versioning, history and conflict detection**
- Shelters with capacity validation, emergency locations, low-bandwidth
  messages with `hopCount` / `maxHops`
- **Store-and-forward sync** (`POST /sync`) — validates batches, ignores
  duplicates / expired packets, honours versions, updates route + shelter
  state, records sync logs and returns statistics
- Centralized **freshness** rules: `Fresh < 30 min`, `Aging 30–120 min`,
  `Outdated > 120 min`
- Trust/conflict detection: two sources reporting different statuses for the
  same route version never silently overwrite each other — the route is marked
  `CONFLICTED` and both reports are retained
- Dashboard summary, demo reset, Swagger docs, CORS for the React dev server

## Tech stack

Python · FastAPI · PyMongo · Pydantic · Uvicorn · python-dotenv · PyJWT

## Project structure

```text
resqroute-backend/
├── app/
│   ├── __init__.py
│   ├── main.py        # app, CORS, tags, /health, /demo/reset
│   ├── database.py    # Mongo connection, indexes, freshness rules, helpers
│   ├── schemas.py     # enums + Pydantic request models
│   ├── auth.py        # hashing, JWT, dependencies, /auth/*
│   ├── alerts.py      # /alerts
│   ├── routes.py      # /routes (version engine + history + conflicts)
│   ├── shelters.py    # /shelters
│   ├── locations.py   # /locations
│   ├── messages.py    # /messages
│   ├── sync.py        # /sync (store-and-forward)
│   └── dashboard.py   # /dashboard/summary
├── data/
│   └── demo_data.json # seed data for POST /demo/reset
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Getting started

### 1. Prerequisites

- Python 3.10+
- MongoDB running locally on `mongodb://localhost:27017`

### 2. Configure

```bash
copy .env.example .env      # Windows: already provided in this repo
```

```env
MONGO_URI=mongodb://localhost:27017
DATABASE_NAME=resqroute_db
JWT_SECRET_KEY=change-this-secret-key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

Change `JWT_SECRET_KEY` before deploying anywhere real.

### 3. Install & run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Health: `GET /health` → `{"status": "ok"}`

### 4. Seed demo data

`POST /demo/reset` wipes the database and reloads `data/demo_data.json`
(relative timestamps such as `minutesAgo` are resolved at load time, so
freshness is always realistic).

Protection: **valid JWT with role `RESPONDER`** — `401` without a token,
`403` for other roles.

### Demo accounts

| email              | password    | role      | deviceId    |
| ------------------ | ----------- | --------- | ----------- |
| rahul@example.com  | password123 | VOLUNTEER | device-001  |
| priya@example.com  | password123 | RESPONDER | device-002  |
| meera@example.com  | password123 | SHELTER   | device-003  |

## Authentication

1. `POST /auth/register` — `{ name, email, password, role, deviceId }` → `201`
   (duplicate email → `409`, invalid input → `422`; plain passwords are never
   stored or returned).
2. `POST /auth/login` — `{ email, password }` → `{ access_token, token_type,
   user }` (wrong credentials → `401`).
3. Send `Authorization: Bearer <JWT>` on protected endpoints. In Swagger click
   **Authorize** and paste the token. The JWT payload only carries `userId`,
   `email`, `role` (+ `iat`/`exp`).
4. `POST /auth/logout` — stateless: the frontend simply drops the token.

Reusable dependencies (see `app/auth.py`):

- `get_current_user` → `current_user.userId / .email / .role / .deviceId`
- `optional_current_user` → identity when a token is sent, `None` otherwise
  (device posts keep working offline; identity fields always come from the
  JWT, never trusted from the request body)
- `require_roles(...)` → `403 Forbidden` for authenticated-but-unauthorized

## API overview

| Method | Path                        | Auth            | Notes |
| ------ | --------------------------- | --------------- | ----- |
| GET    | `/health`                   | –               | `{"status":"ok"}` |
| POST   | `/auth/register`            | –               | 201 / 409 / 422 |
| POST   | `/auth/login`               | –               | 200 / 401 |
| GET    | `/auth/me`                  | Bearer          | current user |
| POST   | `/auth/logout`              | Bearer          | stateless |
| POST   | `/alerts`                   | optional Bearer | 201, duplicate `messageId` → 409 |
| GET    | `/alerts`                   | –               | filters: `type,status,priority,sourceRole,verificationStatus,roadId,freshness,includeExpired` |
| GET    | `/alerts/{messageId}`       | –               | 404 if missing |
| PUT    | `/alerts/{messageId}`       | Bearer          | partial update |
| DELETE | `/alerts/{messageId}`       | Bearer          | 200 / 404 |
| POST   | `/routes`                   | –               | 201, duplicate `routeId` → 409 |
| GET    | `/routes`                   | –               | `status`, `verificationStatus`, `q` |
| GET    | `/routes/{routeId}`         | –               | 404 if missing |
| PUT    | `/routes/{routeId}`         | optional Bearer | version-aware report (see below) |
| GET    | `/routes/{routeId}/history` | –               | history + retained reports + conflicts |
| POST   | `/shelters`                 | optional Bearer | capacity validated → 400 |
| GET    | `/shelters`                 | –               | `status` filter |
| GET/PUT| `/shelters/{shelterId}`     | – / Bearer      | `occupied > capacity` → 400 |
| POST   | `/locations`                | optional Bearer | 6 location types |
| GET    | `/locations`                | –               | `type` filter |
| GET/PUT| `/locations/{locationId}`   | – / optional    | 404 if missing |
| POST   | `/messages`                 | optional Bearer | duplicate `messageId` → 409 |
| GET    | `/messages`                 | –               | `messageType`, `includeExpired` |
| GET    | `/messages/{messageId}`     | –               | 404 if missing |
| POST   | `/sync`                     | –               | store-and-forward batch → statistics |
| GET    | `/sync/pending`             | –               | unresolved batches + conflicted routes |
| GET    | `/dashboard/summary`        | Bearer          | 10 counter fields |
| POST   | `/demo/reset`               | Bearer + `RESPONDER` | 401 / 403 / 200 |


## Route versioning, freshness and conflicts

`PUT /routes/{routeId}` and route reports inside `POST /sync` follow the same
rules:

1. Every incoming report is stored in the `route_updates` collection.
2. **Newer version wins** — it becomes the current state (history entry is
   appended, `lastUpdated`/`lastVerified` refreshed).
3. **Older version is ignored** — response `{ "outcome": "ignored",
   "reason": "stale_version" }`.
4. **Same version + different status + different source → conflict**: the route
   is marked `verificationStatus = CONFLICTED`, the current status is *not*
   overwritten, the conflicting report is retained in `conflicts`, and the
   response returns `conflict: true` with both sides.

Freshness (single source of truth in `app/database.py`):

| Bucket   | Age             |
| -------- | --------------- |
| Fresh    | < 30 minutes    |
| Aging    | 30–120 minutes  |
| Outdated | > 120 minutes   |

Alerts expose a computed `freshness` field and support
`?freshness=Fresh|Aging|Outdated`; the dashboard counts `outdatedAlerts`.

## Store-and-forward sync

`POST /sync` accepts a batch queued by an offline device:

```json
{
  "deviceId": "device-001",
  "alerts":   [{ "messageId": "a-1", "type": "SOS", "lat": 13.1, "lon": 80.2 }],
  "routes":   [{ "routeId": "route-nh44", "status": "BLOCKED", "version": 3,
                 "sourceDevice": "device-001" }],
  "shelters": [{ "shelterId": "shelter-001", "name": "Hall", "latitude": 13.1,
                 "longitude": 80.2, "capacity": 200, "occupied": 120 }],
  "messages": [{ "messageId": "m-1", "message": "Convoy at 14:00" }]
}
```

Response:

```json
{
  "syncId": "sync-xxxx", "status": "pending",
  "received": 12, "accepted": 6, "duplicates": 2, "expired": 1,
  "hopLimited": 1, "stale": 2, "updatedRoutes": 2, "updatedShelters": 1,
  "conflicts": 1
}
```

Rules per item: malformed data → `422` (request validation) · duplicate
`messageId` → counted as duplicate · `expiresAt` in the past → expired ·
`hopCount > maxHops` → hop-limited (repeated processing blocked) · older route
version → stale · newer route state applied (or flagged conflicted) · shelters
only updated when the incoming `lastUpdated` is newer. A batch is logged as
`pending` when it carries conflicts or hop-limited packets;
`GET /sync/pending` and dashboard `pendingSync` only report batches whose
conflicts are still unresolved, so the counter drains as conflicts resolve.

## Dashboard

`GET /dashboard/summary` (Bearer required):

```json
{
  "activeAlerts": 6, "safeRoutes": 1, "blockedRoutes": 1, "cautionRoutes": 1,
  "outdatedAlerts": 2, "conflictedRoutes": 1, "openShelters": 1,
  "nearFullShelters": 1, "fullShelters": 1, "pendingSync": 0
}
```

## HTTP status codes

`200` ok · `201` created · `400` bad request (e.g. capacity rules) ·
`401` unauthorized · `403` forbidden (wrong role) · `404` not found ·
`409` conflict (duplicate email / messageId / routeId) · `422` validation
error · `500` server error. Errors always return JSON `{ "detail": "..." }`.

## MongoDB indexes

`users.email` (unique) · `users.userId` (unique) · `alerts.messageId`
(unique) · `alerts.roadId` · `alerts.createdAt` · `routes.routeId` (unique) ·
`messages.messageId` (unique) · `messages.createdAt` · `shelters.shelterId`
(unique) · `locations.locationId` (unique) — created automatically at startup.

Collections: `users, alerts, routes, route_updates, shelters, locations,
messages, sync_logs` in database `resqroute_db`.

## React frontend integration

CORS allows exactly `http://localhost:5173` (the Vite dev origin) with
credentials, all methods and all headers. Store the `access_token` returned by
`/auth/login`, send `Authorization: Bearer <token>`, and delete it on logout.

