"""Zombie Sweep agent and report page.

One Cloud Run service, two containers: this app, and MCP Toolbox for Databases
on 127.0.0.1:5000 holding every SQL query (tools.yaml). The report page calls
the Toolbox tools directly; the agent calls the same tools through Gemini. So
the page and the agent quote the same numbers, and neither does arithmetic.

    GET  /           report page for the latest finished run, with an ask box
    POST /ask        {"question": "..."}  -> {"answer", "tool_calls"}
    POST /briefing   {"run_id": "..."}    -> {"briefing", "tool_calls"}  (called by Workflows)
"""

from __future__ import annotations

import asyncio
import html
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel
from toolbox_core import ToolboxClient

TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5000")
MODEL = os.environ.get("MODEL", "gemini-3.8-flash")
PROJECT = os.environ.get("PROJECT_TO_SCAN", "comglitn")

INSTRUCTION = f"""You answer questions about unused ("zombie") Google Cloud resources in
project {PROJECT}, found by a daily sweep. Two sources look at the project each run:
zombiescan (20 open-source checks) and Google's Recommender (7 idle-resource
recommenders). A resource "found by both" was flagged by each of them.

Rules:
- Every number you state comes from a tool result in this conversation, quoted
  exactly. Never add, count, average or compare numbers yourself. For any "how
  many" or "how much" question call count_findings with the filters the
  question asks for; "10 or more" means min_monthly_cost 10, which is inclusive.
- Say which run you answered from (its run_id and start time) and which filters
  you used.
- Monthly cost is dollars a month at list price. zombiescan prices a resource
  from the public price list; Recommender's figure is its own projected saving.
  When the two sources price the same resource differently, give both.
- Name resources exactly as the tool returns them.
- If the tools cannot answer the question, say so. Never guess.
- Plain text, short. No markdown tables.
"""

BRIEFING = """Write the daily briefing for run {run_id}. Call totals, top_waste (n 5),
only_one_source and diff_vs_previous, all with run_id {run_id}. Then write at most
five short lines of plain text: total findings and monthly cost per source; the
most expensive findings; what changed since the previous run (or that there is
no previous run); and what only one source found. Quote numbers exactly from the
tool results. No headings, no markdown."""

app = FastAPI()
_client: ToolboxClient | None = None
_tools: dict = {}
_lock = asyncio.Lock()


async def tools() -> dict:
    """Load the Toolbox tools once; retry while the sidecar starts."""
    global _client, _tools
    async with _lock:
        if not _tools:
            last = None
            for _ in range(30):
                try:
                    _client = ToolboxClient(TOOLBOX_URL)
                    _tools = {t.__name__: t for t in await _client.load_toolset()}
                    break
                except Exception as exc:  # sidecar not up yet
                    last = exc
                    await asyncio.sleep(1)
            if not _tools:
                raise RuntimeError(f"Toolbox not reachable at {TOOLBOX_URL}: {last}")
    return _tools


async def call(name: str, **kwargs) -> list[dict]:
    raw = await (await tools())[name](**kwargs)
    data = json.loads(raw) if raw else []
    return data or []


async def run_agent(prompt: str) -> tuple[str, list[dict]]:
    agent = LlmAgent(name="zombie_sweep", model=MODEL, instruction=INSTRUCTION,
                     tools=list((await tools()).values()))
    runner = InMemoryRunner(agent=agent, app_name="zombie_sweep")
    session = await runner.session_service.create_session(app_name="zombie_sweep", user_id="web")
    answer, calls = "", []
    async for event in runner.run_async(
            user_id="web", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)])):
        for fc in event.get_function_calls() or []:
            calls.append({"tool": fc.name, "args": dict(fc.args or {})})
        if event.is_final_response() and event.content and event.content.parts:
            answer = "".join(p.text or "" for p in event.content.parts).strip()
    return answer, calls


class Ask(BaseModel):
    question: str


class Brief(BaseModel):
    run_id: str


@app.post("/ask")
async def ask(body: Ask):
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    answer, calls = await run_agent(body.question[:2000])
    return {"answer": answer, "tool_calls": calls}


@app.post("/briefing")
async def briefing(body: Brief):
    text, calls = await run_agent(BRIEFING.format(run_id=body.run_id))
    return {"run_id": body.run_id, "briefing": text, "tool_calls": calls}


@app.get("/health")  # /healthz is reserved by Cloud Run
async def healthz():
    return {"tools": sorted(await tools())}


# ------------------------------------------------------------------ the page
def e(v) -> str:
    return html.escape("" if v is None else str(v))


def money(v) -> str:
    return "" if v is None else f"${float(v):,.2f}"


def table(rows: list[dict], cols: list[tuple[str, str]], num=()) -> str:
    if not rows:
        return '<p class="muted">None.</p>'
    head = "".join(f'<th class="{"n" if k in num else ""}">{e(t)}</th>' for k, t in cols)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="{"n" if k in num else ""}">'
            f'{money(r.get(k)) if k.endswith("monthly_cost") else e(r.get(k))}</td>'
            for k, _ in cols) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


@app.get("/", response_class=HTMLResponse)
async def page():
    runs, brief, tot, top, only, diff = await asyncio.gather(
        call("list_runs", n=10), call("run_briefing"), call("totals"),
        call("top_waste", n=15), call("only_one_source"), call("diff_vs_previous"))
    run = brief[0] if brief else {}
    per_source = [r for r in tot if r["check_id"] == "ALL"]
    by_check = [r for r in tot if r["check_id"] != "ALL"]
    summary = [r for r in diff if r["row_type"] == "summary"]
    changed = [r for r in diff if r["row_type"] == "finding"]
    tiles = "".join(
        f'<div class="tile"><div class="label">{e(r["source"])}</div>'
        f'<div class="big">{money(r["monthly_cost"])}<span>/mo</span></div>'
        f'<div class="muted">{e(r["findings"])} findings, {e(r["also_found_by_other_source"])} also found by the other source</div></div>'
        for r in per_source)
    return HTML.format(
        project=e(PROJECT), run_id=e(run.get("run_id", "no finished run yet")),
        started=e(str(run.get("started_at", ""))[:19].replace("T", " ")),
        briefing=e(run.get("briefing") or "No briefing stored for this run."),
        tiles=tiles,
        top=table(top, [("monthly_cost", "Monthly"), ("source", "Source"), ("check_id", "Check"),
                        ("kind", "Kind"), ("name", "Name"), ("found_by_both", "Both"), ("reason", "Why")],
                  num=("monthly_cost",)),
        only=table(only, [("found_only_by", "Found only by"), ("check_id", "Check"),
                          ("findings", "Findings"), ("monthly_cost", "Monthly")],
                   num=("findings", "monthly_cost")),
        diff=table(summary, [("change", "Change"), ("findings", "Findings"), ("monthly_cost", "Monthly"),
                             ("previous_run_id", "Compared with run")], num=("findings", "monthly_cost"))
        + (table(changed, [("change", "Change"), ("source", "Source"), ("check_id", "Check"),
                           ("resource", "Resource"), ("monthly_cost", "Monthly")], num=("monthly_cost",))
           if changed else ""),
        checks=table(by_check, [("source", "Source"), ("check_id", "Check"), ("findings", "Findings"),
                                ("monthly_cost", "Monthly"), ("also_found_by_other_source", "Also found by other")],
                     num=("findings", "monthly_cost", "also_found_by_other_source")),
        runs=table(runs, [("started_at", "Started"), ("status", "Status"),
                          ("zombiescan_findings", "zombiescan"), ("zombiescan_monthly_cost", "zombiescan monthly"),
                          ("recommender_findings", "Recommender"), ("recommender_monthly_cost", "Recommender monthly"),
                          ("run_id", "Run")],
                   num=("zombiescan_findings", "zombiescan_monthly_cost",
                        "recommender_findings", "recommender_monthly_cost")),
    )


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zombie Sweep</title>
<style>
:root {{ --bg:#fbfaf8; --fg:#1d1d1b; --muted:#6b6a66; --line:#e4e1db; --card:#ffffff; --accent:#2f6f4f; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161614; --fg:#ecebe7; --muted:#a3a19b; --line:#33322e; --card:#1f1e1c; --accent:#7cc39c; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px }} h2 {{ font-size:16px; margin:32px 0 8px }}
.muted {{ color:var(--muted) }}
.brief {{ background:var(--card); border:1px solid var(--line); border-left:3px solid var(--accent); padding:12px 16px; white-space:pre-wrap; border-radius:6px }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; margin-top:16px }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:6px; padding:12px 16px }}
.label {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted) }}
.big {{ font-size:28px; font-weight:600; font-variant-numeric:tabular-nums }} .big span {{ font-size:14px; color:var(--muted); font-weight:400 }}
.scroll {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--card) }}
table {{ border-collapse:collapse; width:100%; font-size:13px }}
th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top }}
th {{ color:var(--muted); font-weight:500 }} tr:last-child td {{ border-bottom:0 }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }}
form {{ display:flex; gap:8px; margin-top:8px }}
input {{ flex:1; padding:8px 10px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); font:inherit; min-width:0 }}
button {{ padding:8px 14px; border:0; border-radius:6px; background:var(--accent); color:var(--bg); font:inherit; cursor:pointer }}
#answer {{ white-space:pre-wrap; margin-top:12px }} #calls {{ font:12px ui-monospace, monospace; color:var(--muted); white-space:pre-wrap }}
</style></head><body><main>
<h1>Zombie Sweep: {project}</h1>
<div class="muted">Latest finished run {run_id}, started {started} UTC. Every figure on this page comes from SQL in AlloyDB.</div>
<h2>Briefing</h2><div class="brief">{briefing}</div>
<div class="tiles">{tiles}</div>
<h2>Ask</h2>
<form id="f"><input id="q" placeholder="e.g. how many zombiescan findings cost $10 a month or more?" autocomplete="off"><button>Ask</button></form>
<div id="answer"></div><div id="calls"></div>
<h2>Most expensive</h2>{top}
<h2>Found by only one source</h2>{only}
<h2>Since the previous run</h2>{diff}
<h2>By check</h2>{checks}
<h2>Runs</h2>{runs}
</main>
<script>
document.getElementById('f').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const q = document.getElementById('q').value.trim(); if (!q) return;
  const a = document.getElementById('answer'), c = document.getElementById('calls');
  a.textContent = 'Thinking…'; c.textContent = '';
  try {{
    const r = await fetch('ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{question:q}})}});
    const d = await r.json();
    a.textContent = d.answer || ('Error: ' + JSON.stringify(d));
    c.textContent = (d.tool_calls||[]).map(t => 'tool ' + t.tool + ' ' + JSON.stringify(t.args)).join('\\n');
  }} catch (err) {{ a.textContent = 'Error: ' + err; }}
}});
</script></body></html>"""
