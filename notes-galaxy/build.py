import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "whisper"))

from jarvis_memory import graph_for, read_notes


def main():
    parser = argparse.ArgumentParser(description="Build the Jarvis Markdown note graph.")
    parser.add_argument("--notes", type=Path, default=ROOT / "notes")
    parser.add_argument("--output", type=Path, default=ROOT / "viewer" / "graph-data.js")
    args = parser.parse_args()
    graph = graph_for(read_notes(args.notes.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("const GRAPH = " + json.dumps(graph, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"{len(graph['nodes'])} notes, {len(graph['links'])} links")


if __name__ == "__main__":
    main()
