# WBS Job Report

Daily email to paul@wbspraying.co.nz summarising newly Returned Tabula jobs.
Never repeats a job — `data/seen_orders.json` tracks what's already been sent.

## Setup

1. Create a new **private** GitHub repo, upload these files (use the web
   editor, same as wbs-invoice-monitor, so the `.github/workflows` folder
   isn't dropped by drag-and-drop on macOS).

2. Add repo secrets (Settings → Secrets and variables → Actions):
   - `TABULA_EMAIL` / `TABULA_PASSWORD` — Tabula login used to read job data.
   - `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` — reuse the
     same values already used for wbs-invoice-monitor (same Azure app can
     send mail as accounts@wbspraying.co.nz — no new Azure app needed unless
     you want to isolate permissions).

3. **Field verification (do this before relying on the daily run):**
   - Go to Actions → WBS Job Report → Run workflow.
   - Fill in `debug_order_id` with a known recently-returned order id (e.g.
     `4372561`).
   - Run it, then open the run log — it prints every field name and value
     Tabula actually returns for that job.
   - Paste that log output back so I can lock in the exact key names for
     Notes, Comments, TJET ha and product Totals (`KEY_CANDIDATES` in
     `job_report.py` currently guesses several possible names per field —
     harmless if wrong, just means that field prints blank in the email
     until confirmed).

4. Once fields are confirmed, leave it on the daily schedule (7-8am NZT).
   Trigger a manual "Run workflow" (leave `debug_order_id` blank) any time
   to test a real send.

## Notes

- `SINCE_DAYS` (in the workflow env) is a lookback window, not a resend
  window — de-duplication is handled entirely by `seen_orders.json`, so it's
  safe to leave it a bit generous (default 2 days) as a buffer against a
  missed run.
- Mirrors the wbs-invoice-monitor architecture exactly: same secrets
  pattern, same "commit state back to repo" approach, same Graph API mail
  sending.
