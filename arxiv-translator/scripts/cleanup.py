#!/usr/bin/env python3
"""
Remove the .tmp_arxiv working directory.
Usage: python cleanup.py <base_dir>

Removes <base_dir>/.tmp_arxiv entirely and deletes temporary
inspect_*.txt files created under <base_dir>.
Call this only after all papers have been compiled.
"""
import os
import re
import shutil
import sys


INSPECT_OUTPUT_RE = re.compile(r"^inspect_.*\.txt$")


def remove_inspect_outputs(base_dir):
    removed = []
    for entry in os.listdir(base_dir):
        path = os.path.join(base_dir, entry)
        if not os.path.isfile(path):
            continue
        if not INSPECT_OUTPUT_RE.fullmatch(entry):
            continue
        os.remove(path)
        removed.append(path)
    return removed


def cleanup(base_dir):
    base_dir = os.path.abspath(base_dir)
    target = os.path.join(base_dir, ".tmp_arxiv")
    if os.path.exists(target):
        shutil.rmtree(target)
        print(f"✅ Removed: {target}")
    else:
        print(f"Nothing to remove: {target}")

    removed_outputs = remove_inspect_outputs(base_dir)
    if removed_outputs:
        for path in removed_outputs:
            print(f"✅ Removed: {path}")
    else:
        print(f"Nothing to remove: {base_dir}/inspect_*.txt")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python cleanup.py <base_dir>", file=sys.stderr)
        sys.exit(2)
    cleanup(sys.argv[1])
