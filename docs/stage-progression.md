# User stages and Foundation progression

The Teams page opens on **Stages**. Each user has a main timeline and, when eligible, a separate Foundation timeline. Dates are inclusive and the current business date is evaluated in Asia/Kolkata.

## Main stages

`users.join_date` is the canonical date of joining. Existing cohort membership dates are a compatibility fallback when a user has no recorded joining date. Editing a joining date also synchronizes the membership date.

| Stage | Start | End |
| --- | --- | --- |
| Training | Joining date | Joining date + 13 days |
| M1 | Joining date + 14 days | Joining date + 43 days |
| M2 | Joining date + 44 days | Joining date + 73 days |
| M3 | Joining date + 74 days | Joining date + 103 days |
| M4 | Joining date + 104 days | Joining date + 133 days |
| Steady State | Joining date + 134 days | Open-ended |

Training always lasts 14 calendar days. Main progression depends only on the joining date, even when the user has no Kairon or manual charts. Earlier/later chart completions never change this schedule. Missing joining dates are explicit: no main timeline is fabricated. Users without a cohort can still progress.

Kairon imports refresh evidence and rebuild the main cache using these calendar rules and manager restart history. Imports and reads preserve saved manual-productivity snapshots; submissions and confirmed target/stage changes recalculate them. The stage-rule rollout performs an explicit one-time recalculation of existing saved targets.

## Foundation eligibility and targets

The trigger remains **completed_date**, not created/allocated date. Every CODING lead and employee starts the Foundation ramp on their earliest completed Foundation chart. PVP-first, Foundation-first, no-PVP, and same-day first completions all use the same Foundation rules. Without a Foundation completion, the automatic Foundation timeline has not started.

| Foundation stage | Inclusive elapsed days | Default charts/day |
| --- | --- | --- |
| W1 | 0–6 | 7 |
| W2 | 7–13 | 14 |
| W3 | 14–20 | 20 |
| W4 | 21–27 | 30 |
| Foundation Steady State | 28 onward | 30 |

Weeks run for seven calendar days; activity gaps do not restart the ramp. Foundation Steady State never changes the main M stage. The main target applies to PVP; the Foundation weekly or Foundation Steady State target applies to Foundation work. They are combined using the actual completed chart mix as described below. Before the Foundation timeline starts, the existing main-rate fallback remains available.

CODING managers configure targets using **Stage targets**, beside **Refresh stages** in Teams → Stages. Stage targets opens a dedicated screen at `/team/stage-targets`. Cohort, lead and main-stage filters use the standard side drawer, with draft values, Apply, Reset and Cancel.

## Manager stage restarts

Managers with write access to `user_management` can use **Change stage** beside a user’s main or Foundation stage. The API is restricted to CODING managers and their own CODING leads/employees. A manager can select M1–M4 or main Steady State, W1–W4 or Foundation Steady State, or restore automatic progression.

The selected stage starts afresh on the chosen effective date and then advances automatically. Main stages each last 30 calendar days; Foundation weeks each last 7 days. Starting M2 therefore reaches M3 after 30 days, M4 after 60 days, and main Steady State after 90 days. Starting W2 reaches W3 after 7 days, W4 after 14 days, and Foundation Steady State after 21 days. The other program continues independently.

Effective date defaults to today in Asia/Kolkata, or the last working day for a former employee. Historical changes are allowed within employment, but cannot precede a later stage-change event for the same program. Dates before joining or first Foundation completion, dates after departure, and future dates are rejected. Foundation restarts require an observed first Foundation completion; an already-recorded manager restart survives a later import correction that removes that evidence.

A reason and confirmation are required. Stage history before the effective date is retained. From that date onward, this user’s saved manual targets and adjusted CPD are recalculated using the effective Main/Foundation stages, target rule versions, recorded program counts, and time deductions. Chart counts, time entries, approval/rejection status, and other users are preserved. All changes commit atomically; failures roll back timeline, audit, and snapshots.

Multiple restarts can revisit the same stage. Same-day corrections use the most recent event without overlapping periods. **Restore automatic progression** resumes the original joining-date or first-Foundation-completion schedule from the chosen date; it preserves earlier manager history. Kairon imports never delete restart events. `user_stage_changes` records actor, program, selected stage (null for restore), reason, effective date, timestamp, and recalculated record count. The Teams timeline shows repeated periods and the manager history.

## Changing stage targets

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
* `dd5f02f33536` after `aa10c0d4e201`: duration-based main cache backfill, RLS-protected manager restart history, and non-overlapping user-period ranges that allow repeated stages.
* `44a1e2e48e73`: decimal daily-target snapshots and the two component target columns. Existing saved values are preserved; migration does not recalculate history.

* Adds `users.join_date`.
* Adds `user_stage_evidence`, one row per user, with earliest completed dates and refresh timestamp.
* Adds `foundation_target_rules`, seeded with the five defaults and protected from overlapping effective ranges.
* Enables RLS on both new tables; access goes through the existing authenticated Flask API and its database connection.
* Extends `GET /api/team/coders` with joining date, employment status, main periods, Foundation progression, and source dates.
* Adds manager-only `GET /api/team/foundation-target-rules` and `POST /api/team/foundation-targets/change`.
* Accepts and returns `join_date` on the existing user API.
* Adds `POST /api/team/users/<user_id>/stage-changes` with `program`, `stageCode` (null restores automatic), `effectiveFrom`, and `reason`.
* Returns `stageChanges` in the Teams overview.

The live Supabase schema and the supplied 41 joining dates were updated separately on 8 October 2026. The data transaction matched case-insensitive emails, required exactly one match per address, and was restricted to those 41 users. Employee IDs were not changed. A before-state backup and the exact SQL were retained outside the repositories in the task workspace.

Deploy the backend and frontend code together. Run `flask db upgrade` before starting the updated backend. The added audit and component columns must exist before target editing or manager stage changes are enabled. For the stage-rule rollout, run `flask refresh-user-stages --recalculate-targets` after migrating and before starting the updated backend. This command refreshes Kairon evidence and rebuilds all CODING timelines, then recalculates saved manual target snapshots in one transaction. Retain a before-state snapshot for rollout verification. On a different database, run the migration and a Kairon import to populate evidence; joining dates should be supplied through the user API rather than guessed from chart activity.

## Verification

Tests cover 14-day Training and all 30-day main boundaries independently of completions, Foundation-first/no-PVP/date-only ties with a uniform ramp, W4 to Foundation Steady State, manager restarts of every main/Foundation stage, repeated stages, restore automatic, departure clipping, corrections, reassignment, import retries and rollback, access controls, target history, joining-date edits, and atomic adjusted-target recalculation. The exact roster backfill was compared with the Python calculator for all 41 users in an isolated local database.

## Read-only access for leads and coders

The `stage_view` feature grants read-only Stages access to CODING lead and employee roles. Leads see themselves and employees reporting directly to them; coders see only themselves. The server intersects this scope with the viewer's CODING project before applying filters or pagination. Filters never expand this scope. Timelines, current main/Foundation targets, source dates and manager stage-change history are visible. Refresh stages only reloads the view.

Managers retain their existing `user_management` permissions and team view. All stage, target, cohort and assignment writes still require the manager role and write permission; `stage_view` grants no administrative access. Read-only readers skip the manager-only cohort list and do not see editing controls or the Stage targets editor. New custom roles can be assigned View stages by an administrator.
