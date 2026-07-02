"""
WBS Job Report
==============
Daily-scheduled: finds newly Returned Tabula jobs and emails a summary to
paul@wbspraying.co.nz. Never re-sends a job already reported (tracked in
data/seen_orders.json, committed back to the repo by the workflow).

Two modes, selected by env var:

  DEBUG_ORDER_ID=<id>   -> just fetch and print the raw order_infos JSON for
                            that one order (pretty-printed, all keys visible)
                            to the Actions log. No email sent, no state written.
                            Use this once to confirm field names, then unset it.

  (default)             -> normal run: find new Returned jobs in the last
                            SINCE_DAYS days, email a summary, update state.

Required env vars (GitHub secrets):
  TABULA_EMAIL, TABULA_PASSWORD
  AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET
Optional:
  MAIL_SENDER   (default accounts@wbspraying.co.nz)
  MAIL_TO       (default paul@wbspraying.co.nz)
  SINCE_DAYS    (default 2 - overlap window, de-duped by seen_orders.json)
"""
import json
import os
import sys
from pathlib import Path

from tabula_client import TabulaClient
from graph_mailer import send_mail

STATE_PATH = Path(__file__).parent / "data" / "seen_orders.json"

# --- candidate key resolution -------------------------------------------------
# Confirmed keys go first. Once DEBUG_ORDER_ID output confirms the real names
# for Notes / Comments / TJET ha / product Total, trim these lists down.

KEY_CANDIDATES = {
    "orchard": ["customer_name", "Customer", "customer", "CustomerName", "Address"],
    "kpin": ["KPIN", "kpin"],
    "notes": ["Notes", "notes"],
    "comments": ["Comments", "comments"],
    "requested_area": ["requested_area", "RequestedArea", "area", "gross_coverage_area"],
    "l_per_ha": ["water_rate", "WaterRate"],
    "litres": ["Litres", "litres"],
    "tjet_ha": ["TJET", "TjetHa", "tjet_ha", "TJETha", "TJET_ha"],
    "date": ["end_date", "EndDate", "due_date", "DueDate"],
}


def first_present(d: dict, keys: list, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def extract_products(order_info: dict, area) -> list:
    """Returns list of {name, rate_per_ha, total}."""
    raw = order_info.get("order_products_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    raw = raw or []

    products = []
    try:
        area_f = float(area) if area not in (None, "") else None
    except (TypeError, ValueError):
        area_f = None

    for p in raw:
        name = p.get("product_label", "") or p.get("name", "")
        rate = p.get("rate", p.get("label_rate", ""))
        total = p.get("total")
        if total is None and area_f is not None:
            try:
                total = round(float(rate) * area_f, 2)
            except (TypeError, ValueError):
                total = ""
        products.append({"name": name, "rate": rate, "total": total})
    return products


def summarise_job(order_info: dict, scheduling_entry: dict) -> dict:
    merged = {**order_info}  # order_info takes priority for detail fields
    notes = first_present(merged, KEY_CANDIDATES["notes"])
    comments = first_present(merged, KEY_CANDIDATES["comments"])
    combined_notes = " | ".join(x for x in [notes, comments] if x)

    area = first_present(merged, KEY_CANDIDATES["requested_area"])

    return {
        "order_id": scheduling_entry.get("order_id") or merged.get("order_id"),
        "orchard": first_present(scheduling_entry, KEY_CANDIDATES["orchard"])
        or first_present(merged, KEY_CANDIDATES["orchard"]),
        "kpin": first_present(merged, KEY_CANDIDATES["kpin"]),
        "notes": combined_notes,
        "requested_area": area,
        "l_per_ha": first_present(merged, KEY_CANDIDATES["l_per_ha"]),
        "litres": first_present(merged, KEY_CANDIDATES["litres"]),
        "tjet_ha": first_present(merged, KEY_CANDIDATES["tjet_ha"]),
        "date": first_present(scheduling_entry, KEY_CANDIDATES["date"])
        or first_present(merged, KEY_CANDIDATES["date"]),
        "products": extract_products(merged, area),
    }


def load_seen() -> set:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def save_seen(seen: set):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(seen), indent=2))


def render_email(jobs: list) -> str:
    rows = []
    for j in jobs:
        product_lines = "".join(
            f"<li>{p['name']} — {p['rate']} /ha, total {p['total']}</li>"
            for p in j["products"]
        ) or "<li>(no products recorded)</li>"

        rows.append(f"""
        <div style="margin-bottom:24px;padding:16px;border:1px solid #ddd;border-radius:6px;">
          <h3 style="margin:0 0 8px 0;">{j['orchard'] or '(unknown orchard)'} — KPIN {j['kpin']}</h3>
          <table style="font-size:14px;">
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Date</td><td>{j['date']}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Notes</td><td>{j['notes'] or '-'}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Requested area</td><td>{j['requested_area']} ha</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">L/ha</td><td>{j['l_per_ha']}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Litres used</td><td>{j['litres']}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">TJET ha</td><td>{j['tjet_ha']}</td></tr>
          </table>
          <p style="margin:8px 0 4px 0;color:#555;">Products</p>
          <ul style="margin:0;">{product_lines}</ul>
        </div>""")

    return f"""
    <div style="font-family:Arial,sans-serif;color:#222;">
      <p>{len(jobs)} newly returned job{'s' if len(jobs) != 1 else ''}:</p>
      {''.join(rows)}
    </div>"""


def main():
    email = os.environ["TABULA_EMAIL"]
    password = os.environ["TABULA_PASSWORD"]
    mail_to = os.environ.get("MAIL_TO", "paul@wbspraying.co.nz")
    since_days = int(os.environ.get("SINCE_DAYS", "2"))

    client = TabulaClient(email, password)

    debug_order_id = os.environ.get("DEBUG_ORDER_ID")
    if debug_order_id:
        info = client.get_order_info(debug_order_id)
        print(f"--- order_infos keys for order {debug_order_id} ---")
        print(json.dumps(info, indent=2, default=str))
        sys.exit(0)

    seen = load_seen()
    returned = client.get_returned_jobs(since_days=since_days)
    new_jobs = [j for j in returned if str(j.get("order_id")) not in seen]

    if not new_jobs:
        print("No new returned jobs. Nothing to send.")
        return

    summaries = []
    for j in new_jobs:
        order_id = j.get("order_id")
        info = client.get_order_info(order_id)
        summaries.append(summarise_job(info, j))

    html = render_email(summaries)
    send_mail(mail_to, f"WBS: {len(summaries)} job(s) returned", html)
    print(f"Emailed {len(summaries)} new job(s) to {mail_to}.")

    seen.update(str(j.get("order_id")) for j in new_jobs)
    save_seen(seen)


if __name__ == "__main__":
    main()
