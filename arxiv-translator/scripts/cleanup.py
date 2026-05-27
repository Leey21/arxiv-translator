#!/usr/bin/env python3
"""
Remove the .tmp_arxiv working directory.
Usage: python cleanup.py <base_dir> [backup_dir]

Removes <base_dir>/.tmp_arxiv entirely and deletes temporary
inspect_*.txt files created under <base_dir>.
If backup_dir is provided, translated .tex and .bbl files are copied there
before the working directory is removed.
Call this only after all papers have been compiled.
"""
import os
import re
import shutil
import sys


INSPECT_OUTPUT_RE = re.compile(r"^inspect_.*\.txt$")
BACKUP_EXTS = {".tex", ".bbl"}


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


def backup_translated_sources(tmp_dir, backup_dir):
    copied = []
    if not backup_dir:
        return copied
    if not os.path.exists(tmp_dir):
        return copied

    backup_dir = os.path.abspath(backup_dir)
    os.makedirs(backup_dir, exist_ok=True)
    for root, _, files in os.walk(tmp_dir):
        for fname in files:
            if os.path.splitext(fname)[1].lower() not in BACKUP_EXTS:
                continue
            src = os.path.join(root, fname)
            rel = os.path.relpath(src, tmp_dir)
            dst = os.path.join(backup_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


def cleanup(base_dir, backup_dir=None):
    base_dir = os.path.abspath(base_dir)
    target = os.path.join(base_dir, ".tmp_arxiv")
    copied_sources = backup_translated_sources(target, backup_dir)
    if copied_sources:
        print(f"✅ Backed up translated sources: {os.path.abspath(backup_dir)}")

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
    if len(sys.argv) not in (2, 3):
        print("Usage: python cleanup.py <base_dir> [backup_dir]", file=sys.stderr)
        sys.exit(2)
    cleanup(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
