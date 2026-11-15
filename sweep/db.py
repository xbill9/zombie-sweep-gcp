"""Connect to the AlloyDB Express instance through the AlloyDB Python connector."""

import os
import subprocess

from google.cloud.alloydbconnector import Connector, IPTypes

PROJECT = os.environ.get("PROJECT", "comglitn")
REGION = os.environ.get("REGION", "us-west1")
CLUSTER = os.environ.get("CLUSTER", "zombie-sweep")
INSTANCE = os.environ.get("INSTANCE", "zombie-sweep-primary")
URI = f"projects/{PROJECT}/locations/{REGION}/clusters/{CLUSTER}/instances/{INSTANCE}"


def _password() -> str:
    if "DB_PASSWORD" in os.environ:
        return os.environ["DB_PASSWORD"]
    return subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         "--secret=zombie-sweep-db-password", f"--project={PROJECT}"],
        capture_output=True, text=True, check=True,
    ).stdout


def local_credentials():
    """On a workstation, borrow gcloud's token; in Cloud Run, return None (ADC)."""
    if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"):
        return None
    from google.oauth2.credentials import Credentials
    token = subprocess.run(["gcloud", "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    import datetime
    expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=55)  # naive UTC, as google-auth expects
    return Credentials(token=token, expiry=expiry, quota_project_id=PROJECT)


def connect(connector: Connector):
    return connector.connect(
        URI, "pg8000", user="postgres", password=_password(), db="postgres",
        ip_type=IPTypes.PUBLIC,
    )
