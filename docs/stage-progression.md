# User stages and Foundation progression

The Teams page opens on **Stages**. Each user has a main timeline and, when eligible, a separate Foundation timeline. Dates are inclusive and the current business date is evaluated in Asia/Kolkata.

## Main stages

`users.join_date` is the canonical date of joining. Existing cohort membership dates are a compatibility fallback when a user has no recorded joining date. Editing a joining date also synchronizes the membership date.

| Stage | Start | End |
| --- | --- | --- |
| Training | Joining date | Day before first completed Kairon chart |
| M1 | First completed Kairon chart | Start + 29 days |
| M2 | M1 start + 30 days | M1 start + 59 days |
| M3 | M1 start + 60 days | M1 start + 89 days |
| M4 | M1 start + 90 days | M1 start + 119 days |
| Steady State | M1 start + 120 days | Open-ended |

Without a completion, Training has a known start and an unknown end. Production on the joining date produces no artificial, negative-length Training period. A completion before the joining date is flagged for source correction and does not start M1. Users without a cohort can still progress. Manual daily production no longer starts or shifts M1.

The Kairon calculation replaces stored manual stage shortcuts when imports finish. Saved manual-productivity snapshots remain unchanged by imports and reads. Manual resubmissions and explicitly confirmed target changes recalculate them using the stage and target effective on the record date.

## Foundation eligibility and targets

The clarified trigger is **completed_date**, not created/allocated date. Compare the earliest completed PVP and Foundation chart for each user:

* PVP earlier than Foundation: Foundation weekly ramp begins on the first Foundation completion.
* Foundation earlier than PVP, or Foundation present with no PVP: no weekly substages, including when PVP appears later.
* Both first completions on the same date: order cannot be established from date-only evidence. Show this explicitly; do not assume eligibility.
* No completed Foundation chart: awaiting first Foundation completion.

| Foundation stage | Inclusive elapsed days | Default charts/day |
| --- | --- | --- |
| W1 | 0–6 | 7 |
| W2 | 7–13 | 14 |
| W3 | 14–20 | 20 |
| W4 | 21–27 | 30 |
| Foundation Steady State | 28 onward | 30 |

Weeks run for seven calendar days; activity gaps do not restart the ramp. Foundation Steady State never changes the main M stage. The main target applies to PVP; the Foundation weekly target applies to eligible Foundation work. They are combined using the actual completed chart mix as described below. Foundation-first users and date-only ties have no weekly target; the main-stage rate continues to apply.

CODING managers configure targets using **Stage targets**, beside **Refresh stages** in Teams → Stages. There is no separate target tab. Cohort, lead and main-stage filters use the standard side drawer, with draft values, Apply, Reset and Cancel.

Every target edit opens a confirmation with no preselected scope:

* **From today:** use the server's Asia/Kolkata date, preserve earlier target history and snapshots, and recalculate affected records dated today or later.
* **From the start of the program:** replace that stage's target across its full history and recalculate all affected saved daily records, including former employees through their last working day. The original 1900-01-01 rule boundary covers older records imported later; it is not an employee joining date.

Both choices supersede scheduled changes for that same stage from the chosen start onward. Repeated edits on the same day are supported. A `stage_target_changes` audit retains actor, reason, scope, effective date, prior rule versions and recalculated record count. Rules, audit and saved CPD are committed in one transaction; a recalculation failure rolls back everything. Chart counts, time entries and review decisions are preserved. Main and Foundation stages themselves are unchanged.

The API accepts `applyFrom: "today" | "program_start"`. Older clients can still schedule an explicit future `effectiveFrom`; supplying both or neither is rejected. The explicit-date compatibility path preserves snapshots. Rules use exclusive effective-to dates; stage periods have inclusive end dates.

## One combined adjusted target

Unknown allocations do not justify adding two full-day quotas or guessing hours by program. Instead, each completed chart earns standard time based on its program's stage target. This uses configured expectations, not measured handling time.

Let `P` and `F` be completed PVP and Foundation counts, and `Tp` and `Tf` their respective full eight-hour targets on the record date:

```text
Earned standard-day fractions = P / Tp + F / Tf
Full-day target for the observed mix = (P + F) / (P / Tp + F / Tf)
Adjusted charts/day = full-day mix target × available hours / 8
```

Available hours retain the existing formula: eight hours minus downtime, idle, leave and non-Huddle meeting time, floored at zero. Huddles do not reduce saved CPD. The existing operational-efficiency report retains its own time-exclusion and 120% display-cap conventions.

Example: `Tp=30`, `Tf=7`, `P=15`, `F=3`. The mix target is `19.384615` charts for eight hours. With two hours of deductions the saved adjusted target is `14.54` charts. The same rates support pure PVP days (30/day) and pure Foundation W1 days (7/day). An observed mix is needed only when the rates differ.

For a zero-completion day with unequal applicable rates, combined charts/day stays null; the two component targets remain available. Equal rates need no mix. A mixed day containing work with a missing/zero required rate also remains null rather than dividing by zero. The API returns fractional daily targets without integer truncation. Internal mix targets retain six decimals; saved adjusted CPD is rounded half-up to two decimals.

Manual snapshots store `pvp_daily_target`, `foundation_daily_target`, `daily_target` and `adjusted_cpd`. Reads and approvals do not rewrite them. Foundation edits recalculate records whose Foundation stage matches; main edits recalculate records whose main stage matches. Other stages, dates outside the chosen scope, and records after departure are untouched. A confirmed change also invalidates cached manual records, team views and reports.

The monthly adjusted goal uses the saved adjusted value on known Foundation-mix days; days without an observed mix retain the existing main-stage planning baseline. Daily efficiency resolves Manual and Kairon chart mixes separately, using date-specific rates, and does not assume that the two sources have identical chart counts.

## Imports and employment dates

Both legacy snapshot imports and cumulative finalization refresh `user_stage_evidence` and `user_stage_periods` in the finalization transaction. The entire CODING lead/employee population is recalculated so corrections, reassignment away from a user, and replacement snapshots cannot leave stale stages. Unfinished chunks do not publish stage evidence. A failed refresh cannot mark a batch completed. Repeated finalization is safe.

Foundation periods are derived from the saved evidence, so both timelines advance automatically by date without waiting for another upload. The Teams cache is invalidated after foreground or background Kairon completion; it also refreshes on focus and every minute.

Periods are clipped to `last_working_day`. Later stages are not reached. Former employees remain visible with the stage and target at their last working day. Deactivation and joining-date edits rebuild that user's main periods.

## Schema and API

Migrations:

* `f469f0fdd1f2` after `d3f8b0e52147`: joining dates and stage evidence.
* `04bba93362a2`: RLS-protected target-change audit.
* `44a1e2e48e73`: decimal daily-target snapshots and the two component target columns. Existing saved values are preserved; migration does not recalculate history.

* Adds `users.join_date`.
* Adds `user_stage_evidence`, one row per user, with earliest completed dates and refresh timestamp.
* Adds `foundation_target_rules`, seeded with the five defaults and protected from overlapping effective ranges.
* Enables RLS on both new tables; access goes through the existing authenticated Flask API and its database connection.
* Extends `GET /api/team/coders` with joining date, employment status, main periods, Foundation progression, and source dates.
* Adds manager-only `GET /api/team/foundation-target-rules` and `POST /api/team/foundation-targets/change`.
* Accepts and returns `join_date` on the existing user API.

The live Supabase schema and the supplied 41 joining dates were updated separately on 8 October 2026. The data transaction matched case-insensitive emails, required exactly one match per address, and was restricted to those 41 users. Employee IDs were not changed. A before-state backup and the exact SQL were retained outside the repositories in the task workspace.

Deploy the backend and frontend code together. Run `flask db upgrade` before starting the updated backend. The added audit and component columns must exist before target editing is enabled. On a different database, run the migration and a Kairon import to populate evidence; joining dates should be supplied through the user API rather than guessed from chart activity.

## Verification

Tests cover calendar boundaries, same-day training, missing completions, Foundation-first exclusion, date-only ties, W4 to Foundation Steady State, departure clipping, corrections, reassignment, import retries and rollback, access controls, target history, and joining-date edits. The exact roster backfill was compared with the Python calculator for all 41 users in an isolated local database.
