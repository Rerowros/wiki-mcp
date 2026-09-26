"""Bundle an allowlisted agent contract for clients without a repository clone."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "worker/src/context.generated.json"
FILES = {"purpose": "purpose.md", "AGENTS": "AGENTS.md", "schema": "schema.md",
         "method": "wiki/methodology/research-cycle-idea-to-thesis.md"}


def render():
    docs = {}
    for key, rel in FILES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        docs[key] = {"path": rel, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return json.dumps({"_generated_by": "tools/export_context.py", "documents": docs},
                      ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--check", action="store_true")
    args = parser.parse_args(); expected = render()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("Agent contract bundle is stale; run python tools/export_context.py")
        print("agent contract bundle is current")
    else:
        OUT.write_text(expected, encoding="utf-8", newline="\n")
        print("wrote worker/src/context.generated.json")
