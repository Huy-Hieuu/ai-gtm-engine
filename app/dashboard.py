"""Step 6 dashboard: the numbers from `leads` and `metrics` as one server-rendered HTML page."""

import math
import statistics
from html import escape

from app import db

RECENT = 20


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least `pct`% of values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(pct / 100 * len(ordered)), 1) - 1]


def summary(totals: list[dict]) -> dict:
    seconds = [r["first_reply_seconds"] for r in totals if r["first_reply_seconds"] is not None]
    count = len(totals)
    return {
        "leads": count,
        "median": statistics.median(seconds) if seconds else None,
        "p90": percentile(seconds, 90),
        "qualified_pct": 100 * sum(r["qualified"] for r in totals) / count if count else None,
        "booked_pct": 100 * sum(r["booked"] for r in totals) / count if count else None,
    }


def _num(value: float | None, unit: str) -> str:
    return "-" if value is None else f"{value:.1f}{unit}"


def _cell(value) -> str:
    return escape(str(value)) if value not in (None, "") else "-"


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Speed-to-Lead dashboard</title>
<style>
body {{ margin: 0; background: #fff; color: #111827; font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 1020px; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }}
h2 {{ font-size: 18px; margin: 28px 0 8px; }}
.sub {{ color: #4b5563; margin: 0 0 16px; font-size: 14px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
.card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; }}
.card b {{ display: block; font-size: 26px; }}
.card span {{ color: #4b5563; font-size: 13px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
td, th {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
th {{ color: #4b5563; font-size: 13px; }}
code {{ font-size: 13px; background: #f3f4f6; padding: 1px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<main>
<h1>Speed-to-Lead dashboard</h1>
<p class="sub">Every lead the agent texted, from this app's database. Reload for fresh numbers.</p>
<div class="cards">
<div class="card"><b>{leads}</b><span>leads</span></div>
<div class="card"><b>{median}</b><span>median first reply</span></div>
<div class="card"><b>{p90}</b><span>p90 first reply</span></div>
<div class="card"><b>{qualified}</b><span>qualified</span></div>
<div class="card"><b>{booked}</b><span>booked</span></div>
</div>
<h2>Last {recent} leads</h2>
<table>
<tr><th>Created (UTC)</th><th>Name</th><th>Contact</th><th>State</th><th>Need</th><th>Budget</th><th>Timing</th><th>First reply</th></tr>
{rows}
</table>
</main>
</body>
</html>
"""

ROW = (
    "<tr><td>{created}</td><td>{name}</td><td><code>{contact}</code></td><td>{state}</td>"
    "<td>{need}</td><td>{budget}</td><td>{timing}</td><td>{seconds}</td></tr>"
)


def render() -> str:
    totals, recent = db.dashboard_rows(RECENT)
    s = summary(totals)
    rows = "\n".join(
        ROW.format(
            created=_cell(r["created_at"][:19].replace("T", " ")),
            name=_cell(r["first_name"]),
            contact=_cell(r["contact_id"]),
            state=_cell(r["state"]),
            need=_cell(r["need"]),
            budget=_cell(r["budget"]),
            timing=_cell(r["timing"]),
            seconds=_num(r["first_reply_seconds"], " s"),
        )
        for r in recent
    ) or '<tr><td colspan="8">No leads yet.</td></tr>'
    return PAGE.format(
        leads=s["leads"],
        median=_num(s["median"], " s"),
        p90=_num(s["p90"], " s"),
        qualified=_num(s["qualified_pct"], "%"),
        booked=_num(s["booked_pct"], "%"),
        recent=RECENT,
        rows=rows,
    )
