# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

Upsell AI uses **PMS** (reads the arrivals book, writes on an accepted
upgrade), **Email** (drafts and, once approved, sends), and nothing else.
Messaging, Sheets and every stub family (POS, Accounting, Reviews, Calendar,
Payments, Procurement, Locks, Courier) are wired into `core/` for the family
but this agent never calls them.

## PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/*.json`. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/*.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads; upgrade writes go through `update_reservation`/`add_note`. |
| `cli` | universal | a JSON-speaking CLI | Advanced. Bridges to a vendor command line tool. |

**What this agent reads:** `list_reservations(date_from, date_to,
status="confirmed")` for the arrivals book, and `get_rates(date_from, date_to,
room_type=...)` for both the target tier's rack price (Track B's surcharge)
and its per-night availability. **What it writes:** on an accepted upgrade,
`update_reservation()` (new room type and total) and `add_note()` (the PMS
note), both guarded by `pms_write` - see `docs/safety.md`.

**Guest CRM facts your PMS may not model as columns** - `tier`, `vip_score`,
`occasion`, `profile`, `history`, `outreach_status`, an explicit `party`
description - travel in `Reservation.extra`, which every PMS adapter passes
through unchanged for any field it doesn't recognize (`core/adapters/pms_mock.py`,
`pms_csv.py`). For `csv`, just add those columns to `reservations.csv` and
they arrive in `.extra` for free. For `cloudbeds`, `core/adapters/pms_cloudbeds.py`
maps a fixed field set - ask your Claude session to extend it to also read a
custom field, a tag, or a guest note into `.extra` if you want the profile
matcher and repeat-offer logic to have real data instead of a blank profile
(a blank profile is handled gracefully - see `docs/how-it-works.md` step 6 -
but it means "nothing invented", not "no upsells at all").

**`csv` - the one that always works.** Export from your PMS and drop the
files in `data/imports/`; `reservations.csv` needs at minimum `id, status,
check_in, check_out, room_type_id, room_type_name, adults, children, source,
total, currency, guest_email, guest_first_name, guest_last_name`, plus
whichever `tier` / `vip_score` / `occasion` / `profile_*` columns you want to
carry through to `.extra`. In CSV mode the agent cannot write back to your
PMS, so an accepted upgrade is appended to `data/exports/pms_writes.csv`
instead, for a person to apply by hand - see `docs/integrations.md` in the
factory reference for the full column list.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorize it
once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scopes: `read:reservation`, `write:reservation`, `read:rate`.

## Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Appends to `data/exports/sent_email.jsonl`. What `make demo` uses. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

**What this agent sends:** the outreach email (Track A), the upgrade offer
and its confirmation (Track B) - all three go through the review queue first,
never straight from a scan. `tools/review.py send` and
`tools/upgrade.py execute` are the only two places `email.send()` is called.

**`imap`.** In `.env`:

```
EMAIL_ADDRESS=reservations@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
```

**`gmail`.** Google Cloud Console: enable the Gmail API, create an OAuth
client of type **Desktop app**, download the JSON to `credentials.json`, then
`pip install google-api-python-client google-auth-oauthlib` and run
`make doctor` once to complete the browser login.

## Payment links and a guest portal - not built

The roster promises "secure payment links by email or text" and "a branded
guest portal page". Neither exists here on purpose - see
`docs/how-it-works.md`, "Design decisions" #7. A guest replies to a normal
email and a human runs `tools/outreach.py respond` or `tools/upgrade.py
respond`. If you want a real payment link, the shape to add is a `Payments`
adapter call inside `tools/upgrade.py execute_one()` before the confirmation
email - the stub interface is in `core/adapters/base.py`.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a PMS adapter for **<your system>**, and I want it to also carry
> `tier`, `vip_score`, `occasion` and `profile` into `Reservation.extra`. Its
> API docs are at **<url>** and I have credentials in `.env` as `<VAR names>`.
> Copy `core/adapters/pms_csv.py` as the shape, implement `ping`,
> `capabilities`, `list_reservations` and `get_rates` first, register it in
> `core/adapters/__init__.py`, and stop before the write methods so I can
> check the reads with `make doctor`.

**The five steps.** (1) Copy the closest existing adapter -
`core/adapters/pms_csv.py` for a PMS, `email_imap.py` for a mailbox. (2)
Implement `ping()` (never raises; returns `HealthCheck(ok=False, ...)` with a
fix hint) and `capabilities()` (the method names that actually work) first -
`make doctor` reads both. (3) Implement the reads, mapping the vendor's
fields onto `Reservation` / `Guest` / `RateRow` / `EmailMessage`; put anything
unmapped into `.extra` rather than dropping it. (4) Implement the writes,
each wrapped in `@guarded_write("pms_write")` or `@guarded_write("send_email")`
- not optional, it's the whole safety model. (5) Register it - one line in
`core/adapters/__init__.py`'s `REGISTRY`, then `systems.pms.adapter:
yoursystem` in `config/hotel.yaml` and `make doctor` to check it.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change something
in `core/`, keep it generic - a hotel-specific tweak belongs in `tools/` or in
your own adapter file, not in the shared runtime.
