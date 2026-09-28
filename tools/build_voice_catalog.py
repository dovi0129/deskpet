"""Rebuild voice_catalog.json from the original 140 lines + tools/voice_lines.py.

Input is always tools/voice_catalog_base140.json (the untouched rc9-classic1
catalog), so running this twice gives the same file. DEDUPE edits the text of
three original ids. The result is then
loaded with the real VoiceCatalog loader; any error aborts without writing.

  python tools/build_voice_catalog.py [--check]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import voice_lines as src  # noqa: E402
from voice_engine import VoiceCatalog  # noqa: E402

CATALOG = ROOT / "voice_catalog.json"
BASE = ROOT / "tools" / "voice_catalog_base140.json"
INHERIT = ("requires_all", "requires_any", "forbids", "claims", "event", "priority", "cooldown_s", "ttl_s")


def normalize(text: str) -> str:
    # Same normalization the engine uses for repetition checks.
    return re.sub(r"[\s~.!?…。·,_-]+", "", text.lower())


def build() -> dict:
    data = json.loads(BASE.read_text(encoding="utf-8"))
    original = data["messages"]
    by_id = {m["id"]: m for m in original}
    for mid, new in src.DEDUPE.items():
        by_id[mid]["text"] = new

    template = {}
    for m in original:
        template.setdefault(m["intent"], m)

    out = list(original)
    counters: Counter = Counter()

    def add(intent: str, text: str, family: str, extra: list[str] = (), weight: float = 1.0) -> None:
        base = template[intent]
        counters[intent] += 1
        m = {k: copy.deepcopy(base[k]) for k in INHERIT if k in base}
        m["id"] = f"{intent.lower()}_c2_{counters[intent]:03d}"
        m["intent"] = intent
        m["text"] = text
        m["family"] = family
        m["weight"] = weight
        for f in extra:
            if f not in m["requires_all"]:
                m["requires_all"].append(f)
            if f not in m["claims"]:
                m["claims"].append(f)
        out.append(m)

    for intent, lines in src.LINES.items():
        for t in lines:
            add(intent, t, template[intent]["family"])
    for intent, lines in src.SLOT_LINES.items():
        for t in lines:
            add(intent, t, template[intent]["family"] + "_value")
    for v in src.VARIANTS:
        for t in v["lines"]:
            add(v["base"], t, v["family"], v["extra"], v.get("weight", 1.0))

    for spec in getattr(src, "NEW_INTENTS", []):
        intent = spec["intent"]
        for t in spec["lines"]:
            counters[intent] += 1
            out.append({
                "id": f"{intent.lower()}_c3_{counters[intent]:03d}", "intent": intent, "text": t,
                "family": spec["family"], "requires_all": list(spec.get("requires_all", [])),
                "requires_any": list(spec.get("requires_any", [])), "forbids": list(spec.get("forbids", [])),
                "claims": list(spec.get("claims", [])), "event": spec.get("event"),
                "priority": spec["priority"], "weight": 1.0, "cooldown_s": spec["cooldown_s"],
                "ttl_s": spec["ttl_s"],
            })

    data["messages"] = out
    return data


def check(data: dict) -> list[str]:
    problems = []
    seen: dict[str, str] = {}
    for m in data["messages"]:
        key = normalize(m["text"])
        if key in seen:
            problems.append(f"duplicate text: {m['text']!r} ({seen[key]} / {m['id']})")
        seen[key] = m["id"]
    ids = Counter(m["id"] for m in data["messages"])
    problems += [f"duplicate id {i}" for i, c in ids.items() if c > 1]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "voice_catalog.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cat = VoiceCatalog(Path(d))
        problems += cat.errors
        if not cat.errors and len(cat.messages) != len(data["messages"]):
            problems.append(f"loader kept {len(cat.messages)} of {len(data['messages'])}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only, do not write")
    args = ap.parse_args()
    data = build()
    problems = check(data)
    by_intent = Counter(m["intent"] for m in data["messages"])
    print(f"messages: {len(data['messages'])}, intents: {len(by_intent)}")
    for intent, n in sorted(by_intent.items(), key=lambda x: -x[1]):
        print(f"  {intent:20s} {n}")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    if not args.check:
        with open(CATALOG, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"written: {CATALOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
