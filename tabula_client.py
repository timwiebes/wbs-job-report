"""
Headless client for Tabula (app.tabula-online.com).

Confirmed endpoints (from prior WBS extension work + live network capture):
- GET  /login?next=%2F                -> login page, contains a csrf_token hidden field
- POST /login?next=%2F                -> logs in, sets session cookie
      Form fields (confirmed via DevTools): csrf_token, login (email address,
      NOT "email"), password, submit="Log In", next="/". A "metrics" field
      (JSON blob of screen/browser info) is also sent by the real browser but
      appears to be telemetry, not required for auth - omitted here; add back
      if login starts failing.
- GET  /scheduling_data?tm_ajax_request=true&tm_request_time={ms}
      -> JSON array of jobs. Fields used: order_id, customer_name, products,
         area, end_date, is_history (True == "Returned")
- GET  /jobs/order_infos/{order_id}?tm_ajax_request=true&tm_request_time={ms}
      -> JSON array, [0] is the full job detail record.
"""
import os
import re
import time
import requests

BASE_URL = os.environ.get("TABULA_BASE_URL", "https://app.tabula-online.com")

AJAX_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
}


class TabulaClient:
    def __init__(self, email: str, password: str, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (WBS-JobReport-Bot)",
        })
        self._login(email, password)

    def _login(self, email: str, password: str):
        login_url = f"{self.base_url}/login"
        r = self.session.get(login_url, params={"next": "/"}, timeout=30)
        r.raise_for_status()

        token_match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"', r.text
        )
        if not token_match:
            token_match = re.search(
                r'value="([^"]+)"\s+name="csrf_token"', r.text
            )
        if not token_match:
            raise RuntimeError(
                "Could not find csrf_token on Tabula login page. "
                "Login page markup may have changed."
            )
        token = token_match.group(1)

        payload = {
            "csrf_token": token,
            "next": "/",
            "login": email,
            "password": password,
            "submit": "Log In",
        }
        r2 = self.session.post(
            login_url, params={"next": "/"}, data=payload, timeout=30,
            allow_redirects=True,
        )
        r2.raise_for_status()

        # If login failed, Tabula re-renders the login form instead of
        # redirecting through to the app - the password field will still
        # be present on the page we land on.
        if 'name="password"' in r2.text and "/login" in r2.url:
            raise RuntimeError(
                "Tabula login appears to have failed - check TABULA_EMAIL / "
                "TABULA_PASSWORD secrets."
            )

    def _ajax_get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        params["tm_ajax_request"] = "true"
        params["tm_request_time"] = str(int(time.time() * 1000))
        url = f"{self.base_url}{path}"
        r = self.session.get(url, params=params, headers=AJAX_HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_all_jobs(self):
        """Raw job list from the scheduling grid."""
        return self._ajax_get("/scheduling_data")

    def get_returned_jobs(self, since_days: int = 2):
        """Jobs marked is_history == True (Returned) with end_date within the window."""
        jobs = self.get_all_jobs()
        cutoff = time.time() - since_days * 86400
        out = []
        for j in jobs:
            if not j.get("is_history"):
                continue
            end_date = j.get("end_date")
            if not end_date:
                continue
            try:
                # Tabula end_date format confirmed elsewhere as parseable by JS Date();
                # try a couple of common formats defensively.
                import datetime
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
                    try:
                        dt = datetime.datetime.strptime(end_date[:len(fmt.replace('%',''))+10], fmt)
                        break
                    except ValueError:
                        dt = None
                if dt is None:
                    # fall back: don't filter out on unparseable date, let it through
                    out.append(j)
                    continue
                if dt.timestamp() >= cutoff:
                    out.append(j)
            except Exception:
                out.append(j)
        return out

    def get_order_info(self, order_id):
        data = self._ajax_get(f"/jobs/order_infos/{order_id}")
        if isinstance(data, list) and data:
            return data[0]
        return data
