#!/usr/bin/env python3
"""
Submit a LaTeX project to latex-on-http for compilation.
Usage: python compile.py <work_dir> <main_tex> <output_pdf_path>

main_tex: path relative to work_dir (e.g. ms.tex), or absolute path to the main file.
output_pdf_path: full path for the output PDF; if an existing directory is passed, write <main_basename>.pdf there.
"""
import base64
import os
import re
import sys

import requests


_BIBLATEX_RE = re.compile(r"\\(?:usepackage(?:\[[^\]]*\])?\{biblatex\}|addbibresource\{)")
_BIBTEX_CMD_RE = re.compile(r"\\bibliography\{")
_THEBIB_RE = re.compile(r"\\begin\{thebibliography\}")
_BBL_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{[^}]+\.bbl\}")
_CMD_ALREADY_DEFINED_RE = re.compile(r"LaTeX Error: Command \\([A-Za-z@]+) already defined")
_CMD_ALREADY_DEFINED_WITH_PATH_RE = re.compile(
    r"^\./(?P<path>[^:\n]+):(?P<lineno>\d+):\s+LaTeX Error: Command \\(?P<cmd>[A-Za-z@]+) already defined",
    re.MULTILINE,
)
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\{document\}")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_UNRESOLVED_CITE_MARKERS = ("[?", "?]")
_UNRESOLVED_REF_MARKERS = ("??",)
_SOURCE_TEXT_EXTS = {
    ".tex",
    ".sty",
    ".cls",
    ".bst",
    ".bib",
    ".bbx",
    ".cbx",
    ".cfg",
}
_BUILD_ARTIFACT_EXTS = (
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".lof",
    ".lot",
    ".nav",
    ".snm",
    ".vrb",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz",
    ".run.xml",
    ".bcf",
    ".blg",
    ".idx",
    ".ilg",
    ".ind",
    ".xdv",
    ".dvi",
    ".ps",
)
_SKIP_FILENAMES = {"download.env"}
_AUTO_CJK_PREAMBLE = "\n".join(
    (
        r"\usepackage{fontspec}",
        r"\usepackage{luatexja}",
        r"\usepackage{luatexja-fontspec}",
        r"\setmainjfont{Noto Serif CJK SC}",
        "",
    )
)


def encode(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _norm_relpath(path, root):
    rel = os.path.relpath(path, root)
    rel = os.path.normpath(rel)
    if os.name == "nt":
        rel = rel.replace("\\", "/")
    return rel


def _iter_project_files(work_dir):
    for root, _, files in os.walk(work_dir):
        for fname in files:
            abs_path = os.path.join(root, fname)
            yield abs_path, _norm_relpath(abs_path, work_dir)


def _collect_source_texts(work_dir):
    texts = {}
    for abs_path, rel in _iter_project_files(work_dir):
        if os.path.splitext(rel)[1].lower() not in _SOURCE_TEXT_EXTS:
            continue
        try:
            texts[rel] = _read_text(abs_path)
        except OSError:
            continue
    return texts


def _main_tex_relative(work_dir, main_tex):
    work_dir = os.path.abspath(work_dir)
    if os.path.isabs(main_tex):
        main_abs = os.path.abspath(main_tex)
    else:
        main_abs = os.path.abspath(os.path.join(work_dir, main_tex))
    try:
        rel = os.path.relpath(main_abs, work_dir)
    except ValueError:
        rel = main_tex
    if rel.startswith("..") or os.path.isabs(rel):
        print(
            "Error: main file must be inside work_dir.\n"
            f"  work_dir={work_dir}\n"
            f"  main_tex={main_tex} -> {main_abs}",
            file=sys.stderr,
        )
        sys.exit(1)
    rel = os.path.normpath(rel)
    if os.name == "nt":
        rel = rel.replace("\\", "/")
    return work_dir, rel


def _resolve_output_pdf(output_path, main_tex_rel):
    output_path = os.path.expanduser(output_path)
    if output_path.endswith(os.sep) or (os.path.exists(output_path) and os.path.isdir(output_path)):
        base = os.path.splitext(os.path.basename(main_tex_rel))[0] + ".pdf"
        return os.path.join(output_path.rstrip(os.sep), base)
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    return output_path


def _find_prebuilt_bbl(work_dir, main_rel):
    main_stem = os.path.splitext(os.path.basename(main_rel))[0].lower()
    bbl_files = []
    for _, rel in _iter_project_files(work_dir):
        if rel.lower().endswith(".bbl"):
            bbl_files.append(rel)
    if not bbl_files:
        return None
    for rel in bbl_files:
        if os.path.splitext(os.path.basename(rel))[0].lower() == main_stem:
            return rel
    if len(bbl_files) == 1:
        return bbl_files[0]
    return None


def _detect_bibliography_setup(work_dir, main_rel):
    tex_blob = "\n".join(
        text for rel, text in _collect_source_texts(work_dir).items() if rel.lower().endswith(".tex")
    )
    has_bib_files = any(rel.lower().endswith(".bib") for _, rel in _iter_project_files(work_dir))
    prebuilt_bbl = _find_prebuilt_bbl(work_dir, main_rel)

    if _BIBLATEX_RE.search(tex_blob):
        return "biber", None

    # If the document includes an explicit thebibliography environment, BibTeX is unnecessary.
    # Running BibTeX in this case can fail (no .bib database) and leave citations unresolved ("?").
    if _THEBIB_RE.search(tex_blob) or _BBL_INPUT_RE.search(tex_blob):
        return None, None

    # If the paper ships a ready-made .bbl but no .bib, reuse it directly.
    # latex-on-http renames the main file to __main_document__.tex, so we mirror the
    # prebuilt bibliography under the matching __main_document__.bbl name at upload time.
    if _BIBTEX_CMD_RE.search(tex_blob):
        if prebuilt_bbl and not has_bib_files:
            return None, prebuilt_bbl
        return "bibtex", None

    if has_bib_files:
        return "bibtex", None

    return None, None


def _detect_compiler(work_dir, main_rel):
    main_text = _read_text(os.path.join(work_dir, main_rel))

    # Prefer a compiler that matches the CJK stack used in the document.
    # This avoids subtle incompatibilities and also reduces macro conflicts.
    if "\\usepackage{xeCJK}" in main_text or "\\setCJKmainfont" in main_text:
        return "xelatex"
    if "\\usepackage{luatexja}" in main_text or "\\usepackage{luatexja-fontspec}" in main_text or "\\setmainjfont" in main_text:
        return "lualatex"

    # Default for this skill: lualatex (works well with fontspec and many arXiv sources).
    return "lualatex"


def _map_server_path_to_local(server_path, main_rel):
    # latex-on-http renames the main file to __main_document__.tex
    base = os.path.basename(server_path)
    if base == "__main_document__.tex":
        return main_rel
    return server_path.lstrip("./")


def _patch_file_replace(path, pattern, repl, count=1):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False
    new_text, n = re.subn(pattern, repl, text, count=count, flags=re.MULTILINE)
    if n <= 0:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError:
        return False
    return True


def _project_contains_cjk(work_dir):
    for rel, text in _collect_source_texts(work_dir).items():
        if rel.lower().endswith(".tex") and _CJK_RE.search(text):
            return True
    return False


def _ensure_cjk_support(work_dir, main_rel):
    path = os.path.join(work_dir, main_rel)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False

    if not _project_contains_cjk(work_dir):
        return False

    # Respect an existing CJK/Unicode stack if the paper already has one.
    if any(
        tok in text
        for tok in (
            "\\usepackage{luatexja}",
            "\\usepackage{luatexja-fontspec}",
            "\\usepackage{xeCJK}",
            "\\usepackage{ctex}",
            "\\setmainjfont{",
            "\\setCJKmainfont{",
        )
    ):
        return False

    if not _BEGIN_DOCUMENT_RE.search(text):
        return False

    preamble = _AUTO_CJK_PREAMBLE
    if "\\usepackage{fontspec}" in text:
        preamble = preamble.replace("\\usepackage{fontspec}\n", "", 1)

    new_text, n = _BEGIN_DOCUMENT_RE.subn(lambda _: preamble + r"\begin{document}", text, count=1)
    if n <= 0:
        return False

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    except OSError:
        return False

    return True


def _preflight_comment_inputenc_fontenc(work_dir, main_rel):
    # When using XeLaTeX/LuaLaTeX stacks (fontspec / xeCJK / luatexja),
    # inputenc/fontenc frequently cause compilation issues.
    path = os.path.join(work_dir, main_rel)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False

    uses_unicode_stack = any(
        tok in text
        for tok in (
            "\\usepackage{fontspec}",
            "\\usepackage{xeCJK}",
            "\\usepackage{luatexja}",
            "\\usepackage{ctex}",
        )
    )
    if not uses_unicode_stack:
        return False

    changed = False
    # Comment only if the line is not already commented out.
    def _comment_line(m):
        indent = m.group("indent") or ""
        line = m.group(0)
        # Keep indentation, comment the rest of the line.
        return indent + "% " + line[len(indent) :]

    for pat in (
        r"^(?P<indent>\s*)\\usepackage\[[^\]]*\]\{inputenc\}.*$",
        r"^(?P<indent>\s*)\\usepackage\[[^\]]*\]\{fontenc\}.*$",
    ):
        if re.search(pat, text, flags=re.MULTILINE):
            text = re.sub(pat, _comment_line, text, count=1, flags=re.MULTILINE)
            changed = True

    if changed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            return False
    return changed


def _fix_command_already_defined(work_dir, rel_path, cmd):
    # Prefer \renewcommand so the paper's intended macro definition wins.
    # This is safer than \providecommand for math macros commonly redefined in arXiv sources.
    abs_path = os.path.join(work_dir, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return False

    cmd_esc = re.escape(cmd)
    pat1 = re.compile(rf"^\s*\\newcommand\*?\s*\\{cmd_esc}\b")
    pat2 = re.compile(rf"^\s*\\newcommand\*?\s*\{{\\{cmd_esc}\}}\b")

    changed = False
    for i, line in enumerate(lines):
        if pat1.search(line) or pat2.search(line):
            # Replace only the defining primitive; keep the rest (args/body) intact.
            lines[i] = re.sub(r"\\newcommand\*?", r"\\renewcommand", line, count=1)
            changed = True
            break

    if not changed:
        return False

    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        return False

    return True


def _extract_pdf_text(pdf_path):
    # Best-effort; used only for unresolved markers detection.
    try:
        import pypdf  # type: ignore
    except Exception:
        return None

    try:
        reader = pypdf.PdfReader(pdf_path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return None


def _has_unresolved_markers(pdf_path):
    text = _extract_pdf_text(pdf_path)
    if not text:
        return False
    return any(m in text for m in _UNRESOLVED_CITE_MARKERS) or any(m in text for m in _UNRESOLVED_REF_MARKERS)


def _try_fix_from_logs(work_dir, main_rel, logs_text):
    # Returns True if any fix was applied.
    applied = False

    # Fix common macro redefinition errors (e.g. luatexja defines \mc).
    m = _CMD_ALREADY_DEFINED_WITH_PATH_RE.search(logs_text)
    if m:
        rel = _map_server_path_to_local(m.group("path"), main_rel)
        cmd = m.group("cmd")
        if _fix_command_already_defined(work_dir, rel, cmd):
            applied = True

    # Fallback: if we didn't get a path, try to extract command name and patch main.
    if not applied:
        m2 = _CMD_ALREADY_DEFINED_RE.search(logs_text)
        if m2:
            cmd = m2.group(1)
            if _fix_command_already_defined(work_dir, main_rel, cmd):
                applied = True

    return applied


def _pdf_is_referenced(rel_path, source_texts):
    rel_path = rel_path.replace("\\", "/")
    stem_path = os.path.splitext(rel_path)[0]
    base = os.path.basename(rel_path)
    base_stem = os.path.splitext(base)[0]
    keys = tuple(dict.fromkeys((rel_path, stem_path, base, base_stem)))

    for text in source_texts.values():
        if any(key and key in text for key in keys):
            return True
    return False


def _should_skip_resource(rel_path, source_texts):
    rel_lower = rel_path.lower()
    base_lower = os.path.basename(rel_lower)

    if rel_lower.startswith("__macosx/"):
        return True
    if base_lower in _SKIP_FILENAMES:
        return True
    if rel_lower.endswith(_BUILD_ARTIFACT_EXTS):
        return True
    if rel_lower.endswith(".pdf") and not _pdf_is_referenced(rel_path, source_texts):
        return True
    return False


def compile_online(work_dir, main_tex, output_path):
    work_dir, main_rel = _main_tex_relative(work_dir, main_tex)
    output_path = _resolve_output_pdf(output_path, main_rel)
    bibliography_command, prebuilt_bbl = _detect_bibliography_setup(work_dir, main_rel)
    compiler = _detect_compiler(work_dir, main_rel)

    def _build_resources():
        # Rebuild every attempt so retries include any auto-fixes applied to source files.
        source_texts = _collect_source_texts(work_dir)
        resources = []
        main_marked = False
        for fpath, rel_cmp in _iter_project_files(work_dir):
            if _should_skip_resource(rel_cmp, source_texts):
                continue
            item = {"path": rel_cmp, "file": encode(fpath)}
            if rel_cmp == main_rel:
                item["main"] = True
                main_marked = True
            resources.append(item)
        if not main_marked:
            print(
                "Error: main file not found under work_dir; cannot set main.\n"
                f"  expected relative path: {main_rel!r}\n"
                f"  work_dir: {work_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        if prebuilt_bbl and prebuilt_bbl != "__main_document__.bbl":
            resources.append(
                {
                    "path": "__main_document__.bbl",
                    "file": encode(os.path.join(work_dir, prebuilt_bbl)),
                }
            )
        return resources

    max_attempts = 3  # 1 initial + up to 2 auto-fix retries
    last_error = None

    for attempt in range(1, max_attempts + 1):
        _ensure_cjk_support(work_dir, main_rel)
        # Preflight source tweaks that are almost always needed for Unicode stacks.
        _preflight_comment_inputenc_fontenc(work_dir, main_rel)
        resources = _build_resources()

        payload = {
            "compiler": compiler,
            "resources": resources,
            "options": {
                "compiler": {"halt_on_error": True},
                "response": {"log_files_on_failure": True},
            },
        }
        if bibliography_command:
            payload["options"]["bibliography"] = {"command": bibliography_command}

        resp = requests.post(
            "https://latex.ytotech.com/builds/sync",
            json=payload,
            timeout=300,
        )

        if 200 <= resp.status_code < 300 and resp.content.startswith(b"%PDF"):
            with open(output_path, "wb") as f:
                f.write(resp.content)

            # Extra guard: catch "successful" PDFs that still contain unresolved
            # citations/references due to earlier errors or incomplete runs.
            if _has_unresolved_markers(output_path):
                last_error = "PDF contains unresolved markers (e.g. '??' or '[?]')."
                if attempt < max_attempts:
                    continue
                print(f"Compilation failed: {last_error}", file=sys.stderr)
                sys.exit(1)

            print(f"✅ Wrote PDF: {os.path.abspath(output_path)}")
            return True

        # Failure: try to parse logs (JSON error payload) and apply targeted fixes.
        logs_text = ""
        try:
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                data = resp.json()
                if isinstance(data, dict):
                    log_files = data.get("log_files") or {}
                    if isinstance(log_files, dict):
                        logs_text = log_files.get("__main_document__.log") or data.get("logs") or ""
        except Exception:
            logs_text = ""

        snippet = resp.content[:4000]
        try:
            msg = snippet.decode("utf-8", errors="replace")
        except Exception:
            msg = repr(snippet[:500])

        last_error = f"HTTP {resp.status_code}: {msg if msg.strip() else '(non-text response, truncated)'}"
        if logs_text:
            if attempt < max_attempts and _try_fix_from_logs(work_dir, main_rel, logs_text):
                # retry after applying fix
                continue

        # No fix applied or out of attempts.
        print("Compilation failed (attempt %d/%d)." % (attempt, max_attempts), file=sys.stderr)
        print(last_error, file=sys.stderr)
        if logs_text:
            print("\n--- compiler log (truncated) ---", file=sys.stderr)
            print(logs_text[-8000:], file=sys.stderr)
        sys.exit(1)

    print("Compilation failed.", file=sys.stderr)
    if last_error:
        print(last_error, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Usage: python compile.py <work_dir> <main_tex> <output_pdf_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    compile_online(sys.argv[1], sys.argv[2], sys.argv[3])
