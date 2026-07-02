"""
Sends mail via Microsoft Graph using app-only (client credentials) auth -
same pattern as the wbs-invoice-monitor project.

Required env vars (GitHub secrets):
  AZURE_CLIENT_ID
  AZURE_TENANT_ID
  AZURE_CLIENT_SECRET
  MAIL_SENDER          - mailbox the app sends as, e.g. accounts@wbspraying.co.nz
"""
import os
import requests

TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
SENDER = os.environ.get("MAIL_SENDER", "accounts@wbspraying.co.nz")

TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_SEND_URL = f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail"


def _get_token() -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_mail(to_address: str, subject: str, html_body: str):
    token = _get_token()
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": "true",
    }
    resp = requests.post(
        GRAPH_SEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
