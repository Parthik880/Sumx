# Implementation and verification

Latest dashboard/cost upgrade: see [Dashboard and AI cost analytics](DASHBOARD_ANALYTICS.md). Migration `0003` and the corresponding API/UI changes pass 35 backend tests and 8 browser workflows; the earlier sections below describe preceding implementation stages.

Initial audit: the project directory was empty, including hidden files. No existing source, databases or stack were overwritten. This is a new local application at the requested path.

Files are organized as:

- `backend/config.py`, `models.py`, `schema.py`: settings, persisted entities and validated minutes/action items.
- `backend/main.py`, `security.py`, `limits.py`: API routes, Google/admin sessions, role/ownership checks, CSRF, database rate counters and streaming upload caps.
- `backend/provider.py`, `media.py`, `jobs.py`: OpenRouter-only inference, bounded FFmpeg processing and durable leased jobs/checkpoints.
- `backend/migrations`: Alembic migration with a frozen initial schema snapshot; production does not call `create_all`.
- `backend/tests`: isolated API/provider/media/worker tests and browser-only test server.
- `frontend/src`: shared API/types, routed React application and light-only CSS tokens/components.
- `frontend/tests`: Chrome navigation, upload/edit/narration, microphone permissions and MediaRecorder workflows.
- `.env.example`, setup/run PowerShell scripts and README: local configuration and operation.

The explicit follow-up design request supersedes the original optional system/dark mode requirement. Both dark CSS branches and the persisted theme preference were removed. White/light surfaces apply to all routes, forms, native media controls, notifications and dialogs. Blue buttons use a slightly darker action token than the decorative #007AFF accent to preserve contrast for small white labels. Secondary gray is darker than #86868B for readable small text on the sidebar.

Validation includes real MP4 extraction and browser WebM decoding, mocked OpenRouter contract/JSON/audio tests, authorization and allowlist rejection, CSRF/origin rejection, upload caps, optimistic edit conflicts, chunk retry and worker lease recovery, current-summary audio access, range streaming, deletion and duplicate active jobs. Browser tests use the real API, database and worker with an explicitly mocked HTTP transport. They are not fake production meeting data.

Not validated live: authenticated OpenRouter inference, real Google OAuth, external HTTPS deployment, PostgreSQL, physical microphone hardware, all browser/codec combinations and multi-host operation. The README provides a real-key manual acceptance procedure. There is no payment flow or automatic top-up. Accounting preserves unknown costs rather than fabricating exact totals.

Dependency checks: the installed Python environment passed `pip-audit` after updating vulnerable dependencies. The frontend production dependency audit passes; the ESLint development dependency tree currently reports moderate minimatch denial-of-service advisories without a compatible upstream fix in its selected range. Do not pass untrusted glob patterns into development tooling. This does not affect shipped browser JavaScript.

Final checks on 31 October 2026:

| Check | Result |
| --- | --- |
| `python -m pytest backend/tests -q` | 18 passed |
| `ruff check backend` | Passed |
| `python -m alembic upgrade head` / `python -m alembic check` | Applied; no schema drift |
| `npm run typecheck` / `npm run lint` / `npm run build` | Passed |
| `npm test` | 5 Chrome workflows passed, including light surfaces under OS dark mode and an old saved dark preference |
| `npm audit --omit=dev` | Zero known production vulnerabilities |
| `pip-audit -r backend/requirements-lock.txt --no-deps --disable-pip` | No known vulnerabilities |
| `python -m pip check` | No broken requirements |
| PowerShell parse of setup/run scripts | Passed |
| Local frontend HTTP and API health | HTTP 200 / `status: ok` |

The Python libraries emit HTTPX deprecation notices, but the current transport and all tests pass. The local `.env` contains a freshly generated session secret and empty external credential placeholders. No administrator or sample content was created in the application database. Temporary browser-test identities and AI responses are isolated from that database.

## Subsequent Google popup authentication update

The subsequent user request replaces the original redirect flow with the official Google Identity Services popup button. `frontend/src/GoogleSignIn.tsx` loads Google's official script, initializes popup mode with automatic selection disabled, and posts credentials to FastAPI. `backend/google_identity.py` uses the official Google token verifier. The previous Authlib redirect/callback implementation is removed. Existing secret/redirect settings in older `.env` files are ignored; this flow uses only the public client ID and authorized JavaScript origin.

Migration `0002` adds a unique nullable `users.google_sub` without replacing existing user, allowlist or meeting rows. Existing approved users link to their verified subject on their next successful login. Session rotation, nonce/origin checks, verified-email/allowlist checks and separate admin login remain enforced. Logout also remains available to a session whose allowlist access has subsequently been revoked, while retaining Origin and CSRF validation.

Approved emails are managed locally through Administration; this repository includes no user allowlist records or preconfigured user sessions.

Latest validation supersedes the earlier authentication results: **29 backend tests**, **6 Chrome workflows**, type-check, lint, production build, migration checks and the Python dependency audit pass. The auth tests verify real RSA signatures against mocked Google certificates; browser tests stub Google's UI while sending signed credentials through the actual backend verifier. An actual Google script load against the configured client reported **The given origin is not allowed for the given client ID**. The Cloud administrator must register `http://localhost:5173` as an Authorized JavaScript origin on the Web application client documented in [GOOGLE_OAUTH.md](GOOGLE_OAUTH.md). No real Google account selection or successful live sign-in was claimed.
