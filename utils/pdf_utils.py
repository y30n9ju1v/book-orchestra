# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import os
import re
import json
import shutil
import struct
import subprocess
import tempfile
import textwrap
import threading
import traceback
import zlib

_LAST_COMPILE_STATE = threading.local()

FORBIDDEN_LATEX_COMMANDS: list[tuple[str, str]] = [
    (r"\\write\s*18(?![a-zA-Z])", r"\write18 (shell execution)"),
    (r"\\immediate(?![a-zA-Z])", r"\immediate (unrestricted stream/shell I/O)"),
    (r"\\openin(?![a-zA-Z])", r"\openin (arbitrary file read)"),
    (r"\\openout(?![a-zA-Z])", r"\openout (arbitrary file write)"),
    (r"\\newread(?![a-zA-Z])", r"\newread"),
    (r"\\newwrite(?![a-zA-Z])", r"\newwrite"),
    (r"\\read(?![a-zA-Z])", r"\read (file stream read)"),
    (r"\\input(?![a-zA-Z])", r"\input (arbitrary file inclusion)"),
    (r"\\include(?![a-zA-Z])", r"\include (arbitrary file inclusion)"),
    (r"\\verbatiminput(?![a-zA-Z])", r"\verbatiminput (file inclusion)"),
    (r"\\lstinputlisting(?![a-zA-Z])", r"\lstinputlisting (file inclusion)"),
    (r"\\ShellEscape(?![a-zA-Z])", r"\ShellEscape"),
    (r"\\catcode(?![a-zA-Z])", r"\catcode (TeX lexer mutation)"),
    (r"\\@@input(?![a-zA-Z])", r"\@@input"),
    (r"\\special(?![a-zA-Z])", r"\special"),
    (r"\\pdfobj(?![a-zA-Z])", r"\pdfobj"),
]


def detect_latex_engine() -> str | None:
    """Returns 'pdflatex', 'tectonic', or None if no LaTeX engine is installed."""
    if shutil.which("pdflatex"):
        return "pdflatex"
    if shutil.which("tectonic"):
        return "tectonic"
    return None


def get_last_compile_mode() -> str:
    """Returns the render mode from the most recent compile_latex call ('pdflatex'|'tectonic'|'preview'|'failed')."""
    return getattr(_LAST_COMPILE_STATE, "render_mode", "unknown")

try:
    import rich
except ImportError:

    class _RichFallback:
        @staticmethod
        def print(msg):
            cleaned = re.sub(r"\[/?[a-zA-Z0-9_ ]+\]", "", str(msg))
            print(cleaned)

    rich = _RichFallback()  # type: ignore

try:
    import pymupdf4llm  # type: ignore
except ImportError:
    pymupdf4llm = None  # type: ignore

try:
    import pymupdf  # type: ignore
except ImportError:
    pymupdf = None  # type: ignore

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None  # type: ignore

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None  # type: ignore

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:
    PdfReader = None  # type: ignore


def _extract_pdf_text_pdfminer(pdf_path: str) -> str:
    try:
        from pdfminer.high_level import extract_text  # type: ignore

        return extract_text(pdf_path) or ""
    except Exception:
        return ""


def _extract_pdf_text_raw_bytes(pdf_path: str) -> str:
    try:
        with open(pdf_path, "rb") as f:
            raw = f.read().decode("latin-1", errors="ignore")
        b64_match = re.search(r"%UTF8_TEXT_B64:([A-Za-z0-9+/=]+)", raw)
        if b64_match:
            try:
                return base64.b64decode(b64_match.group(1)).decode("utf-8", errors="replace")
            except Exception:
                pass
        matches = re.findall(r"\((.*?)\)\s*Tj", raw, re.DOTALL)
        cleaned = []
        for m in matches:
            s = m.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
            cleaned.append(s)
        return "\n".join(cleaned)
    except Exception:
        return ""


def load_book(pdf_path, num_pages=None, min_size=100):
    """Extracts text from a PDF file using available PDF backends."""
    text = ""
    if pymupdf is not None:
        try:
            doc = pymupdf.open(pdf_path)
            if num_pages:
                doc = doc[:num_pages]
            for page in doc:
                text += page.get_text()
            if len(text) >= min_size:
                return text
        except Exception as e:
            print(f"Error with pymupdf, trying next backend: {e}")

    if PdfReader is not None:
        try:
            reader = PdfReader(pdf_path)
            pages = reader.pages if num_pages is None else reader.pages[:num_pages]
            text = "".join((page.extract_text() or "") for page in pages)
            if len(text) >= min_size:
                return text
        except Exception as e:
            print(f"Error with pypdf, trying next backend: {e}")

    text = _extract_pdf_text_pdfminer(pdf_path)
    if len(text) >= min_size:
        return text

    if shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", pdf_path, "-"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and len(proc.stdout) >= min_size:
                return proc.stdout
        except Exception:
            pass

    raw_text = _extract_pdf_text_raw_bytes(pdf_path)
    if len(raw_text) >= min_size:
        return raw_text

    if len(text) > 0:
        return text
    if len(raw_text) > 0:
        return raw_text

    raise Exception(f"Text too short or unreadable from PDF: {pdf_path}")


def latex_to_markdown(latex_text: str) -> str:
    """Converts a LaTeX technical book/paper source into clean Markdown."""
    if not latex_text:
        return ""

    text = latex_text
    # Extract document body if present
    begin_doc = re.search(r"\\begin\{document\}", text)
    end_doc = re.search(r"\\end\{document\}", text)
    title_match = re.search(r"\\title\{(.*?)\}", text, re.DOTALL)
    author_match = re.search(r"\\author\{(.*?)\}", text, re.DOTALL)

    if begin_doc and end_doc and end_doc.start() > begin_doc.end():
        body = text[begin_doc.end() : end_doc.start()]
    else:
        body = text

    # Remove comments (excluding escaped \%)
    body = re.sub(r"(?<!\\)%[^\n]*", "", body)
    body = body.replace(r"\maketitle", "")
    body = body.replace(r"\tableofcontents", "")
    body = body.replace(r"\frontmatter", "").replace(r"\mainmatter", "").replace(r"\backmatter", "")
    body = body.replace(r"\clearpage", "\n").replace(r"\newpage", "\n").replace(r"\appendix", "\n# Appendix\n")

    # Preserve code listings before inline regexes
    code_blocks = []

    def _replace_lstlisting(match):
        opts = match.group(1) or ""
        code = match.group(2).strip("\n")
        lang_match = re.search(r"language\s*=\s*([A-Za-z0-9_+-]+)", opts)
        cap_match = re.search(r"caption\s*=\s*\{(.*?)\}", opts)
        lang = lang_match.group(1).lower() if lang_match else ""
        header = f"**Listing: {cap_match.group(1).strip()}**\n\n" if cap_match else ""
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append(f"{header}```{lang}\n{code}\n```")
        return f"\n{placeholder}\n"

    body = re.sub(
        r"\\begin\{lstlisting\}(?:\[(.*?)\])?(.*?)\\end\{lstlisting\}",
        _replace_lstlisting,
        body,
        flags=re.DOTALL,
    )

    # Callout boxes
    box_labels = {
        "objectivesbox": "Chapter Learning Objectives",
        "tipbox": "Engineering Tip",
        "warningbox": "Production Warning",
        "notebox": "Technical Note",
        "architecturebox": "Architecture Trade-off",
        "pitfallbox": "Production Pitfall",
        "exercisesbox": "Summary & Exercises",
    }
    for env_name, default_title in box_labels.items():
        pattern = rf"\\begin\{{{env_name}\}}(?:\[(.*?)\])?(.*?)\\end\{{{env_name}\}}"

        def _replace_box(m, title=default_title):
            custom = m.group(1).strip() if m.group(1) else title
            inner = m.group(2).strip()
            lines = [f"> **{custom}**"]
            for ln in inner.splitlines():
                lines.append(f"> {ln}")
            return "\n" + "\n".join(lines) + "\n"

        body = re.sub(pattern, _replace_box, body, flags=re.DOTALL)

    # Structural headings
    body = re.sub(r"\\part\*?\{(.*?)\}", r"\n# Part: \1\n", body)
    body = re.sub(r"\\chapter\*?\{(.*?)\}", r"\n# \1\n", body)
    body = re.sub(r"\\section\*?\{(.*?)\}", r"\n## \1\n", body)
    body = re.sub(r"\\subsection\*?\{(.*?)\}", r"\n### \1\n", body)
    body = re.sub(r"\\subsubsection\*?\{(.*?)\}", r"\n#### \1\n", body)
    body = re.sub(r"\\paragraph\*?\{(.*?)\}", r"\n**\1** ", body)

    # Abstract
    body = re.sub(
        r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
        r"\n## Abstract / Overview\n\1\n",
        body,
        flags=re.DOTALL,
    )

    # Figures
    def _replace_figure(m):
        inner = m.group(1)
        inc = re.search(r"\\includegraphics(?:\[.*?\])?\{(.*?)\}", inner)
        cap = re.search(r"\\caption\{(.*?)\}", inner, re.DOTALL)
        path = inc.group(1).strip() if inc else "figure"
        caption = cap.group(1).strip() if cap else path
        return f"\n![{caption}]({path})\n*{caption}*\n"

    body = re.sub(
        r"\\begin\{figure\*?\}(?:\[.*?\])?(.*?)\\end\{figure\*?\}",
        _replace_figure,
        body,
        flags=re.DOTALL,
    )

    # Lists
    body = re.sub(r"\\begin\{(?:itemize|enumerate)\}(?:\[.*?\])?", "", body)
    body = re.sub(r"\\end\{(?:itemize|enumerate)\}", "", body)
    body = re.sub(r"\\item\s*", "- ", body)

    # Inline formatting & citations
    body = re.sub(r"\\label\{.*?\}", "", body)
    body = re.sub(r"\\(?:cite|citet|citep)\{(.*?)\}", r"[\1]", body)
    body = re.sub(r"\\(?:cref|Cref|ref)\{(.*?)\}", r"(\1)", body)
    body = re.sub(r"\\textbf\{(.*?)\}", r"**\1**", body)
    body = re.sub(r"\\(?:textit|emph)\{(.*?)\}", r"*\1*", body)
    body = re.sub(r"\\(?:texttt|verb\|)(.*?)(?:\}|\|)", r"`\1`", body)
    body = re.sub(r"\\href\{(.*?)\}\{(.*?)\}", r"[\2](\1)", body)
    body = re.sub(r"\\url\{(.*?)\}", r"\1", body)

    # Tables basic cleanup
    body = re.sub(r"\\(?:toprule|midrule|bottomrule|hline)", "", body)
    body = re.sub(r"\\begin\{(?:table\*?|center)\}(?:\[.*?\])?", "", body)
    body = re.sub(r"\\end\{(?:table\*?|center)\}", "", body)
    body = re.sub(r"\\begin\{(?:tabular|tabularx)\}\{.*?\}(?:\{.*?\})?", "", body)
    body = re.sub(r"\\end\{(?:tabular|tabularx)\}", "", body)
    body = re.sub(r"\\caption\{(.*?)\}", r"\n**Table/Figure:** \1\n", body)

    # Bibliography markers
    body = re.sub(r"\\bibliographystyle\{.*?\}", "", body)
    body = re.sub(r"\\bibliography\{.*?\}", "\n## References\n*(See references.bib)*\n", body)

    # Restore code blocks
    for idx, block in enumerate(code_blocks):
        body = body.replace(f"__CODE_BLOCK_{idx}__", block)

    header_lines = []
    if title_match:
        title_clean = re.sub(r"\\\\", " — ", title_match.group(1).strip())
        title_clean = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", title_clean)
        header_lines.append(f"# {title_clean.strip()}\n")
    if author_match:
        author_clean = re.sub(r"\\\\", ", ", author_match.group(1).strip())
        author_clean = re.sub(r"\\[a-zA-Z]+\s*", "", author_clean)
        header_lines.append(f"**Author:** {author_clean.strip()}\n")

    result = "\n".join(header_lines) + "\n" + body
    result = re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"
    return result


def _escape_pdf_text(line: str) -> str:
    ascii_line = line.encode("latin-1", errors="replace").decode("latin-1")
    return ascii_line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_minimal_pdf(text: str, pdf_path: str, title: str = "BookOrchestra Manuscript"):
    """Pure-Python multi-page PDF 1.4 writer used as fallback when pdflatex is unavailable."""
    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    raw_lines = (text or title).splitlines()
    wrapped_lines = []
    for line in raw_lines:
        if not line.strip():
            wrapped_lines.append("")
        else:
            wrapped_lines.extend(textwrap.wrap(line, width=88) or [""])

    if not wrapped_lines:
        wrapped_lines = [title]

    lines_per_page = 46
    pages_lines = [
        wrapped_lines[i : i + lines_per_page]
        for i in range(0, len(wrapped_lines), lines_per_page)
    ]

    # Object 1: Catalog, Object 2: Pages, Object 3: ASCII Helvetica (/F1),
    # Object 4-5: Korean Type0 CIDFont (/F2)
    # For each page p (0..N-1): Page obj = 6 + 2*p, Content obj = 7 + 2*p
    num_pages = len(pages_lines)
    page_obj_ids = [6 + 2 * i for i in range(num_pages)]
    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {num_pages} >>\nendobj\n".encode(
            "latin-1"
        ),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type0 /BaseFont /HYGoThic-Medium /Encoding /UniKS-UCS2-H /DescendantFonts [5 0 R] >>\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /CIDFontType2 /BaseFont /HYGoThic-Medium /CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) /Supplement 2 >> >>\nendobj\n",
    ]

    for idx, page_lines in enumerate(pages_lines):
        page_id = 6 + 2 * idx
        content_id = 7 + 2 * idx
        stream_cmds = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
        for l_idx, line in enumerate(page_lines):
            prefix = "" if l_idx == 0 else "T* "
            if line.isascii():
                escaped = _escape_pdf_text(line)
                stream_cmds.append(f"{prefix}({escaped}) Tj")
            else:
                utf16be_hex = line.encode("utf-16-be", errors="replace").hex().upper()
                stream_cmds.append(f"{prefix}/F2 10 Tf <{utf16be_hex}> Tj /F1 10 Tf")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1")

        page_obj = (
            f"{page_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>\n"
            f"endobj\n"
        ).encode("latin-1")
        content_obj = (
            f"{content_id} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode(
                "latin-1"
            )
            + stream_bytes
            + b"\nendstream\nendobj\n"
        )
        objects.append(page_obj)
        objects.append(content_obj)

    pdf_chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    current_offset = len(pdf_chunks[0])
    for obj in objects:
        offsets.append(current_offset)
        pdf_chunks.append(obj)
        current_offset += len(obj)

    xref_offset = current_offset
    total_objs = len(objects) + 1
    xref_lines = [f"xref\n0 {total_objs}\n0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n \n")
    utf8_b64 = base64.b64encode((text or title).encode("utf-8")).decode("ascii")
    trailer = (
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n"
        f"%UTF8_TEXT_B64:{utf8_b64}\n"
        f"%%EOF\n"
    )
    pdf_chunks.append("".join(xref_lines).encode("latin-1"))
    pdf_chunks.append(trailer.encode("latin-1"))

    with open(pdf_path, "wb") as f:
        f.write(b"".join(pdf_chunks))


def _write_minimal_png(png_path: str, width: int = 64, height: int = 64):
    """Writes a valid solid-white RGB PNG using pure Python stdlib."""
    os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
    raw_row = b"\x00" + (b"\xff\xff\xff" * width)
    compressed = zlib.compress(raw_row * height)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    with open(png_path, "wb") as f:
        f.write(png_bytes)


def validate_latex_syntax(
    latex_text: str, cwd: str = None
) -> tuple[bool, list[str]]:
    """
    Validates structural LaTeX integrity before compilation or preview fallback:
    1. Checks presence and ordering of \\documentclass, \\begin{document}, \\end{document}.
    2. Checks balanced \\begin{env} ... \\end{env} pairs outside code/verbatim blocks.
    3. Checks balanced unescaped curly braces { ... } outside code/verbatim blocks.
    4. When `cwd` is given, checks that all \\includegraphics targets exist on disk.
    """
    errors: list[str] = []
    if not latex_text or not latex_text.strip():
        return False, ["Empty LaTeX document."]

    if r"\documentclass" not in latex_text:
        errors.append("Missing \\documentclass declaration.")
    begin_doc = latex_text.find(r"\begin{document}")
    end_doc = latex_text.rfind(r"\end{document}")
    if begin_doc == -1:
        errors.append("Missing \\begin{document}.")
    if end_doc == -1:
        errors.append("Missing \\end{document}.")
    if begin_doc != -1 and end_doc != -1 and end_doc < begin_doc:
        errors.append("\\end{document} appears before \\begin{document}.")

    # Strip comments first
    no_comments = re.sub(r"(?<!\\)%[^\n]*", "", latex_text)

    # Check that verbatim / lstlisting blocks themselves are properly closed
    for raw_env in ("lstlisting", "verbatim"):
        opens = len(re.findall(rf"\\begin\{{{raw_env}\}}", no_comments))
        closes = len(re.findall(rf"\\end\{{{raw_env}\}}", no_comments))
        if opens != closes:
            errors.append(
                f"Unbalanced \\begin{{{raw_env}}} ({opens}) vs \\end{{{raw_env}}} ({closes})."
            )

    # Mask out lstlisting and verbatim bodies so code braces/macros don't trigger false positives
    masked = re.sub(
        r"\\begin\{(lstlisting|verbatim)\}.*?\\end\{\1\}",
        "",
        no_comments,
        flags=re.DOTALL,
    )

    # Check for dangerous TeX file/shell primitives outside verbatim/lstlisting blocks
    for pattern, label in FORBIDDEN_LATEX_COMMANDS:
        if re.search(pattern, masked):
            errors.append(
                f"Forbidden LaTeX command detected: {label} is blocked for security."
            )

    # Verify balanced \begin{...} and \end{...} stack
    env_Stack: list[str] = []
    for match in re.finditer(r"\\(begin|end)\{([^}]+)\}", masked):
        kind = match.group(1)
        env_name = match.group(2).strip()
        if kind == "begin":
            env_Stack.append(env_name)
        else:
            if not env_Stack:
                errors.append(f"Unexpected \\end{{{env_name}}} with no matching \\begin.")
            elif env_Stack[-1] != env_name:
                errors.append(
                    f"Mismatched environment: opened \\begin{{{env_Stack[-1]}}} but closed \\end{{{env_name}}}."
                )
                env_Stack.pop()
            else:
                env_Stack.pop()
    for unclosed in env_Stack:
        errors.append(f"Unclosed environment: \\begin{{{unclosed}}}.")

    # Verify balanced unescaped braces { and }
    brace_depth = 0
    i = 0
    n = len(masked)
    while i < n:
        ch = masked[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                errors.append("Unmatched closing brace '}' in LaTeX source.")
                brace_depth = 0
        i += 1
    if brace_depth > 0:
        errors.append(f"Unclosed curly braces in LaTeX source (depth={brace_depth}).")

    # Verify referenced figure files and BibTeX citation consistency when cwd is provided
    if cwd and os.path.isdir(cwd):
        from utils.path_security import is_within_directory

        for inc_match in re.finditer(
            r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", masked
        ):
            fig_rel = inc_match.group(1).strip()
            if os.path.isabs(fig_rel) or ".." in fig_rel.replace("\\", "/").split("/"):
                errors.append(
                    f"Unsafe \\includegraphics path '{fig_rel}' (absolute or parent traversal disallowed)."
                )
                continue
            candidates = [
                os.path.join(cwd, fig_rel),
                os.path.join(cwd, f"{fig_rel}.pdf"),
                os.path.join(cwd, f"{fig_rel}.png"),
                os.path.join(cwd, f"{fig_rel}.jpg"),
                os.path.join(cwd, f"{fig_rel}.jpeg"),
            ]
            if not any(os.path.exists(c) and is_within_directory(c, cwd) for c in candidates):
                errors.append(
                    f"Missing figure file referenced by \\includegraphics{{{fig_rel}}} in {cwd}."
                )

        cited_keys = sorted(
            set(
                k.strip()
                for m in re.findall(
                    r"\\(?:cite|citet|citep)(?:\[[^\]]*\])?\{([^}]+)\}", masked
                )
                for k in m.split(",")
                if k.strip()
            )
        )
        bib_match = re.search(r"\\bibliography\{([^}]+)\}", masked)
        if cited_keys and bib_match:
            bib_name = bib_match.group(1).strip()
            if not bib_name.endswith(".bib"):
                bib_name = f"{bib_name}.bib"
            bib_path = os.path.join(cwd, bib_name)
            if not os.path.exists(bib_path):
                errors.append(
                    f"Missing BibTeX file '{bib_name}' in {cwd} required by citations: {cited_keys}."
                )
            else:
                try:
                    with open(bib_path, "r", encoding="utf-8") as bf:
                        bib_text = bf.read()
                    if bib_text.count("{") != bib_text.count("}"):
                        errors.append(
                            f"Malformed BibTeX file '{bib_name}': unbalanced braces."
                        )
                    defined_bib_keys = set(
                        k.strip()
                        for k in re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib_text)
                    )
                    missing_keys = [
                        k for k in cited_keys if k not in defined_bib_keys
                    ]
                    if missing_keys:
                        errors.append(
                            f"Undefined citation key(s) {missing_keys} not found in '{bib_name}'."
                        )
                except Exception as e:
                    errors.append(f"Error reading BibTeX file '{bib_name}': {e}")

    return len(errors) == 0, errors


def inspect_book_layout_issues(latex_text: str, cwd: str = None) -> dict:
    """
    Performs deterministic typesetting & layout analysis on a LaTeX book manuscript:
    - Detects code lines > 88 chars inside `lstlisting` that will overflow book margins
    - Detects wide tables (>= 5 columns) missing `tabularx` or `\\small`/`\\footnotesize`
    - Detects `\\includegraphics` missing `figures/` prefix or width scaling
    - Includes any structural syntax issues from `validate_latex_syntax`
    """
    figure_and_tables = {}
    other_issues = []

    is_valid, syntax_errors = validate_latex_syntax(latex_text, cwd=cwd)
    for err in syntax_errors:
        other_issues.append(
            {
                "page": 1,
                "element": "LaTeX Structure",
                "detected_issue": err,
                "suggested_fix": "Fix the mismatched LaTeX environment, brace, or missing figure path.",
            }
        )

    no_comments = re.sub(r"(?<!\\)%[^\n]*", "", latex_text or "")

    # 1. Check lstlisting line widths
    for idx, m in enumerate(
        re.finditer(
            r"\\begin\{lstlisting\}(?:\[(.*?)\])?(.*?)\\end\{lstlisting\}",
            no_comments,
            flags=re.DOTALL,
        ),
        1,
    ):
        body = m.group(2) or ""
        long_lines = [
            ln for ln in body.splitlines() if len(ln) > 88
        ]
        if long_lines:
            other_issues.append(
                {
                    "page": idx,
                    "element": f"Listing {idx}",
                    "detected_issue": (
                        f"Listing {idx} contains {len(long_lines)} line(s) exceeding 88 characters "
                        f"(max={max(len(x) for x in long_lines)} chars), risking right-margin overflow."
                    ),
                    "suggested_fix": "Wrap long code lines to <= 88 characters and ensure breaklines=true.",
                }
            )

    # 2. Check figures
    for idx, m in enumerate(
        re.finditer(
            r"\\begin\{figure\*?\}(?:\[.*?\])?(.*?)\\end\{figure\*?\}",
            no_comments,
            flags=re.DOTALL,
        ),
        1,
    ):
        inner = m.group(1)
        inc = re.search(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}", inner)
        fig_key = f"Figure {idx}"
        if inc:
            opts = inc.group(1) or ""
            path = inc.group(2).strip()
            issues = []
            fixes = []
            if not path.startswith("figures/"):
                issues.append(f"Figure path '{path}' is missing the 'figures/' directory prefix.")
                fixes.append(f"Use 'figures/{os.path.basename(path)}'.")
            if "width" not in opts and "scale" not in opts:
                issues.append("Missing explicit width scaling in \\includegraphics.")
                fixes.append("Use [width=0.9\\linewidth] in \\includegraphics.")
            figure_and_tables[fig_key] = {
                "detected_issue": " ".join(issues) if issues else "None",
                "suggested_fix": " ".join(fixes) if fixes else "None",
            }

    # 3. Check tables
    for idx, m in enumerate(
        re.finditer(
            r"\\begin\{table\*?\}(?:\[.*?\])?(.*?)\\end\{table\*?\}",
            no_comments,
            flags=re.DOTALL,
        ),
        1,
    ):
        inner = m.group(1)
        tab_key = f"Table {idx}"
        tab_match = re.search(r"\\begin\{tabular\}\{([^}]+)\}", inner)
        if tab_match:
            col_spec = re.sub(r"[^lcrpX]", "", tab_match.group(1))
            if (
                len(col_spec) >= 5
                and r"\small" not in inner
                and r"\footnotesize" not in inner
                and r"\resizebox" not in inner
            ):
                figure_and_tables[tab_key] = {
                    "detected_issue": f"Wide table ({len(col_spec)} columns) without \\small or tabularx may overflow margins.",
                    "suggested_fix": "Add \\small or convert to tabularx with X columns.",
                }
            else:
                figure_and_tables[tab_key] = {
                    "detected_issue": "None",
                    "suggested_fix": "None",
                }

    return {
        "valid_syntax": is_valid,
        "figure_and_tables": figure_and_tables,
        "other_issues": other_issues,
    }


def _stage_isolated_latex_sandbox(
    cwd: str, sandbox_dir: str, texfile_name: str
) -> None:
    """
    Copies only allowlisted LaTeX build inputs (.tex target, .cls/.sty/.bst/.bib, and
    image assets under figures/) into an isolated temporary compilation directory.
    """
    allowed_root_exts = {".cls", ".sty", ".bst", ".bib"}
    target_tex = os.path.join(cwd, f"{texfile_name}.tex")
    shutil.copy2(target_tex, os.path.join(sandbox_dir, f"{texfile_name}.tex"))

    for entry in os.listdir(cwd):
        src_entry = os.path.join(cwd, entry)
        if os.path.isfile(src_entry):
            ext = os.path.splitext(entry)[1].lower()
            if ext in allowed_root_exts:
                shutil.copy2(src_entry, os.path.join(sandbox_dir, entry))

    src_figs = os.path.join(cwd, "figures")
    if os.path.isdir(src_figs):
        dst_figs = os.path.join(sandbox_dir, "figures")
        os.makedirs(dst_figs, exist_ok=True)
        for root, _, files in os.walk(src_figs):
            rel_dir = os.path.relpath(root, src_figs)
            cur_dst_dir = os.path.join(dst_figs, rel_dir) if rel_dir != "." else dst_figs
            os.makedirs(cur_dst_dir, exist_ok=True)
            for fname in files:
                if os.path.splitext(fname)[1].lower() in {".png", ".jpg", ".jpeg", ".pdf"}:
                    shutil.copy2(os.path.join(root, fname), os.path.join(cur_dst_dir, fname))


def compile_latex(
    cwd,
    pdf_file,
    texfile_name="template",
    timeout=30,
    strict_latex: bool = False,
    preview_pdf_file: str = None,
) -> bool:
    """
    Validates LaTeX syntax & security primitives, then compiles in an isolated sandbox
    using `pdflatex -no-shell-escape` or `tectonic`.
    If no TeX engine is installed:
      - In `strict_latex=True` (publish mode): fails and returns False.
      - In preview mode: writes Markdown/HTML preview PDF (to `preview_pdf_file` if provided,
        otherwise `pdf_file` for internal intermediate drafts) and sets render_mode='preview'.
    """
    _LAST_COMPILE_STATE.render_mode = "failed"
    source_pdf = os.path.join(cwd, f"{texfile_name}.pdf")
    source_tex = os.path.join(cwd, f"{texfile_name}.tex")
    if os.path.exists(source_pdf):
        os.remove(source_pdf)
    if os.path.exists(pdf_file):
        os.remove(pdf_file)
    if preview_pdf_file and os.path.exists(preview_pdf_file):
        os.remove(preview_pdf_file)

    if not os.path.exists(source_tex):
        rich.print(f"[bold red]❌ LaTeX source file not found: {source_tex}[/bold red]")
        return False

    with open(source_tex, "r", encoding="utf-8") as f:
        tex_content = f.read()

    is_valid_syntax, syntax_errors = validate_latex_syntax(tex_content, cwd=cwd)
    if not is_valid_syntax:
        rich.print(
            f"[bold red]❌ LaTeX Syntax Validation Failed for {source_tex}:[/bold red]"
        )
        for err in syntax_errors:
            rich.print(f"[red]  - {err}[/red]")
        return False

    engine = detect_latex_engine()
    if engine is not None:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/Library/TeX/texbin"),
            "openin_any": "p",
            "openout_any": "p",
            "shell_escape": "f",
        }
        if "HOME" in os.environ:
            env["HOME"] = os.environ["HOME"]

        has_citations = bool(
            re.search(r"\\(?:cite|citet|citep)(?:\[[^\]]*\])?\{[^}]+\}", tex_content)
            and re.search(r"\\bibliography\{[^}]+\}", tex_content)
        )

        if engine == "pdflatex":
            commands = [
                [
                    "pdflatex",
                    "-no-shell-escape",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    f"{texfile_name}.tex",
                ],
            ]
            if has_citations:
                commands.append(["bibtex", texfile_name])
            commands.extend(
                [
                    [
                        "pdflatex",
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        f"{texfile_name}.tex",
                    ],
                    [
                        "pdflatex",
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        f"{texfile_name}.tex",
                    ],
                ]
            )
        else:  # tectonic
            commands = [
                [
                    "tectonic",
                    "-Z",
                    "shell-escape-disabled",
                    "--keep-logs",
                    f"{texfile_name}.tex",
                ]
            ]

        execution_logs = []
        with tempfile.TemporaryDirectory(prefix="book_tex_sbx_") as sbx_dir:
            _stage_isolated_latex_sandbox(cwd, sbx_dir, texfile_name)
            sbx_pdf = os.path.join(sbx_dir, f"{texfile_name}.pdf")

            for command in commands:
                cmd_str = " ".join(command)
                try:
                    result = subprocess.run(
                        command,
                        cwd=sbx_dir,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env=env,
                    )
                    # Fallback if older tectonic version does not support -Z shell-escape-disabled
                    if (
                        engine == "tectonic"
                        and result.returncode != 0
                        and "unexpected argument '-Z'" in (result.stderr or "")
                    ):
                        result = subprocess.run(
                            ["tectonic", "--keep-logs", f"{texfile_name}.tex"],
                            cwd=sbx_dir,
                            capture_output=True,
                            text=True,
                            timeout=timeout,
                            env=env,
                        )

                    execution_logs.append(
                        {
                            "cmd": cmd_str,
                            "returncode": result.returncode,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    )

                except subprocess.TimeoutExpired:
                    execution_logs.append(
                        {"cmd": cmd_str, "error": f"TIMEOUT after {timeout}s"}
                    )
                except Exception as e:
                    execution_logs.append({"cmd": cmd_str, "error": str(e)})

            has_timeout = any(
                "error" in log and "TIMEOUT" in log["error"] for log in execution_logs
            )
            final_pdflatex_log = execution_logs[-1] if execution_logs else {}
            final_rc = final_pdflatex_log.get("returncode", 1)
            final_stdout = (final_pdflatex_log.get("stdout") or "") + "\n" + (
                final_pdflatex_log.get("stderr") or ""
            )

            has_bibtex_error = False
            if has_citations and engine == "pdflatex":
                for log in execution_logs:
                    if log.get("cmd", "").startswith("bibtex"):
                        b_out = (log.get("stdout") or "") + "\n" + (log.get("stderr") or "")
                        if (
                            log.get("returncode", 0) != 0
                            or "error message" in b_out.lower()
                            or "I didn't find a database entry for" in b_out
                        ):
                            has_bibtex_error = True

            has_fatal_tex_error = (
                final_rc != 0
                or has_bibtex_error
                or "! LaTeX Error:" in final_stdout
                or "! Emergency stop" in final_stdout
                or "LaTeX Warning: Citation `" in final_stdout
            )

            if (
                os.path.exists(sbx_pdf)
                and not has_timeout
                and not has_fatal_tex_error
            ):
                try:
                    os.makedirs(os.path.dirname(os.path.abspath(pdf_file)), exist_ok=True)
                    shutil.move(sbx_pdf, pdf_file)
                    _LAST_COMPILE_STATE.render_mode = engine
                    return True
                except Exception:
                    rich.print(
                        f"[bold red]Error moving generated PDF to {pdf_file}[/bold red]"
                    )
                    print(traceback.format_exc())
                    return False

        rich.print(
            f"\n[bold red]❌ PDF Generation Failed via {engine} (rc={final_rc})[/bold red]"
        )
        for log in execution_logs:
            if "error" in log:
                header_color = "red"
            elif log["returncode"] == 0:
                header_color = "green"
            else:
                header_color = "yellow"
            rich.print(
                f"\n[bold {header_color}]---> Ran: {log['cmd']}[/bold {header_color}]"
            )
            if "error" in log:
                rich.print(f"[bold red]SYSTEM ERROR: {log['error']}[/bold red]")
            else:
                if log["stdout"].strip():
                    rich.print(f"[dim]{log['stdout'].strip()}[/dim]")
                if log["stderr"].strip():
                    rich.print(f"[red]{log['stderr'].strip()}[/red]")
        return False

    if strict_latex:
        rich.print(
            "[bold red]❌ Publish Mode Error: Neither 'pdflatex' nor 'tectonic' is installed. "
            "Cannot produce a publication-grade typeset PDF.[/bold red]"
        )
        _LAST_COMPILE_STATE.render_mode = "failed"
        return False

    preview_target = preview_pdf_file or pdf_file
    print(
        f"⚠️ Warning: No TeX engine ('pdflatex'/'tectonic') found on system. "
        f"LaTeX syntax validated; rendering non-typeset preview PDF at '{preview_target}'."
    )

    # Fallback preview rendering (ONLY when no TeX engine is installed, strict_latex=False,
    # AND validate_latex_syntax passed)
    md_text = latex_to_markdown(tex_content)
    if pymupdf is not None and hasattr(pymupdf, "Story"):
        try:
            html_lines = [
                "<html><body style='font-family: sans-serif; font-size: 10pt;'>"
            ]
            for line in md_text.splitlines():
                escaped = (
                    line.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                if line.startswith("# "):
                    html_lines.append(f"<h1>{escaped[2:]}</h1>")
                elif line.startswith("## "):
                    html_lines.append(f"<h2>{escaped[3:]}</h2>")
                elif line.startswith("### "):
                    html_lines.append(f"<h3>{escaped[4:]}</h3>")
                else:
                    html_lines.append(f"<p>{escaped or '&nbsp;'}</p>")
            html_lines.append("</body></html>")
            story = pymupdf.Story(html="\n".join(html_lines))
            writer = pymupdf.DocumentWriter(preview_target)
            mediabox = pymupdf.paper_rect("letter")
            where = mediabox + (54, 54, -54, -54)
            more = True
            while more:
                dev = writer.begin_page(mediabox)
                more, _ = story.place(where)
                story.draw(dev, None)
                writer.end_page()
            writer.close()
            _LAST_COMPILE_STATE.render_mode = "preview"
            return True
        except Exception as e:
            print(f"PyMuPDF Story fallback failed, using pure-Python PDF writer: {e}")

    render_minimal_pdf(md_text, preview_target)
    if os.path.exists(preview_target):
        _LAST_COMPILE_STATE.render_mode = "preview"
        return True
    return False


def pdf_to_grid_images(pdf_path, output_dir, scale=2.0):
    """Converts a PDF to 2x2 grid images of its pages.
    Returns a list of paths to the generated grid images.
    """
    os.makedirs(output_dir, exist_ok=True)
    if pymupdf is None or np is None or cv2 is None:
        grid_path = os.path.join(output_dir, "grid_1.png")
        _write_minimal_png(grid_path)
        return [grid_path]

    doc = pymupdf.open(pdf_path)

    mat = pymupdf.Matrix(scale, scale)
    pixmaps = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        pixmaps.append(pix)

    if not pixmaps:
        grid_path = os.path.join(output_dir, "grid_1.png")
        _write_minimal_png(grid_path)
        return [grid_path]

    grid_image_paths = []

    for i in range(0, len(pixmaps), 4):
        chunk = pixmaps[i : i + 4]

        img_list = []
        for pix in chunk:
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.h, pix.w, pix.n
            )
            img_list.append(img)

        while len(img_list) < 4:
            ref_img = img_list[0]
            white_img = np.ones_like(ref_img) * 255
            img_list.append(white_img)

        row1 = np.hstack((img_list[0], img_list[1]))
        row2 = np.hstack((img_list[2], img_list[3]))
        grid = np.vstack((row1, row2))

        grid_path = os.path.join(output_dir, f"grid_{i//4 + 1}.png")
        grid_bgr = cv2.cvtColor(grid, cv2.COLOR_RGB2BGR)
        cv2.imwrite(grid_path, grid_bgr)

        grid_image_paths.append(grid_path)

    return grid_image_paths
