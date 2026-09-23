"""Format the JSON of GET /gateway/status (stdin) as a short plain-text summary."""

import json
import sys

s = json.load(sys.stdin)
print(f"Virtual Model:  {s.get('virtual_model')}")
if not s.get("conversation"):
    print("No conversation seen yet.")
    sys.exit(0)
print(f"Conversation:   {s['conversation']}")
print(f"Turn:           {s.get('turn')}")
print(f"Phase:          {s.get('phase') or '-'}")

flags = s.get("flags") or []
print("Flags:          " + ("none" if not flags else ""))
for f in flags:
    print(f"  - {f['rule_id']} (since turn {f['since_turn']}, p={f['probability']:.2f})")

props = s.get("open_proposals") or []
print("Open proposals: " + ("none" if not props else ""))
for p in props:
    print(f"  - {p['id']}: {p['rule_id']} ({p['mode']}, turn {p['turn']})")

blocks = s.get("blocks") or []
print("Blocks:         " + ("none" if not blocks else ""))
for b in blocks:
    print(f"  - {b['rule_id']} (turn {b['turn']})")

d = s.get("last_decision")
if not d:
    print("Last decision:  none yet")
    sys.exit(0)
broken = ", ".join(d.get("broken") or []) or "none"
print(f"Last decision:  broken: {broken} ({d.get('decider')}, {d.get('latency_ms')} ms)")
for rid, v in (d.get("verdicts") or {}).items():
    print(f"  - {rid}: p={v['probability']:.2f}{' BROKEN' if v['broken'] else ''}")
if d.get("error"):
    print(f"  error: {d['error']}")
