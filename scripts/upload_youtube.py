#!/usr/bin/env python3
"""Upload the demo to YouTube as a public video.

usage: upload_youtube.py <client_secret.json>

Works with a "web" OAuth client whose redirect URI is
http://localhost:8765/callback: a one-shot local server catches the code.
"""

import http.server
import pathlib
import sys
import threading
import urllib.parse
import webbrowser

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = pathlib.Path(__file__).resolve().parent.parent
REDIRECT = "http://localhost:8765/callback"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
code = {}


class Callback(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.urlparse(self.path)
        if q.path == "/callback":
            code.update(urllib.parse.parse_qs(q.query))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorised. You can close this tab.")
            threading.Thread(target=self.server.shutdown).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


flow = Flow.from_client_secrets_file(sys.argv[1], scopes=SCOPES, redirect_uri=REDIRECT)
url, _ = flow.authorization_url(prompt="consent", access_type="offline")
server = http.server.HTTPServer(("localhost", 8765), Callback)
print("AUTH_URL", url, flush=True)
webbrowser.open(url)
server.serve_forever()
if "code" not in code:
    sys.exit(f"no code returned: {code}")
flow.fetch_token(code=code["code"][0])

yt = build("youtube", "v3", credentials=flow.credentials)
body = dict(
    snippet=dict(
        title="Zombie Sweep on Google Cloud: a daily waste report on AlloyDB Express, with an agent",
        description=(
            "A real sweep, start to finish. Cloud Scheduler starts a workflow once a day; Workflows runs "
            "zombiescan-gcp's 20 checks and 7 of Recommender's idle recommenders as two parallel Cloud Run "
            "job executions, and stores every priced finding in AlloyDB Express. An ADK agent on Gemini "
            "reads the run through MCP Toolbox for Databases and writes a daily briefing; every count and "
            "sum comes from a SQL tool. The report page sits behind IAP.\n\n"
            "AlloyDB Express scales to zero between sweeps and caps storage at 20 GB, so the database bill "
            "has a ceiling you can compute in advance.\n\n"
            "Article: https://dev.to/gde/zombie-sweep-on-google-cloud-a-daily-waste-report-on-alloydb-express-with-a-database-bill-you-can-305g\n"
            "Code: https://github.com/xbill9/zombie-sweep-gcp\n\n"
            "Costs are estimates from Google Cloud list prices, not a bill. The sweep is read-only.\n\n"
            "#AlloyDBSprint"
        ),
        categoryId="28",
        tags=["Google Cloud", "AlloyDB", "AlloyDB Express", "PostgreSQL", "Cloud Run", "Workflows",
              "ADK", "MCP Toolbox", "FinOps", "AlloyDBSprint"],
    ),
    status=dict(privacyStatus="public", selfDeclaredMadeForKids=False),
)
media = MediaFileUpload(str(ROOT / "media/zombie-sweep-gcp-demo.mp4"), resumable=True)
r = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
s = yt.videos().list(part="status", id=r["id"]).execute()["items"][0]["status"]
print("VIDEO", f"https://youtu.be/{r['id']}", s, flush=True)
