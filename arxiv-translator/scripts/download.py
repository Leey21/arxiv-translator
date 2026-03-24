#!/usr/bin/env python3
"""
Download arXiv e-print, extract, pick main .tex, fetch paper title (PDF basename).
Usage: python download.py <paper_id> <work_dir>

stdout: three lines — WORK_DIR='…' MAIN_TEX='…' PDF_NAME='…'
"""
import gzip
import html
import os
import re
import shutil
import sys
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def extract_tar_archive(tf, work_dir):
    """Use the safer tar extraction mode when the runtime supports it."""
    if sys.version_info >= (3, 12):
        tf.extractall(work_dir, filter="data")
    else:
        tf.extractall(work_dir)


def download_and_extract(paper_id, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    source_path = os.path.join(work_dir, "source.bin")

    url = f"https://arxiv.org/e-print/{paper_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(source_path, "wb") as f:
                shutil.copyfileobj(resp, f)
    except Exception as e:
        print(f"Error: download failed {url}\n{e}", file=sys.stderr)
        sys.exit(1)

    extracted = False
    try:
        if tarfile.is_tarfile(source_path):
            with tarfile.open(source_path) as tf:
                extract_tar_archive(tf, work_dir)
            extracted = True
    except Exception:
        pass
    if not extracted:
        try:
            with gzip.open(source_path, "rb") as gz, open(os.path.join(work_dir, "paper.tex"), "wb") as out:
                shutil.copyfileobj(gz, out)
            extracted = True
        except Exception:
            pass
    if not extracted:
        shutil.copy(source_path, os.path.join(work_dir, "paper.tex"))
    os.remove(source_path)

    tex_files = []
    for root, _, files in os.walk(work_dir):
        for f in files:
            if f.endswith(".tex"):
                tex_files.append(os.path.relpath(os.path.join(root, f), work_dir))
    if not tex_files:
        print("Error: no .tex files found; this paper may be PDF-only.", file=sys.stderr)
        sys.exit(1)
    return tex_files


_RE_DOCCLASS = re.compile(r"\\documentclass")
_RE_INPUT = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_RE_CITATION_TITLE = re.compile(
    r'<meta\s+name=["\']citation_title["\']\s+content=["\'](.*?)["\']',
    re.IGNORECASE,
)
_RE_HTML_TITLE = re.compile(r"<title>\s*(?:\[[^\]]+\]\s*)?(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)


def find_main_tex(work_dir, tex_files):
    candidates = []
    for tf in tex_files:
        try:
            content = open(os.path.join(work_dir, tf), "r", encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if _RE_DOCCLASS.search(content):
            candidates.append((tf, content))
    if not candidates:
        print("Error: no .tex file containing \\documentclass found.", file=sys.stderr)
        sys.exit(1)
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=lambda c: len(_RE_INPUT.findall(c[1])))


def fetch_arxiv_title(paper_id):
    """Prefer arXiv API for title; fall back to abstract page metadata."""
    title = fetch_arxiv_title_from_api(paper_id)
    if title:
        return title
    return fetch_arxiv_title_from_abs_page(paper_id)


def fetch_arxiv_title_from_api(paper_id):
    query = urllib.parse.urlencode({"id_list": paper_id})
    url = f"https://arxiv.org/api/query?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "arxiv-translator/1.0 (+https://arxiv.org/help/api/user-manual)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception:
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    entry = root.find("atom:entry", _ATOM_NS)
    if entry is None:
        return None
    title_el = entry.find("atom:title", _ATOM_NS)
    if title_el is None:
        return None
    title = " ".join(title_el.itertext()).strip()
    return re.sub(r"\s+", " ", title) or None


def fetch_arxiv_title_from_abs_page(paper_id):
    url = f"https://arxiv.org/abs/{paper_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    for pattern in (_RE_CITATION_TITLE, _RE_HTML_TITLE):
        match = pattern.search(page)
        if not match:
            continue
        title = html.unescape(match.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        if title:
            return title
    return None


def pdf_name_from_title(title, fallback, max_len=240):
    """Use paper title as PDF basename: keep text, strip path-illegal characters only."""
    if not title or not str(title).strip():
        return fallback
    s = " ".join(str(title).split())
    s = s.replace("\x00", "")
    s = s.replace("/", "-").replace("\\", "-")
    if os.name == "nt":
        for ch in '<>:"|?*':
            s = s.replace(ch, "_")
    s = s.strip().rstrip(".")
    if not s:
        return fallback
    if max_len and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _sh_var_assign(name, value):
    esc = str(value).replace("'", "'\\''")
    return f"{name}='{esc}'"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python download.py <paper_id> <work_dir>", file=sys.stderr)
        sys.exit(2)
    paper_id, work_dir = sys.argv[1], sys.argv[2]
    tex_files = download_and_extract(paper_id, work_dir)
    rel_path, _ = find_main_tex(work_dir, tex_files)
    rel_path = rel_path.replace("\\", "/")
    fallback = os.path.splitext(os.path.basename(rel_path))[0]
    pdf_name = pdf_name_from_title(fetch_arxiv_title(paper_id), fallback)

    print(_sh_var_assign("WORK_DIR", os.path.abspath(work_dir)))
    print(_sh_var_assign("MAIN_TEX", rel_path))
    print(_sh_var_assign("PDF_NAME", pdf_name))
