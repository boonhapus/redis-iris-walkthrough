"""Insert the column fragments into the scaffold, in column order. Idempotent on the scaffold.

Each `# @@ COLUMN N CELLS @@` marker in iris_basics.py is replaced by the whole content of
fragment N when that fragment exists; markers whose fragment is missing stay in place. Without
--output the notebook is rewritten in place (the assembly step). With --output PATH the scaffold
is read, the fragments inserted, and the result written to PATH, leaving the notebook untouched
(a column builder's preview; .gitignore covers .agents/fragments/preview_*.py).
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAGMENTS = {
    0: "column0_redis_search.py",
    1: "column1_langcache.py",
    2: "column2_context_retriever.py",
    3: "column3_agent_memory.py",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=None,
        help="write the assembled notebook to this path instead of over iris_basics.py",
    )
    arguments = parser.parse_args()

    notebook = ROOT / "iris_basics.py"
    text = notebook.read_text(encoding="utf-8")
    inserted = []
    for column, name in FRAGMENTS.items():
        marker = f"# @@ COLUMN {column} CELLS @@"
        if marker not in text:
            sys.exit(f"marker missing: {marker} (run on the scaffold, not on an assembled notebook)")
        path = ROOT / ".agents" / "fragments" / name
        if not path.exists():
            continue
        fragment = path.read_text(encoding="utf-8").strip("\n")
        cells = re.findall(r"^@app\.cell\(column=(\d)", fragment, flags=re.M)
        if not cells or set(cells) != {str(column)}:
            sys.exit(f"{name}: every cell must carry column={column}")
        text = text.replace(marker, fragment)
        inserted.append(name)

    target = notebook if arguments.output is None else arguments.output
    target.write_text(text, encoding="utf-8", newline="\n")
    print("assembled", inserted, "->", target)


if __name__ == "__main__":
    main()
