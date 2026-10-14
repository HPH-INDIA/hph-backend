# Manual record review

All writes require Reports write access. Clients remain read-only.

| Role | Edit | Delete | Review |
| --- | --- | --- | --- |
| Coder | Own pending/rejected records; resubmission returns to pending and clears review metadata | Own pending records only | None |
| Lead | Own QA entry behavior remains auto-approved | None | Pending records for own and currently covered coders |
| Manager | Existing controlled upload behavior | Any status within authorized team scope | Pending team records, including historical pending QA entries |

Manager daily reviews support individual/page/all-matching selection, newest/oldest date sorting, name/date search, and status filters. Selections reset when the team, people, group, date window, search, or status filter changes. All-matching selection spans pages and refers only to the chosen scope and filters. Bulk delete accepts explicit IDs, never an unfiltered delete-all flag. A batch is atomic: any missing or unauthorized ID aborts deletion. Deletion is permanent and the UI requires a confirmation listing the affected people/dates/statuses. Maximum delete batch: 5,000 records.

Review, edit, and delete lock existing rows before validating status or modifying them. Approved coder records cannot be silently resubmitted. Review batches preserve existing best-effort skips for missing, unauthorized, or already reviewed entries.

Pending and approved manual production/adjustments are included in calculations. Rejected records remain visible in source history but are excluded from performance calculations. Successful edits, reviews, and deletes invalidate manual-record and dashboard caches. No schema migration is required.

## Mixed-program duration

New self-entry records with both PVP and Foundation production require `foundationHours`. Foundation duration is productive work allocation, not a downtime deduction. An 8-hour target day is reduced by downtime, idle, leave, and non-Huddle meetings. The remaining productive time after Foundation allocation belongs to PVP. The duration must be positive and leave positive PVP time. All entered hours (Foundation plus downtime, idle, leave, and all meeting entries including Huddles) must total at most 10. Yup validates this before submission; the API also validates it.

With PVP rate P, Foundation weekly rate F, productive hours H, and Foundation hours f:
- Normalized daily target = ((H - f) × P + f × F) / H.
- Adjusted target / saved adjusted CPD = ((H - f) × P + f × F) / 8.
- Actual manual CPD = completed charts × 8 / H.

PVP-only and Foundation-only submissions do not store an allocation. Historical/imported rows with a null allocation retain the existing standard-time chart-mix calculation. Migration `115a697d0959` adds a nullable duration column and range constraint without backfilling or recalculating historical records. Changed bulk uploads clear duration when resetting entries to PVP-only.
