# SUMX administration

The administrator workspace uses a password session and its own navigation. Google sign-in continues to serve the ordinary meeting workspace. A Google session cannot become an admin session just because the account's database role changes.

## Upgrade and administrator accounts

Back up the SQLite database with SQLite's backup API before upgrading; preserve `data/media`, the root `.env`, models and worker environments. Stop the API and workers after in-flight jobs finish, migrate, and restart them together so they use the same schema.

```powershell
cd C:\Projects\Sumx
.\.venv\Scripts\python -m alembic upgrade head
.\start_sumx.ps1
```

Administrator credentials are provisioned directly in the private deployment database. The interactive CLI password setup has been removed. A password administrator has its own unique Admin ID and an Argon2id password hash; Google users remain separate. Direct password resets terminate that administrator's sessions and are recorded in the audit log. No account-specific passwords, hashes or usernames belong in source files or environment examples.

This repository does not seed a default administrator or contain the credentials of any existing deployment. A new deployment needs its own administrator provisioned through trusted local database administration before password login can work. Existing deployment accounts remain in the local database across code changes.

Open `http://localhost:5173/admin`. `/admin/login` remains a compatibility alias. Successful login opens `/admin/overview`; admin logout returns to `/admin`. Regular signed-in users can open the admin login form without being sent to their dashboard, but must authenticate with admin credentials.

## Frontend routes and components

`frontend/src/AdminWorkspace.tsx` supplies the login, layout, overview, individual user and approval-management pages. It uses the existing SUMX CSS tokens, icons and API/CSRF helper. `frontend/src/admin.css` provides the light, responsive administrative layout; no new UI package is required.

| Route | Behavior |
| --- | --- |
| `/admin` | Admin ID/password form, or redirect to overview for a valid password-authenticated admin |
| `/admin/login` | Redirect to `/admin` |
| `/admin/overview` | Aggregate cards and searchable/sortable/paginated users |
| `/admin/users/add` | Approve, revoke, restore and search Google emails |
| `/admin/users/:id` | Stable-ID profile, cost chart, paginated meetings and request inspection |
| `/admin/analytics` | Existing detailed model analytics and ledger export |

Unauthenticated visits to protected admin pages go to `/admin`. The ordinary login, recording, upload, history, settings and voice library remain available at their existing routes. `/analytics` preserves its previous role check and forwards admins to the separate analytics page.

## Backend API

All paths below begin with `/api/admin`. Every protected endpoint uses the same backend password-session authorization dependency, independently of React routing.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/login` | `{admin_id, password}`; permitted Origin required, rate limited |
| POST | `/logout` | Destroy the current admin session, clear cookie, audit logout |
| GET | `/session` | Admin identity, CSRF token and expiry |
| GET | `/overview` | All-account counts and SQL-aggregated service costs |
| GET | `/users` | Registered users plus approvals awaiting first registration |
| GET | `/users/export` | Stream filtered all-user totals CSV |
| GET | `/users/{id}` | Profile and cumulative service totals |
| GET | `/users/{id}/meetings` | Paginated meetings and retained/unlinked usage groups |
| GET | `/users/{id}/usage` | Paginated provider, model, request, units, status and cost ledger |
| GET | `/users/{id}/export` | Stream meeting breakdowns, standalone usage and a cumulative summary |
| POST | `/approved-users` | Normalize/validate email, approve or restore, audit actor/time |
| GET | `/approved-users` | Searchable/paginated approval records |
| DELETE | `/approved-users/{id}` | Revoke approval and terminate regular-user sessions; retain history |
| POST | `/usage/reconcile` | Existing OpenRouter generation billing reconciliation |
| GET | `/credits` | Existing OpenRouter credit lookup |

`days=0` (default) means all time; supported period filters are `0`, `7`, `30`, `90`. UTC calendar boundaries filter meeting creation and usage execution independently. Older meetings with usage during the chosen period remain visible. Account registration counts remain all-time. Overview cards cover all accounts; the user search filters the directory and its export, not global cards.

The directory/export accept `q`, `sort=name|email|meetings|llm|stt|tts|total`, and `direction=asc|desc`. Lists accept `offset` and `limit` (1–100, default 25; usage defaults to 50). Usage inspection optionally filters `meeting_id`; `unlinked` selects previews and other records without a meeting. All selected-user lookups use the stable database ID. Money is serialized as decimal strings.

Legacy `POST /api/auth/admin/login` delegates to the same password login, treating its `email` field as the ID. Legacy approval mutations also use the audited approval helper. The old approval-list GET moved to deprecated `/api/admin/allowed-users`; `/api/admin/users` now returns the paginated directory. Deprecated `/api/admin/analytics` now returns the SQL-aggregated overview instead of loading every meeting into application memory.

## Database migration 0007

The additive migration preserves user IDs, Google subjects, meetings, voice profiles, consent, recordings, generated audio, allowlist status and existing cost records.

- `users`: unique indexed `admin_id`, nullable `picture`, nullable `last_login`.
- `sessions`: `auth_method` distinguishes Google and password-authenticated admin sessions. Existing admin sessions originated from the previous password login and retain that method.
- `allowed_emails`: unique stable `id`, nullable `approved_at`, `approved_by` (user foreign key), `revoked_at`. An existing registered user's ID is reused; awaiting-registration approvals get their own stable ID. On first Google login, that ID becomes the user ID. Historical approval dates/actors remain unknown rather than fabricated.
- `admin_audit`: actor foreign key, action, target identifier and timestamp, indexed by actor/date. Records administrator provisioning/reset, login success/failure, logout, approvals, revocations, user-detail access and CSV exports. It never records passwords, tokens or meeting text.
- `api_usage`: explicit `provider` and `currency`. Existing remote records remain OpenRouter USD. A database check currently permits USD only, preventing silent cross-currency summation. Existing local generation metrics are backfilled with their persisted external API fee and a unique per-generation billing key.

Migration `0008` adds a nullable revocation timestamp to sessions. Revocation invalidates existing regular-user sessions while retaining their hashes and expiry long enough to distinguish revoked access from an ordinary expired login. Restoring approval requires a fresh Google sign-in; previously revoked sessions remain invalid. Revoked users may still securely terminate their own session through the CSRF-protected logout endpoint.

Approval revocation retains the approval record with inactive status. Duplicate active approvals return 409, including concurrent insertion conflicts. Restoring an inactive approval records the new actor/time. An email can be approved before the person has ever logged in; it appears as **Approved — Not Yet Registered**, without an invented Google name or photo.

## Identity and security

Google identity verification still checks signature, audience, issuer, expiry, verified email, browser nonce, Google subject binding and the active server-side allowlist. A denied unapproved/revoked sign-in returns safely to the public homepage with an access message. Verified name/picture/last login populate only after successful authentication.

Admin cookies are HttpOnly and SameSite=Lax; `COOKIE_SECURE=true` is required when `APP_URL` is HTTPS. For local HTTP only, the existing development setting can be false. `SESSION_HOURS` controls expiry (default 12 hours). Password login rotates the previous browser session and uses persistent per-IP (10/15 minutes) and per-ID (8/15 minutes) limits, including dummy Argon2 verification for unknown IDs. Protected mutations require both the exact permitted Origin and the session's CSRF token. Admin session state is rechecked on window focus and periodically. Every ordinary-user request still checks current approval, so revoked users cannot use a previously issued cookie.

## Cost provenance and future providers

`Total AI cost = recorded LLM + recorded STT + recorded TTS` in USD. Totals exclude duplicate ledger rows. Exact decimal text/NUMERIC storage and database decimal sums preserve precision; floating-point conversion is used only to draw charts and format display values.

OpenRouter responses supply provider cost, generation/request identifiers and numeric usage such as input/output tokens. STT also records the processed chunk duration. Generation/request billing keys prevent the same reported request from being counted twice; reconciliation updates a pending/estimated record instead of creating a new charge. When a provider supplies no identifier, its separate invocations cannot be reliably matched across ambiguous external retries; SUMX does not claim those retries were free.

Historical remote-speech estimates, provider-reported costs and local zero-API-fee records keep their original provenance. New preset and cloned speech is recorded with provider `modal_qwen3`, the actual Qwen checkpoint, GPU execution seconds, cold-start seconds, retries and peak VRAM. Reference verification is a separate STT charge.

Modal costs are labeled `estimated` with source `modal_gpu_rate_estimate`: billable GPU seconds multiplied by the published rate for the GPU recorded on that request, from `MODAL_GPU_USD_PER_SECOND` in `modal_service/config.py`. They exclude CPU, memory and idle scale-down time, so they normally understate the invoice; the Modal dashboard is the billing record. Tasks with no measured GPU time remain pending rather than zero. Per-user exports include standalone previews and retained costs for deleted meetings so their cumulative summary reconciles with the ledger.

Unavailable costs are never confirmed zero: records with null costs remain pending, historical meetings without ledger records are counted as unrecorded, and summaries with some known costs are labeled partial. With no activity, the UI says “No usage.” Empty CSV cost fields remain empty. CSV exports stream database rows in bounded batches, use UTF-8 with a BOM, escape values with the CSV library and neutralize formula prefixes, including leading whitespace and control characters. Their query filters match the corresponding dashboard. Summary rows are labeled `row_type=summary` and must not be summed again with meeting rows.

## Verification

Run backend tests and lint from the repository root:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
.\.venv\Scripts\python -m ruff check backend
.\.venv\Scripts\python -m alembic check
```

Run frontend checks:

```powershell
cd frontend
npm run build
npm run lint
npm test
```

If access is revoked, the shared fetch and upload handlers clear the cached signed-in state and remove the workspace. An accessible “You are not approved” dialog requires OK before navigating to the public homepage. Regular sessions are also checked every five seconds, on navigation, and when the page regains focus, so idle pages are removed without waiting for another upload. Brief network errors do not sign out an otherwise valid user. Backend authorization remains authoritative on every request.

The login form respects the server's remaining Retry-After interval: it displays a countdown and temporarily disables resubmission when locked out. Rate limiting remains enforced by the backend.

The browser suite runs an isolated SQLite/HTTP test server with fixture identities and mocked AI providers; production has no fixture login routes or seeded passwords. It checks real API authorization, page routing, forms, CSV downloads, account management, dark-OS light-mode rendering and mobile overflow. Live Google consent and external provider billing still require configured credentials and a human-controlled Google sign-in.
