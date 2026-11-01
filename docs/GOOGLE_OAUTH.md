# Google account popup login

SUMX uses Google's official **Google Identity Services Sign In With Google button**, rendered by `google.accounts.id.renderButton`. It is initialized with `ux_mode: "popup"`, `auto_select: false`, and `button_auto_select: false`. React receives an ID token in the credential callback and sends it in JSON to `POST /api/auth/google`. It does not use an OAuth authorization-code redirect, One Tap, Firebase, Supabase, or a hosted user database.

The button opens Google's account/consent UI. Account chooser appearance depends on Google's current session and browser behavior; SUMX does not guarantee a particular chooser screen or sign the user out of Google itself.

## Google Cloud Console

Open [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients) and use an OAuth client of type **Web application**. Set your own Web application client ID in the project-root `.env`:

```text
your-web-client-id.apps.googleusercontent.com
```

Verify the client type in Google Cloud; a client ID suffix alone cannot establish its type. If it is another client type, create a Web application client and update `GOOGLE_CLIENT_ID` in `.env`.

| Google Cloud field | Local value |
| --- | --- |
| Authorized JavaScript origins | Add both `http://localhost` and `http://localhost:5173` as separate entries |
| Authorized redirect URIs | None required by this popup credential-callback implementation |

Origins include scheme, hostname and port when used, with no path or trailing slash. Google's [local-development setup guidance](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid) requests both the bare localhost origin and the origin with your development port. The Vite frontend serves at `http://localhost:5173` and refuses to silently move to another port. Its internal API proxy points to `127.0.0.1:8000`; that internal address is not the browser origin and does not belong in the JavaScript origins field for this setup.

The previous `http://localhost:8000/api/auth/google/callback` is unused and can be removed from the Google client's redirect URI list if no other application uses it. `GET /api/auth/google` no longer starts a redirect and the old callback route no longer exists. No `redirect_uri`, `login_uri`, OAuth code or client secret is submitted by this flow.

For production, register the real HTTPS frontend origin, set `APP_URL` to that exact origin and `COOKIE_SECURE=True`. Serve `/api` through the same browser origin. If you intentionally use another local hostname or port, register its origin and update `APP_URL` to match it; do not mix `localhost` and `127.0.0.1` during a sign-in session.

If Google's app audience is in Testing and requires test users, add the intended account there too. Google app access and SUMX's local allowlist are separate checks. Add approved users through SUMX Administration and configure any required Google Cloud test users separately. `approved.user@example.edu` below is an example, not a preconfigured account.

## Environment and startup

```dotenv
GOOGLE_CLIENT_ID=your-web-client-id.apps.googleusercontent.com
APP_URL=http://localhost:5173
COOKIE_SECURE=False
```

`False` is for local HTTP only. Keep the existing random `SESSION_SECRET` server-side. The frontend fetches the public client ID and a browser-bound nonce from `/api/auth/google/config`; it does not need a `VITE_GOOGLE_CLIENT_ID` variable. `GOOGLE_CLIENT_SECRET` and `OAUTH_REDIRECT_URI` in an older `.env` are ignored and may be removed. No secret was copied to the frontend or documentation.

After pulling these changes:

```powershell
cd C:\Projects\Sumx
.\.venv\Scripts\python -m pip install -r backend\requirements-lock.txt
.\.venv\Scripts\python -m alembic upgrade head
.\run.ps1 api
# In another terminal:
.\run.ps1 web
```

Restart the API after environment changes, and restart Vite after changing its host configuration. The additive `0002` migration adds a nullable unique Google subject ID to users. Existing users, admin credentials, allowlist rows, meetings and sessions are retained. On an approved user's first verified popup login, SUMX links the Google subject to that existing email/account without changing its internal ID or meeting ownership.

## Identity verification and sessions

- The official `google-auth` verifier checks the signature against Google's certificates, Google issuer, configured client audience, issue time and expiration. SUMX also requires `email_verified` to be the boolean `true`, a nonempty Google `sub`, and a matching browser-bound nonce.
- The JSON login endpoint requires the configured frontend Origin and a short-lived nonce held in a signed HttpOnly cookie. The nonce passed to GIS must appear in the verified token. Direct email-only requests, missing/expired nonce requests and cross-origin requests fail.
- The verified email must match an active `allowed_emails` row. Missing/inactive entries return a clear access-denied error. The stable Google subject is linked to the local user, and an email cannot silently be reassigned to a different subject.
- Admins continue to use the separate email/password login. No Google session can promote a user to admin or bypass the approved-email check.
- SUMX issues a random HttpOnly, SameSite=Lax session cookie with Secure enabled in HTTPS production. Its database token is hashed. Google credentials are not stored in localStorage, sessionStorage, the database, logs or a URL.
- Sign out deletes the SUMX session and calls GIS `disableAutoSelect` if loaded. Settings → Switch Google account signs out of SUMX and returns to the button, where another account can be selected. It does not revoke Google consent or log the user out of Google.

Google loads `https://accounts.google.com/gsi/client`. Do not self-host or modify that library. Allow it in your production CSP, together with Google's documented frame/connect sources. The frontend development server sends `Cross-Origin-Opener-Policy: same-origin-allow-popups`; use that compatible policy at the production proxy if you set COOP, rather than a policy that isolates the login popup. Preserve HTTPS, CSRF, token verification and session security. See [Google's button guide](https://developers.google.com/identity/gsi/web/guides/display-button), [JavaScript reference](https://developers.google.com/identity/gsi/web/reference/js-reference), and [server-side token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

## Verification

Backend tests use locally signed RSA tokens and mocked Google certificates while exercising the actual official verifier. Coverage includes approved login, preserved existing meetings, unapproved/inactive email rejection, admin separation, wrong signature/issuer/audience/expiry/nonce, identity rebinding rejection, logout and revoked-session rejection. Migration tests verify existing records survive.

Browser tests stub only Google's UI script and use signed fixture tokens through the real backend verifier and local database. They verify popup initialization flags, token callback submission, denied access, successful login, account switching and logout. Other recording/upload/OpenRouter and light-theme workflows remain covered.

A real Google account selection/consent has not been completed automatically. After saving the authorized JavaScript origin in Cloud Console, visit `http://localhost:5173/login`, click the official button and choose `approved.user@example.edu`. Expect the dashboard after successful verification. Test another unapproved account and expect access denied. Sign out and confirm private API requests fail; use Switch Google account to return to account selection. If Google reports `origin_mismatch`, check the exact origin on this same client ID and allow time for saved Cloud settings to propagate. Popup blockers or browser extensions may also prevent Google's UI from loading.
