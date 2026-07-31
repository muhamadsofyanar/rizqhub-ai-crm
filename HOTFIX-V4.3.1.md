# RizqHub V4.3.1 — Broadcast PostgreSQL Lock Hotfix

Fixes:

`django.db.utils.NotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`

## Root cause

The broadcast worker locked `BroadcastRecipient` while also using `select_related()` for nullable `contact` and `group` relations. PostgreSQL implements those relations as outer joins and refuses `FOR UPDATE` on the nullable side.

## Change

The worker now locks the concrete `BroadcastRecipient` and `Broadcast` rows in separate queries. Nullable contact/group rows are no longer part of the locking query.

## Install

Replace only:

`crm/tasks.py`

Commit and redeploy. No migration or environment-variable change is required.

## Safe recovery

1. Cancel the currently stuck broadcast before deploying this patch.
2. Redeploy.
3. Create a new test broadcast to one owned/test group.
4. Confirm the recipient changes from `Antrean` to `Mengirim` and then `Terkirim`.
5. Only then create a larger broadcast.
