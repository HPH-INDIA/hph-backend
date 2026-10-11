# Temporary lead coverage

Lead coverage gives another lead temporary access to a team's manual approvals,
Kairon reports, and dashboard. It does not change permanent reporting lines.

## Workflow

- A lead opens **Lead coverage**, chooses another lead under the same manager,
  enters the first and last coverage dates, and sends a request.
- The covering lead accepts or declines the incoming request. Pending requests
  grant no access.
- A manager can select two leads in their own team and assign coverage directly,
  without recipient approval.
- Approved coverage is active from the start date through the end date,
  inclusive, in **Asia/Kolkata**. Future coverage is scheduled automatically;
  expired coverage grants no access. No background expiry job is required.
- Either participating lead or their manager can cancel pending or approved
  coverage. Cancellation ends access immediately on subsequent API requests.

The covering lead uses the team selector in the dashboard and reports to choose
the covered team. The selected team's QA and coder totals remain separate from
the covering lead's own team. Manual approvals record the actual reviewing lead.

## Access rules

Both participants must be active leads in the same project and report to the
same manager. A lead can only request coverage for their own team. A manager
can only assign their own leads. The server checks these conditions again when
granting access, so changing a participant's role, project, reporting line, or
active status cannot retain an otherwise invalid grant.

Coverage does not grant user administration, stage editing, imports, or manager
permissions. Existing feature permissions continue to apply. Personal reports
and submission editing retain their existing scope. Coverage allows reviewing
the source lead's employees; it does not allow reviewing the source lead's own
manual submission.

Overlapping assignments for the same source lead and overlapping chains of
coverage are rejected. Current access is evaluated using today's business date,
independently of the dates selected for a historical report. Request, decision,
and cancellation identities and timestamps are retained for audit.

## API

All endpoints use the existing encrypted API transport and response envelope.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/lead-coverage` | Relevant requests and assignment history |
| `GET /api/lead-coverage/options` | Eligible leads and current IST date |
| `POST /api/lead-coverage` | Request coverage or assign as manager |
| `POST /api/lead-coverage/{id}/approve` | Accept as covering lead |
| `POST /api/lead-coverage/{id}/reject` | Decline as covering lead |
| `POST /api/lead-coverage/{id}/cancel` | Cancel request or stop coverage |

Creation accepts `coveringLeadId`, `startsOn`, `endsOn`, optional `reason`, and
`sourceLeadId` for manager assignment. Dates use `YYYY-MM-DD`.

## Rollout

Apply the included database migration before starting the updated API, then
deploy the matching frontend. Existing reporting lines and records require no
backfill. The migration creates an empty coverage history; no access is granted
until someone requests and accepts coverage or a manager assigns it.

Run workflow and access tests against a separate PostgreSQL test database,
including the existing manual review, lead dashboard, Kairon, and holds tests.
