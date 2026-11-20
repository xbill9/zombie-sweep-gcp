#!/usr/bin/env python3
"""Record the demo video in two parts, then join them.

    python3 scripts/record_demo.py sweep            # title, architecture, a live sweep -> media/part1.mp4, media/run.json
    python3 scripts/record_demo.py report SHOTS     # report-page screenshots + cost -> media/part2.mp4, then the join

The report page sits behind IAP (Google sign-in), so headless Chrome cannot open
it; the screenshots for part 2 are taken in a signed-in browser after part 1.
Every number on screen comes from the sweep this script starts, from SQL on that
run, or from docs/evidence. Output: media/zombie-sweep-gcp-demo.mp4 (1280x720).
"""

import base64
import datetime as dt
import html
import json
import pathlib
import subprocess
import sys
import threading
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEDIA = ROOT / "media"
PROJECT, REGION, WF, JOB = "comglitn", "us-west1", "zombie-sweep", "zombie-sweep"
G = ["gcloud", f"--project={PROJECT}", "--quiet"]
sys.path.insert(0, str(ROOT / "sweep"))

CSS = """
:root{--bg:#141412;--fg:#efede6;--mute:#a19f97;--acc:#f08a5d;--blue:#5b9be8;--ok:#5fbf77;--line:#34322c}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:20px/1.4 system-ui,sans-serif;height:720px;overflow:hidden}
.card{display:flex;flex-direction:column;justify-content:center;height:720px;padding:0 90px}
h1{font-size:60px;margin:0 0 12px} h2{font-size:38px;margin:0 0 18px} .mute{color:var(--mute)}
.big{font-size:29px;line-height:1.5} .acc{color:var(--acc)} .blue{color:var(--blue)}
#cap{position:fixed;left:50%;transform:translateX(-50%);bottom:28px;max-width:1080px;background:#000d;color:#fff;
 font:600 24px/1.35 system-ui;padding:12px 22px;border-radius:10px;z-index:9;text-align:center}
#cap:empty{display:none}
.flow{display:flex;gap:14px;align-items:stretch;margin-top:26px}
.st{flex:1;border:1px solid var(--line);border-radius:10px;padding:14px;min-height:150px}
.st b{display:block;font-size:19px;margin-bottom:6px} .st span{display:block;color:var(--mute);font-size:15px}
.st .v{color:var(--fg);font-size:17px;margin-top:10px}
.st.run{border-color:var(--acc)} .st.done{border-color:var(--ok);background:#1d2a20}
.top{display:flex;justify-content:space-between;align-items:baseline}
#clock{font:600 40px ui-monospace,monospace;color:var(--acc)}
.arch{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin-top:20px}
.box{border:1px solid var(--line);border-radius:10px;padding:16px;font-size:18px}
.box b{display:block;font-size:20px;margin-bottom:4px} .box.db{border-color:var(--blue)}
.brief{white-space:pre-wrap;font:18px/1.5 ui-monospace,monospace;background:#1c1b18;border:1px solid var(--line);
 border-radius:10px;padding:18px 22px;margin-top:10px}
.shot{height:720px;display:flex;align-items:center;justify-content:center;background:#0d0d0c}
.shot img{max-width:1280px;max-height:720px}
"""


def page_html(body):
    return f"<!doctype html><meta charset=utf-8><style>{CSS}</style><body>{body}<div id=cap></div></body>"


def cap(p, text):
    p.evaluate("t=>{document.getElementById('cap').textContent=t}", text)


def set_text(p, el_id, text):
    p.evaluate("([i,t])=>{document.getElementById(i).textContent=t}", [el_id, text])


def gjson(*args):
    return json.loads(subprocess.run(G + list(args) + ["--format=json"], capture_output=True, text=True, check=True).stdout)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class Watch:
    """Start a sweep; track the workflow's current step and the job executions it starts."""

    def __init__(self):
        self.run_id = None
        self.started = None
        self.step = "starting"
        self.jobs = {}  # mode -> {"state": ..., "seconds": ...}
        self.state = "ACTIVE"
        self.seconds = None
        self.wf_start = None  # the workflow's own start time, for the on-screen clock

    error = None

    def run(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001 -- reported by the caller
            self.error = repr(e)

    def _run(self):
        self.started = time.monotonic()
        t0 = dt.datetime.now(dt.UTC)
        out = subprocess.run(G + ["workflows", "execute", WF, f"--location={REGION}", "--format=value(name)"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.run_id = out.rsplit("/", 1)[-1]
        while True:
            d = gjson("workflows", "executions", "describe", self.run_id, f"--workflow={WF}", f"--location={REGION}")
            if d.get("startTime"):
                self.wf_start = ts(d["startTime"])
            steps = (d.get("status") or {}).get("currentSteps") or []
            if steps:
                self.step = steps[-1]["step"]
            for e in gjson("run", "jobs", "executions", "list", f"--job={JOB}", f"--region={REGION}", "--limit=6"):
                if ts(e["metadata"]["creationTimestamp"]) < t0:
                    continue
                s = json.dumps(e)
                mode = next((m for m in ("zombiescan", "recommender", "finish") if f'"value": "{m}"' in s), None)
                if not mode:
                    continue
                done = e["status"].get("completionTime")
                self.jobs[mode] = {"state": "done" if done else "run",
                                   "seconds": (ts(done) - ts(e["status"]["startTime"])).total_seconds() if done else None}
            if d["state"] != "ACTIVE":
                self.state = d["state"]
                self.seconds = (ts(d["endTime"]) - ts(d["startTime"])).total_seconds()
                return
            time.sleep(2)


def run_figures(run_id):
    """Counts and sums for the run, computed by PostgreSQL."""
    import db
    from google.cloud.alloydbconnector import Connector
    with Connector(credentials=db.local_credentials()) as c:
        cur = db.connect(c).cursor()
        cur.execute("SELECT source, count(*), round(sum(monthly_cost),2) FROM findings WHERE run_id=%s GROUP BY 1 ORDER BY 1", (run_id,))
        by_source = {s: (n, float(m)) for s, n, m in cur.fetchall()}
        cur.execute("SELECT count(*), round(coalesce(sum(monthly_cost),0),2) FROM findings WHERE run_id=%s", (run_id,))
        total = cur.fetchone()
        cur.execute("SELECT count(DISTINCT (kind, name)) FROM findings_compared WHERE run_id=%s AND found_by_both", (run_id,))
        both = cur.fetchone()[0]
        cur.execute("SELECT briefing, status FROM runs WHERE run_id=%s", (run_id,))
        briefing, status = cur.fetchone()
    return {"by_source": by_source, "findings": total[0], "monthly": float(total[1]), "both": both,
            "briefing": briefing or "", "status": status}


def encode(video, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-r", "25", "-crf", "20", "-movflags", "+faststart", str(out)], check=True)
    pathlib.Path(video).unlink()


def browser(w):
    b = w.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport=dict(width=1280, height=720), record_video_dir=str(MEDIA / "raw"),
                        record_video_size=dict(width=1280, height=720))
    return b, ctx, ctx.new_page()


def part_sweep():
    MEDIA.mkdir(exist_ok=True)
    with sync_playwright() as w:
        b, ctx, p = browser(w)

        # 1. Title
        p.set_content(page_html(
            "<div class=card><h1>Zombie Sweep on Google Cloud</h1>"
            "<div class='big mute'>A daily sweep for resources nobody is using, stored in AlloyDB Express,<br>"
            "with an agent that writes the briefing from SQL.</div></div>"))
        p.wait_for_timeout(4500)
        cap(p, "Google Cloud already ships idle-resource recommendations: Recommender.")
        p.wait_for_timeout(4500)
        cap(p, "This sweep runs 7 of its idle recommenders beside the 20 checks of zombiescan-gcp, an open-source scanner.")
        p.wait_for_timeout(6000)

        # 2. Architecture
        p.set_content(page_html(
            "<div style='padding:60px 70px'><h2>How it fits together</h2><div class=arch>"
            "<div class=box><b>Cloud Scheduler</b>06:00 UTC, daily</div>"
            "<div class=box><b>Workflows</b>both sources in parallel, then the briefing</div>"
            "<div class=box><b>Cloud Run job</b>zombiescan · recommender · finish</div>"
            "<div class='box db'><b class=blue>AlloyDB Express</b>runs, findings, two comparison views</div>"
            "<div class=box style='grid-column:2/4'><b>Cloud Run service behind IAP</b>ADK agent on Gemini + "
            "MCP Toolbox for Databases sidecar: 8 read-only SQL tools</div>"
            "<div class='box db'><b class=blue>scales to zero</b>when idle; storage capped at 20 GB</div>"
            "</div></div>"))
        cap(p, "Cloud Scheduler starts a workflow once a day.")
        p.wait_for_timeout(4500)
        cap(p, "Workflows runs one Cloud Run job image twice in parallel, once per source, and stores every finding in AlloyDB Express.")
        p.wait_for_timeout(7000)
        cap(p, "An ADK agent reads the run through MCP Toolbox's SQL tools and writes a briefing. Every number in it comes from SQL.")
        p.wait_for_timeout(7000)

        # 3. A live sweep
        stages = [("wf", "Workflows", "execution"), ("zombiescan", "zombiescan job", "20 checks"),
                  ("recommender", "recommender job", "7 idle recommenders"),
                  ("brief", "agent briefing", "ADK + Toolbox, through IAP"), ("finish", "finish job", "stores the briefing")]
        tiles = "".join(f"<div class=st id='s-{k}'><b>{t}</b><span>{s}</span><div class=v id='v-{k}'>waiting</div></div>"
                        for k, t, s in stages)
        p.set_content(page_html(
            "<div style='padding:50px 60px'><div class=top><h2>Live sweep</h2><div id=clock>0 s</div></div>"
            f"<div class=mute id=exec>starting…</div><div class=flow>{tiles}</div></div>"))
        cap(p, "Starting a real sweep now.")
        watch = Watch()
        t = threading.Thread(target=watch.run)
        t.start()
        while t.is_alive():
            if watch.wf_start:
                set_text(p, "clock", f"{(dt.datetime.now(dt.UTC) - watch.wf_start).total_seconds():.0f} s")
            if watch.run_id:
                set_text(p, "exec", "workflow execution " + watch.run_id)
                p.evaluate("()=>{document.getElementById('s-wf').className='st run'}")
                set_text(p, "v-wf", "running")
            for mode, j in list(watch.jobs.items()):
                set_text(p, f"v-{mode}", f"done in {j['seconds']:.0f} s" if j["state"] == "done" else "running")
                p.evaluate("([m,s])=>{document.getElementById('s-'+m).className='st '+s}", [mode, j["state"]])
            sources_done = all(watch.jobs.get(m, {}).get("state") == "done" for m in ("zombiescan", "recommender"))
            if "finish" in watch.jobs:
                set_text(p, "v-brief", "written")
                p.evaluate("()=>{document.getElementById('s-brief').className='st done'}")
                cap(p, "The finish job stores the briefing in AlloyDB and closes the run.")
            elif sources_done:
                set_text(p, "v-brief", "writing")
                p.evaluate("()=>{document.getElementById('s-brief').className='st run'}")
                cap(p, "The agent is writing the briefing from Toolbox's SQL tools.")
            elif watch.run_id:
                cap(p, "zombiescan and Recommender run side by side, each as its own Cloud Run job execution.")
            p.wait_for_timeout(500)
        t.join()
        if watch.seconds is None:
            raise SystemExit(f"the sweep did not finish: {watch.error}")
        set_text(p, "clock", f"{watch.seconds:.0f} s")
        set_text(p, "v-wf", watch.state)
        p.evaluate("()=>{document.getElementById('s-wf').className='st done'}")
        cap(p, "Counting this run's findings in AlloyDB, with SQL.")
        f = run_figures(watch.run_id)
        zs, rc = f["by_source"].get("zombiescan", (0, 0.0)), f["by_source"].get("recommender", (0, 0.0))
        def n(k):
            return f"{k} finding" if k == 1 else f"{k} findings"
        cap(p, f"{watch.state} in {watch.seconds:.0f} seconds. zombiescan: {n(zs[0])}, ${zs[1]:.2f} a month. "
               f"Recommender: {n(rc[0])}, ${rc[1]:.2f} a month.")
        p.wait_for_timeout(7000)
        cap(p, f"{f['both']} resource found by both sources. The totals come from SQL on this run, not from the model."
            if f["both"] == 1 else
            f"{f['both']} resources found by both sources. The totals come from SQL on this run, not from the model.")
        p.wait_for_timeout(6500)

        # The briefing the agent stored for this run
        p.set_content(page_html(
            f"<div style='padding:50px 60px'><h2>The agent's briefing</h2><div class=mute>runs.briefing, run {watch.run_id}</div>"
            f"<div class=brief>{html.escape(f['briefing'])}</div></div>"))
        cap(p, "The briefing the agent stored for this run, written from the results of its SQL tools.")
        p.wait_for_timeout(9000)
        cap(p, "")
        p.wait_for_timeout(500)
        video = p.video.path()
        ctx.close()
        b.close()
    encode(video, MEDIA / "part1.mp4")
    (MEDIA / "run.json").write_text(json.dumps({"run_id": watch.run_id, "state": watch.state, "seconds": watch.seconds, **f}))
    print("run", watch.run_id, watch.state, f"{watch.seconds:.0f}s", f["findings"], f["monthly"])
    print("wrote", MEDIA / "part1.mp4")


def part_report(shots):
    shots = pathlib.Path(shots)
    run = json.loads((MEDIA / "run.json").read_text())
    cost = (ROOT / "docs/evidence/cost-2026-11-20.txt").read_text()
    total = next(ln for ln in cost.splitlines() if "total, upper end" in ln).split(":", 1)[1].strip()
    ceiling = next(ln for ln in cost.splitlines() if ln.strip().startswith("1 CU:")).rsplit("ceiling", 1)[1].strip()
    captions = json.loads((shots / "captions.json").read_text())
    with sync_playwright() as w:
        b, ctx, p = browser(w)
        for name, text, ms in captions:
            # set_content pages are about:blank, which may not load file:// images
            uri = "data:image/jpeg;base64," + base64.b64encode((shots / name).read_bytes()).decode()
            p.set_content(page_html(f"<div class=shot><img src='{uri}'></div>"))
            p.wait_for_timeout(300)
            cap(p, text)
            p.wait_for_timeout(ms)
        p.set_content(page_html(
            "<div class=card style='padding-bottom:150px'><h2>What it costs</h2><div class=big>"
            f"<span class=acc>{html.escape(total)}</span><br>"
            "for 30 daily sweeps, priced from the Cloud Billing Catalog API and measured run times.<br><br>"
            f"The most AlloyDB Express can bill this 1 vCPU instance in a month: <span class=blue>{html.escape(ceiling)}</span>,<br>"
            "with it never idle and storage full at 20 GB.<br><br>"
            "<span class=mute>Read-only scanner. Deletes nothing.  github.com/xbill9/zombie-sweep-gcp</span></div></div>"))
        cap(p, "AlloyDB Express scales to zero between sweeps and caps its own storage, so the database bill has a ceiling.")
        p.wait_for_timeout(8000)
        cap(p, "deploy/deploy.sh sets up the whole thing, and runs again safely.")
        p.wait_for_timeout(5000)
        cap(p, "")
        p.wait_for_timeout(1500)
        video = p.video.path()
        ctx.close()
        b.close()
    encode(video, MEDIA / "part2.mp4")
    lst = MEDIA / "parts.txt"
    lst.write_text("file 'part1.mp4'\nfile 'part2.mp4'\n")
    out = MEDIA / "zombie-sweep-gcp-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    lst.unlink()
    print("run", run["run_id"], "wrote", out)


if __name__ == "__main__":
    if sys.argv[1:2] == ["sweep"]:
        part_sweep()
    elif sys.argv[1:2] == ["report"] and len(sys.argv) == 3:
        part_report(sys.argv[2])
    else:
        sys.exit(__doc__)
