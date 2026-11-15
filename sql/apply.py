#!/usr/bin/env python3
"""Apply sql/schema.sql to the AlloyDB Express instance."""
import pathlib, re, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "sweep"))
from google.cloud.alloydbconnector import Connector
import db

def statements(sql):
    out, buf = [], ""
    for part in re.split(r"(;\s*\n)", sql):
        buf += part
        if part.strip() == ";" and buf.count("$$") % 2 == 0:
            s = re.sub(r"--[^\n]*", "", buf).strip().rstrip(";").strip()
            if s: out.append(s)
            buf = ""
    return out

t = time.monotonic()
with Connector(credentials=db.local_credentials()) as c:
    conn = db.connect(c); print(f"connected in {time.monotonic()-t:.1f}s")
    cur = conn.cursor()
    cur.execute("select version()"); print(cur.fetchone()[0][:70])
    for name in ("schema.sql", "views.sql"):
      for s in statements((pathlib.Path(__file__).parent / name).read_text()):
        cur.execute(s)
    conn.commit()
    cur.execute("select table_name from information_schema.tables where table_schema='public' order by 1")
    print("tables", [r[0] for r in cur.fetchall()])
    cur.execute("show max_connections"); print("max_connections", cur.fetchone()[0])
    conn.close()
