# Dashboard redesign and AI cost analytics

The dashboard now follows the supplied light macOS-style reference: the existing SUMX sidebar/logo, a personalized header, four metric cards, three recording/upload/summary actions, a searchable date-sorted meeting table, and a spending card. It uses the existing system typography, light tokens, rounded white surfaces and accessible controls. The muted gray and small-button blue remain slightly darker than the illustrative reference to maintain text contrast. No dark theme was added.

Regular accounts see only their own combined spending and meeting totals. Service-level STT/LLM/TTS amounts, model costs, participant totals and CSV exports are restricted by FastAPI administrator checks. The new **Analytics** sidebar item and `/analytics` route are administrator-only. Existing `/admin` access management, authentication, meeting details and processing routes remain available.

## Metrics and filters

- Total meetings and captured duration come from the current user's retained meeting records. Short recordings show `<1 min`; longer totals show hours/minutes.
- Summaries ready counts meetings with a completed summary version. Weekly ready-summary changes use the first persisted summary time, so regenerating the same meeting does not inflate the count.
- Count/duration comparisons use recorded data from the current seven UTC calendar days and the equivalent elapsed interval one week earlier. A newly created account with no previous-week history gets a truthful unavailable comparison instead of an invented trend. Spending percentage changes are omitted when the prior amount is zero, or either period contains pending/estimated charges.
- The recent table preserves queued, processing, completed and failed meetings. Costs include all matching provider generations, including repeated summaries and narration. History supports a summaries-only query/filter, search, oldest/newest sorting and pagination.
- Spending offers 7/30/90-day UTC calendar ranges, including the current partial day. Bars use actual persisted daily amounts. Zero-activity days remain zero. The previous equivalent period is shifted by the selected number of days; daily amounts are also available in an accessible table.
- Admin participant counts include meetings created or charged in the selected period. Deleted meeting content is labeled `Deleted meeting`, while its accounting remains. Average cost divides recorded period spending by the number of distinct meetings in that scope. Pending averages are marked partial. Duration is available only for retained meeting metadata.
- Participant search matches name/email. Expanding a participant fetches individual meeting costs only when needed. Admin CSV exports the whole selected period's deduplicated ledger, including operation, model, generation/request IDs, supplied token/audio units, exact USD, status and source. Spreadsheet formula prefixes in text fields are escaped.

## Precise storage and request accounting

The existing `api_usage` table is extended rather than replaced. Migration `0003` preserves every historical row and existing unknown cost. It changes USD storage to exact fixed-point decimal text on SQLite and `NUMERIC(24,12)` on PostgreSQL, adds cost state and a unique billing key, and adds date/user-date indexes. SQLite sums monetary values through a Decimal aggregate; it does not use floating-point SQL SUM. New APIs return USD strings to preserve precision through JSON. Browser number conversion is for display/chart positioning only.

Provider JSON monetary values are parsed as Decimal before storage. The supported scale is twelve decimal places in USD. Existing floating-point values are converted from the values already recorded; the migration does not claim to reconstruct precision absent from older records.

- `actual`: an amount explicitly supplied by OpenRouter or verified with its generation API.
- `estimated`: an explicitly labeled estimate, if present in the ledger. This implementation does not fabricate estimates from model prices.
- `pending`: no confirmed amount is available. Known amounts remain visible with a pending indicator; missing charges are excluded from monetary sums.

Generation ID, or request ID when no generation ID is returned, forms the deduplication key. Separate generations accumulate, while the same provider request counts once. Existing duplicate rows remain in the database for audit but are excluded from totals. A later confirmed amount can update a pending canonical request. Requests without either ID cannot safely be deduplicated across ambiguous provider retries; no matching request is invented.

If an HTTP-successful AI response contains billed usage but malformed summary/transcript/audio content, its known usage is still recorded before the job is marked failed. Completed transcript data remains retained. An ambiguous transport timeout without a provider ID/cost remains an unresolved billing limitation; the application does not claim exactly-once remote billing.

Administrator **Reconcile provider costs** retrieves available amounts for pending or estimated generation IDs through the existing backend OpenRouter integration. It can confirm up to 30 records per operation and remains rate-limited. Some provider records may never become retrievable; these remain pending.

## API additions

Implementation files changed:

| Area | Files |
| --- | --- |
| Dashboard and shared cost UI | `frontend/src/Dashboard.tsx`, `CostWidgets.tsx`, `CostAnalytics.tsx`, `analytics.css`, and the existing route/sidebar integration in `main.tsx` |
| Ledger and aggregates | `backend/analytics.py`, `money.py`, `models.py`, `jobs.py`, `provider.py`, and existing route/reconciliation integration in `main.py` |
| Migration | `backend/migrations/versions/0003_precise_usage_costs.py` |
| Verification | `backend/tests/test_cost_analytics.py`, isolated browser fixture server, analytics browser tests, and the existing workflow locator updated for table links |
| Documentation | This document, `README.md`, and `docs/IMPLEMENTATION.md` |

| Endpoint | Scope |
| --- | --- |
| `GET /api/dashboard` | Current user's metrics, recent meetings, combined costs and spending |
| `GET /api/analytics/spending?days=7` | Current user's combined daily spending only |
| `GET /api/meetings` | Existing history route, now with combined costs, sorting and summaries-only filtering |
| `GET /api/admin/analytics/costs` | Admin platform, operation, model and participant aggregates |
| `GET /api/admin/analytics/users/{id}/meetings` | Admin participant expansion |
| `GET /api/admin/analytics/export?days=30` | Admin CSV ledger export |

## Applied migration and validation

An online SQLite backup was saved under `data/sumx-before-cost-analytics-20261031-225029.db` before applying the local migration. Before/after counts were unchanged: one user, one approved-email row, one meeting and zero provider usage rows. The existing queued meeting was preserved. The requested email remains active on the allowlist. No analytics fixtures or historical charges were added to the application database.

For another checkout, apply the existing migrations and restart the API/worker processes after updating:

```powershell
.\.venv\Scripts\python -m alembic upgrade head
```

No new environment variables, frontend libraries or charting dependencies are required. The small daily chart uses native SVG with screen-reader descriptions, keyboard-focusable data points and a table alternative. No mock chart values ship in the application.

Checks completed: **35 backend tests**, **8 Chrome workflows**, Ruff, frontend TypeScript, ESLint, Vite production build and Alembic schema comparison. Tests cover decimal precision, request deduplication, repeated generations, missing/estimated costs, malformed-but-billed responses, date filtering, ownership/role rejection, CSV export, migration preservation, responsive layout, sorting and summary filtering. Existing Google popup, recording, upload, transcript editing, narration and light-mode workflows pass as regressions.

Screenshots under `frontend/test-results` use explicitly isolated database fixtures, not production content. Real OpenRouter inference, physical microphone hardware and PostgreSQL operation were not retested for this upgrade. The existing Google Cloud authorized-origin configuration requirement still applies to live Google sign-in.
