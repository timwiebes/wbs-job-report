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

  TEST_ORDER_IDS=<ids>  -> comma-separated order_ids to force-email right now,
                            regardless of whether they're already in
                            seen_orders.json. Does NOT update seen_orders.json -
                            safe to re-run repeatedly while checking formatting.

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

KEY_CANDIDATES = {
    "orchard": ["customer_full_name", "customer_name", "Customer", "customer", "Address"],
    "kpin": ["KPIN", "kpin"],
    "notes": ["notes", "Notes"],
    "comments": ["comments", "Comments"],
    "requested_area": ["gross_coverage_area", "requested_area", "area"],
    "l_per_ha": ["water_rate", "WaterRate"],
    "tjet_ha": ["TJET ha", "TJET", "tjet_ha"],
}


def round_num(value, places=1) -> str:
    """Round anything numeric-looking to `places` decimals; pass through otherwise."""
    if value in (None, ""):
        return ""
    try:
        return f"{round(float(value), places):.{places}f}"
    except (TypeError, ValueError):
        return to_text(value)


def first_present(d: dict, keys: list, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def extract_products(order_info: dict) -> list:
    """Returns list of {name, rate, unit, total, total_unit}.

    Confirmed structure per product (from live order_products_json dump):
      rate                          -> the /ha application rate (e.g. 700)
      weight_unit                   -> unit for rate (e.g. "g", "ml")
      requested_total                -> the job's target total product use -
                                     matches the "Total" column on the Tabula
                                     job sheet PDF exactly (NOT "total", which
                                     is a separate in-progress applied figure)
      requested_total_weight_units  -> unit for requested_total
    """
    raw = order_info.get("order_products_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    raw = raw or []

    products = []
    for p in raw:
        name = to_text(p.get("product_label", ""))
        rate = p.get("rate", "")
        unit = to_text(p.get("weight_unit", ""))
        total = p.get("requested_total")
        total_unit = to_text(p.get("requested_total_weight_units", "")) or unit
        products.append({
            "name": name,
            "rate": round_num(rate),
            "unit": unit,
            "total": round_num(total),
            "total_unit": total_unit,
        })
    return products


def to_text(value) -> str:
    """Coerce whatever Tabula returns (str, list, dict, None) into text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " / ".join(to_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def format_date(value) -> str:
    """Handle both epoch (order_info) and pre-formatted (scheduling_entry) dates."""
    if value in (None, ""):
        return ""
    try:
        num = float(value)
        if num > 10_000_000:
            import datetime
            return datetime.datetime.utcfromtimestamp(num).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        pass
    return to_text(value)


def summarise_job(order_info: dict, scheduling_entry: dict) -> dict:
    merged = {**order_info}
    notes = to_text(first_present(merged, KEY_CANDIDATES["notes"]))
    comments = to_text(first_present(merged, KEY_CANDIDATES["comments"]))
    combined_notes = " | ".join(x for x in [notes, comments] if x)

    raw_area = first_present(merged, KEY_CANDIDATES["requested_area"])
    area = round_num(raw_area, places=2)

    water_rate = merged.get("water_rate")
    litres = ""
    try:
        if water_rate not in (None, "") and raw_area not in (None, ""):
            litres = round_num(float(water_rate) * float(raw_area))
    except (TypeError, ValueError):
        litres = ""

    date = to_text(scheduling_entry.get("end_date")) or format_date(
        merged.get("due_date") or merged.get("job_last_returned")
    )

    return {
        "order_id": scheduling_entry.get("order_id") or merged.get("order_id"),
        "orchard": to_text(
            first_present(merged, KEY_CANDIDATES["orchard"])
            or first_present(scheduling_entry, KEY_CANDIDATES["orchard"])
        ),
        "kpin": to_text(first_present(merged, KEY_CANDIDATES["kpin"])),
        "notes": combined_notes,
        "requested_area": area,
        "l_per_ha": round_num(first_present(merged, KEY_CANDIDATES["l_per_ha"])),
        "litres": litres,
        "tjet_ha": round_num(first_present(merged, KEY_CANDIDATES["tjet_ha"]), places=2),
        "date": date,
        "products": extract_products(merged),
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
            f"<li>{p['name']} — {p['rate']}{(' ' + p['unit']) if p['unit'] else ''}/ha, "
            f"total {p['total']}{(' ' + p['total_unit']) if p['total_unit'] else ''}</li>"
            for p in j["products"]
        ) or "<li>(no products recorded)</li>"

        try:
            high_rate = float(j["l_per_ha"]) >= 1500
        except (TypeError, ValueError):
            high_rate = False
        l_per_ha_style = "color:#c0392b;font-weight:bold;" if high_rate else ""

        rows.append(f"""
        <div style="margin-bottom:24px;padding:16px;border:1px solid #ddd;border-radius:6px;">
          <h3 style="margin:0 0 8px 0;">{j['orchard'] or '(unknown orchard)'} — KPIN {j['kpin']}</h3>
          <table style="font-size:14px;">
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Date</td><td>{j['date']}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Notes</td><td>{j['notes'] or '-'}</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">Requested area</td><td>{j['requested_area']} ha</td></tr>
            <tr><td style="padding:2px 12px 2px 0;color:#555;">L/ha</td><td style="{l_per_ha_style}">{j['l_per_ha']}</td></tr>
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

    test_order_ids = os.environ.get("TEST_ORDER_IDS", "").strip()
    if test_order_ids:
        ids = [x.strip() for x in test_order_ids.split(",") if x.strip()]
        all_jobs = {str(j.get("order_id")): j for j in client.get_all_jobs()}
        summaries = []
        for oid in ids:
            scheduling_entry = all_jobs.get(oid, {"order_id": oid})
            info = client.get_order_info(oid)
            summaries.append(summarise_job(info, scheduling_entry))
        html = render_email(summaries)
        send_mail(mail_to, f"WBS TEST: {len(summaries)} job(s)", html)
        print(f"[TEST] Emailed {len(summaries)} job(s) to {mail_to}. seen_orders.json NOT updated.")
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
