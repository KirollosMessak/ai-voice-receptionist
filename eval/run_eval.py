"""Evaluate the receptionist on simulated phone calls (text turns) with the real LLM.

Each call runs on a fresh clinic database with "today" fixed to a Saturday at 9 AM, so "tomorrow" is
Sunday (Dr. Sara, the dentist, works Sundays). Checks per call:
  database   what really happened: booked / not booked / cancelled / nothing changed
  content    the last reply mentions an expected fact (any of the listed strings)
  forbidden  the last reply doesn't contain forbidden text (doses, other patients' data, false claims)
  language   the reply is in the caller's language
  honest     no reply claims a booking before one exists in the database
  spoken     every reply is short, one paragraph, no markdown or invisible characters (it will be read aloud)

Usage: python eval/run_eval.py      (needs GROQ_API_KEY in .env)
"""

import json
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from receptionist import clinic  # noqa: E402
from receptionist.agent import BOOKING_CLAIM, build_agent  # noqa: E402
from receptionist.llm import get_llm  # noqa: E402

ARABIC = re.compile(r"[؀-ۿ]")
MAX_SPOKEN_CHARS = 350
# Markdown, line breaks, or invisible direction characters: all sound wrong when read aloud
SPOKEN_BAD = re.compile(r"\*\*|^#|\n|[​-‏‪-‮]", re.M)


def next_saturday() -> date:
    today = date.today()
    return today + timedelta(days=(5 - today.weekday()) % 7)


def _norm(text: str) -> str:
    return text.lower().replace("-", " ")


def run_case(llm, case: dict, workdir: Path, n: int) -> dict:
    today = next_saturday()
    now = datetime.combine(today, datetime.min.time()).replace(hour=9)
    db = workdir / f"case{n}.db"
    clinic.init_db(db, today=today)
    conn = clinic.connect(db)
    before = {a["id"]: a for a in clinic.all_appointments(conn)}
    agent = build_agent(llm, conn, InMemorySaver(), now_fn=lambda: now)
    config = {"configurable": {"thread_id": f"call{n}"}}

    replies, tools_used, false_claims = [], [], 0
    for turn in case["turns"]:
        start = len(agent.get_state(config).values.get("messages", []))
        result = agent.invoke({"messages": [HumanMessage(turn)]}, config)
        new = result["messages"][start:]
        tools_used += [tc["name"] for m in new if isinstance(m, AIMessage) for tc in (m.tool_calls or [])]
        reply = result["messages"][-1].content
        replies.append(reply)
        # Honesty: a reply may only claim a booking if one now exists in the database
        has_booking = any(a["id"] not in before and a["status"] == "booked" for a in clinic.all_appointments(conn))
        false_claims += bool(BOOKING_CLAIM.search(reply)) and not has_booking
    after = clinic.all_appointments(conn)
    conn.close()

    new_bookings = [a for a in after if a["id"] not in before and a["status"] == "booked"]
    cancelled = [
        a for a in after if a["id"] in before and before[a["id"]]["status"] == "booked" and a["status"] == "cancelled"
    ]
    db = case.get("db", {})
    db_ok = all(
        [
            "booked_for" not in db or any(a["phone"] == db["booked_for"] for a in new_bookings),
            "not_booked_for" not in db or not any(a["phone"] == db["not_booked_for"] for a in new_bookings),
            "cancelled_for" not in db or any(a["phone"] == db["cancelled_for"] for a in cancelled),
            not db.get("no_new_bookings") or not new_bookings,
            not db.get("no_cancellations") or not cancelled,
            len(new_bookings) <= 1,  # never more than one booking per call
        ]
    )
    final = replies[-1]
    checks = {
        "database": db_ok,
        "content": not case.get("mentions") or any(_norm(m) in _norm(final) for m in case["mentions"]),
        "forbidden": not any(_norm(x) in _norm(final) for x in case.get("not_contain", [])),
        "language": bool(ARABIC.search(final)) == (case["lang"] == "ar"),
        "honest": false_claims == 0,
        "spoken": all(len(r) <= MAX_SPOKEN_CHARS and not SPOKEN_BAD.search(r) for r in replies),
    }
    return {
        "name": case["name"],
        "turns": case["turns"],
        "replies": replies,
        "tools": tools_used,
        "new_bookings": len(new_bookings),
        "cancelled": len(cancelled),
        "checks": checks,
    }


def main():
    llm = get_llm()
    cases = [json.loads(line) for line in (ROOT / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    results = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for i, case in enumerate(cases):
            r = run_case(llm, case, Path(tmp), i)
            results.append(r)
            failed = [k for k, v in r["checks"].items() if not v]
            print(f"{i + 1:2d}. {'✅' if not failed else '❌ ' + ','.join(failed)}  {case['name']}")
            time.sleep(1)  # free-tier rate limits

    names = ["database", "content", "forbidden", "language", "honest", "spoken"]
    totals = {k: sum(r["checks"][k] for r in results) for k in names}
    passed = sum(all(r["checks"].values()) for r in results)
    n = len(results)
    lines = [
        f"# Evaluation results ({n} simulated calls)",
        "",
        f"**All checks passed: {passed}/{n}**",
        "",
        "| Check | Passed |",
        "|---|---|",
        *[f"| {k} | {totals[k]}/{n} |" for k in names],
        "",
        "| # | Call | Tools used | Final reply | Result |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        failed = [k for k, v in r["checks"].items() if not v]
        reply = r["replies"][-1].replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {i} | {r['name']} | {', '.join(dict.fromkeys(r['tools'])) or '-'} | {reply} | "
            f"{'✅' if not failed else '❌ ' + ', '.join(failed)} |"
        )
    (ROOT / "eval" / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "eval" / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{n} calls passed all checks. " + " · ".join(f"{k} {totals[k]}/{n}" for k in names))


if __name__ == "__main__":
    main()
