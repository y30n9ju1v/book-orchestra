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
import html
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
    """Return the best available Unicode-capable TeX engine.

    XeLaTeX and LuaLaTeX are preferred because generated books may contain Korean
    or other Unicode scripts. Tectonic is XeTeX based. pdfLaTeX remains supported
    for legacy Latin-only templates.
    """
    if shutil.which("xelatex"):
        return "xelatex"
    if shutil.which("lualatex"):
        return "lualatex"
    if shutil.which("tectonic"):
        return "tectonic"
    if shutil.which("pdflatex"):
        return "pdflatex"
    return None


def get_last_compile_mode() -> str:
    """Return the most recent TeX engine, ``preview``, ``failed``, or ``unknown``."""
    return getattr(_LAST_COMPILE_STATE, "render_mode", "unknown")


def get_last_preview_backend() -> str | None:
    """Return the preview backend used by the most recent ``compile_latex`` call, or ``None``."""
    return getattr(_LAST_COMPILE_STATE, "preview_backend", None)

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


def _normalize_extracted_pdf_text(text: str) -> str:
    """Normalize layout-only spacing characters emitted by PDF text backends."""
    return text.replace("\u00a0", " ")


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
                return _normalize_extracted_pdf_text(text)
        except Exception as e:
            print(f"Error with pymupdf, trying next backend: {e}")

    if PdfReader is not None:
        try:
            reader = PdfReader(pdf_path)
            pages = reader.pages if num_pages is None else reader.pages[:num_pages]
            text = "".join((page.extract_text() or "") for page in pages)
            if len(text) >= min_size:
                return _normalize_extracted_pdf_text(text)
        except Exception as e:
            print(f"Error with pypdf, trying next backend: {e}")

    text = _extract_pdf_text_pdfminer(pdf_path)
    if len(text) >= min_size:
        return _normalize_extracted_pdf_text(text)

    if shutil.which("pdftotext"):
        try:
            proc = subprocess.run(
                ["pdftotext", pdf_path, "-"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0 and len(proc.stdout) >= min_size:
                return _normalize_extracted_pdf_text(proc.stdout)
        except Exception:
            pass

    raw_text = _extract_pdf_text_raw_bytes(pdf_path)
    if len(raw_text) >= min_size:
        return _normalize_extracted_pdf_text(raw_text)

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

    # Preserve code listings and verbatim blocks BEFORE stripping LaTeX % comments
    # (otherwise MLIR SSA values like %arg0/%0 and Bash ${VAR%.o} get stripped!).
    code_blocks = []

    def _clean_inline_tex(s: str) -> str:
        s = re.sub(r"\\allowbreak\b", "", s)
        s = s.replace("{[}", "[").replace("{]}", "]")
        for _ in range(3):
            s = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", s)
            s = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", s)
            s = re.sub(r"\\(?:textit|emph)\{([^{}]*)\}", r"*\1*", s)
        s = s.replace(r"\_", "_").replace(r"\%", "%").replace(r"\&", "&").replace(r"\,", " ")
        return s.strip()

    def _extract_braced(s: str, start: int) -> tuple[str, int]:
        """Extract balanced {...} content starting at s[start] == '{'."""
        depth = 0
        i = start
        while i < len(s):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    return s[start + 1 : i], i + 1
            i += 1
        return s[start + 1 :], len(s)

    def _replace_all_lstlistings(src: str) -> str:
        out = []
        pos = 0
        while True:
            m = re.search(r"\\begin\{lstlisting\}", src[pos:])
            if not m:
                out.append(src[pos:])
                break
            out.append(src[pos : pos + m.start()])
            cur = pos + m.end()
            opts = ""
            if cur < len(src) and src[cur] == "[":
                cur += 1
                opt_start = cur
                brace_depth = 0
                while cur < len(src):
                    if src[cur] == "{":
                        brace_depth += 1
                    elif src[cur] == "}":
                        brace_depth -= 1
                    elif src[cur] == "]" and brace_depth == 0:
                        break
                    cur += 1
                opts = src[opt_start:cur]
                cur += 1  # skip ']'
            end_m = re.search(r"\\end\{lstlisting\}", src[cur:])
            if not end_m:
                out.append(src[pos + m.start() :])
                break
            code = src[cur : cur + end_m.start()].strip("\n")
            lang_m = re.search(r"language\s*=\s*([A-Za-z0-9_+-]+)", opts)
            lang = lang_m.group(1).lower() if lang_m else ""
            cap_text = ""
            cap_m = re.search(r"caption\s*=\s*\{", opts)
            if cap_m:
                cap_raw, _ = _extract_braced(opts, cap_m.end() - 1)
                cap_text = _clean_inline_tex(cap_raw)
            header = f"**Listing: {cap_text}**\n\n" if cap_text else ""
            placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
            code_blocks.append(f"{header}```{lang}\n{code}\n```")
            out.append(f"\n{placeholder}\n")
            pos = cur + end_m.end()
        return "".join(out)

    body = _replace_all_lstlistings(body)

    def _replace_verbatim(match):
        code = match.group(1).strip("\n")
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append(f"```\n{code}\n```")
        return f"\n{placeholder}\n"

    body = re.sub(
        r"\\begin\{verbatim\}(.*?)\\end\{verbatim\}",
        _replace_verbatim,
        body,
        flags=re.DOTALL,
    )

    # Remove LaTeX comments (excluding escaped \%) AFTER code blocks are protected
    body = re.sub(r"(?<!\\)%[^\n]*", "", body)
    body = body.replace(r"\maketitle", "")
    body = body.replace(r"\tableofcontents", "")
    body = body.replace(r"\frontmatter", "").replace(r"\mainmatter", "").replace(r"\backmatter", "")
    body = body.replace(r"\clearpage", "\n").replace(r"\newpage", "\n").replace(r"\appendix", "\n# Appendix\n")

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

    # Structural headings (support optional short title [...] and nested inline commands)
    body = re.sub(r"\\part\*?(?:\[[^\]]*\])?\{(.*?)\}", r"\n# Part: \1\n", body)
    body = re.sub(r"\\chapter\*?(?:\[[^\]]*\])?\{(.*?)\}", r"\n# \1\n", body)
    body = re.sub(r"\\section\*?(?:\[[^\]]*\])?\{(.*?)\}", r"\n## \1\n", body)
    body = re.sub(r"\\subsection\*?(?:\[[^\]]*\])?\{(.*?)\}", r"\n### \1\n", body)
    body = re.sub(r"\\subsubsection\*?(?:\[[^\]]*\])?\{(.*?)\}", r"\n#### \1\n", body)
    body = re.sub(r"\\paragraph\*?\{(.*?)\}", r"\n**\1** ", body)

    # Abstract
    body = re.sub(
        r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
        r"\n## Abstract / Overview\n\1\n",
        body,
        flags=re.DOTALL,
    )

    # Figures (distinguish raster \includegraphics vs TikZ diagrams)
    def _replace_figure(m):
        inner = m.group(1)
        inc = re.search(r"\\includegraphics(?:\[.*?\])?\{(.*?)\}", inner)
        cap_m = re.search(r"\\caption\{", inner)
        caption = ""
        if cap_m:
            cap_raw, _ = _extract_braced(inner, cap_m.end() - 1)
            caption = _clean_inline_tex(cap_raw)
        if inc:
            path = inc.group(1).strip()
            cap_out = caption or path
            return f"\n![{cap_out}]({path})\n*{cap_out}*\n"
        if caption:
            return f"\n> **[Diagram]** {caption}\n"
        return "\n"

    body = re.sub(
        r"\\begin\{figure\*?\}(?:\[.*?\])?(.*?)\\end\{figure\*?\}",
        _replace_figure,
        body,
        flags=re.DOTALL,
    )

    # Tables -> GitHub Flavored Markdown tables
    def _replace_table(m):
        inner = m.group(1)
        cap_m = re.search(r"\\caption\{", inner)
        caption = ""
        if cap_m:
            cap_raw, _ = _extract_braced(inner, cap_m.end() - 1)
            caption = _clean_inline_tex(cap_raw)
        tab_m = re.search(
            r"\\begin\{(?:tabularx|tabular)\}(?:\{[^}]*\})?(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})?(.*?)\\end\{(?:tabularx|tabular)\}",
            inner,
            flags=re.DOTALL,
        )
        if not tab_m:
            return f"\n**Table:** {caption}\n" if caption else "\n"
        tab_body = tab_m.group(1)
        tab_body = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|addlinespace)\b(?:\[[^\]]*\])?", "", tab_body)
        # Split into rows on \\ (not escaped \\\\)
        raw_rows = [r.strip() for r in re.split(r"\\\\", tab_body) if r.strip()]
        rows = []
        for raw_row in raw_rows:
            if "&" not in raw_row:
                continue
            cells = [_clean_inline_tex(c).replace("|", "\\|").replace("\n", " ") for c in re.split(r"(?<!\\)&", raw_row)]
            if any(cells):
                rows.append(cells)
        if not rows:
            return f"\n**Table:** {caption}\n" if caption else "\n"
        ncols = max(len(r) for r in rows)
        norm_rows = [r + [""] * (ncols - len(r)) for r in rows]
        md_lines = []
        if caption:
            md_lines.append(f"**Table: {caption}**\n")
        md_lines.append("| " + " | ".join(norm_rows[0]) + " |")
        md_lines.append("| " + " | ".join(["---"] * ncols) + " |")
        for r in norm_rows[1:]:
            md_lines.append("| " + " | ".join(r) + " |")
        return "\n" + "\n".join(md_lines) + "\n"

    body = re.sub(
        r"\\begin\{table\*?\}(?:\[.*?\])?(.*?)\\end\{table\*?\}",
        _replace_table,
        body,
        flags=re.DOTALL,
    )

    # Description lists need their labels preserved before generic list cleanup.
    body = re.sub(
        r"\\item\s*\[([^\]]+)\]\s*",
        lambda m: f"\n- **{m.group(1).strip()}**: ",
        body,
    )

    # Lists
    body = re.sub(r"\\begin\{(?:itemize|enumerate)\}(?:\[.*?\])?", "", body)
    body = re.sub(r"\\end\{(?:itemize|enumerate)\}", "", body)
    body = re.sub(r"\\item\s*", "- ", body)

    # Inline formatting & citations (run thrice to handle nested \textbf{\texttt{...}})
    body = re.sub(r"\\allowbreak\b", "", body)
    body = re.sub(r"\\label\{.*?\}", "", body)
    body = re.sub(r"\\markboth\{[^}]*\}\{[^}]*\}", "", body)
    body = re.sub(r"\\lstset\{[^}]*\}", "", body)
    body = re.sub(r"\\(?:small|footnotesize|scriptsize|tiny|normalsize|large|Large)\b", "", body)
    body = re.sub(r"\\(?:renewcommand|setlength)\{[^}]*\}\{[^}]*\}", "", body)
    body = re.sub(r"~?\\(?:cite|citet|citep)\{(.*?)\}", r" [\1]", body)
    body = re.sub(r"\(\s*\\(?:cref|Cref|ref|eqref)\{(.*?)\}\s*\)", r"(\1)", body)
    body = re.sub(r"~?\\(?:cref|Cref|ref|eqref)\{(.*?)\}", r" `\1`", body)
    for _ in range(3):
        body = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", body)
        body = re.sub(r"\\verb\|([^|]*)\|", r"`\1`", body)
        body = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", body)
        body = re.sub(r"\\(?:textit|emph)\{([^{}]*)\}", r"*\1*", body)
    body = re.sub(r"\\href\{(.*?)\}\{(.*?)\}", r"[\2](\1)", body)
    body = re.sub(r"\\url\{(.*?)\}", r"\1", body)
    body = body.replace(r"\_", "_").replace(r"\%", "%").replace(r"\&", "&").replace(r"\,", " ").replace("~", " ")
    body = body.replace("``", '"').replace("''", '"')

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

    # Common layout-only commands and environments must never leak into previews.
    body = re.sub(r"\\addcontentsline\{[^}]+\}\{[^}]+\}\{([^{}]*)\}", r"\n**\1**\n", body)
    body = re.sub(r"\\(?:phantomsection|noindent|smallskip|medskip|bigskip)\b", "", body)
    body = re.sub(r"\\(?:vspace|hspace)\*?\{[^}]*\}", "", body)
    body = re.sub(r"\\(?:begin|end)\{(?:description|flushright|flushleft|quote)\}", "", body)
    body = re.sub(r"\\(?:centering|raggedright|raggedleft)\b", "", body)

    # Restore code blocks
    for idx, block in enumerate(code_blocks):
        body = body.replace(f"__CODE_BLOCK_{idx}__", block)

    header_lines = []
    if title_match:
        title_clean = re.sub(r"\\\\", " — ", title_match.group(1).strip())
        for _ in range(3):
            title_clean = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", title_clean)
        title_clean = re.sub(r"\\[a-zA-Z]+\s*", "", title_clean).replace("{", "").replace("}", "")
        header_lines.append(f"# {title_clean.strip()}\n")
    if author_match:
        author_clean = re.sub(r"\\\\", ", ", author_match.group(1).strip())
        author_clean = re.sub(r"\\[a-zA-Z]+\s*", "", author_clean).replace("{", "").replace("}", "")
        header_lines.append(f"**Author:** {author_clean.strip()}\n")

    result = "\n".join(header_lines) + "\n" + body
    result = re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"
    return result


_PREVIEW_BOX_LABELS = {
    "objectivesbox": "Chapter Learning Objectives",
    "tipbox": "Engineering Tip",
    "warningbox": "Production Warning",
    "notebox": "Technical Note",
    "architecturebox": "Architecture Trade-off",
    "pitfallbox": "Production Pitfall",
    "exercisesbox": "Summary & Exercises",
}


def _extract_braced_command(text: str, command: str) -> str:
    """Extract a possibly nested ``\\command{...}`` argument."""
    match = re.search(rf"\\{re.escape(command)}\s*\{{", text)
    if not match:
        return ""
    start = match.end()
    depth = 1
    index = start
    while index < len(text) and depth:
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    return text[start : index - 1].strip() if depth == 0 else ""


def _prepare_latex_body_for_pandoc(latex_text: str) -> tuple[str, str, str]:
    """Make custom BookOrchestra LaTeX environments consumable by Pandoc."""
    begin_doc = latex_text.find(r"\begin{document}")
    end_doc = latex_text.rfind(r"\end{document}")
    if begin_doc >= 0 and end_doc > begin_doc:
        body = latex_text[begin_doc + len(r"\begin{document}") : end_doc]
    else:
        body = latex_text

    for env_name, default_title in _PREVIEW_BOX_LABELS.items():
        pattern = rf"\\begin\{{{env_name}\}}(?:\[(.*?)\])?(.*?)\\end\{{{env_name}\}}"

        def _replace_box(match, title=default_title):
            box_title = (match.group(1) or title).strip()
            return (
                "\\begin{quote}\n"
                f"\\textbf{{{box_title}}}\\par\n"
                f"{match.group(2).strip()}\n"
                "\\end{quote}"
            )

        body = re.sub(pattern, _replace_box, body, flags=re.DOTALL)

    body = re.sub(
        r"\\addcontentsline\{[^}]+\}\{[^}]+\}\{([^{}]*)\}",
        r"\\paragraph{\1}",
        body,
    )
    body = body.replace(r"\phantomsection", "")
    body = body.replace(r"\tableofcontents", "")
    title = _extract_braced_command(latex_text, "title")
    author = _extract_braced_command(latex_text, "author")
    return body, title, author


def _latex_to_html_with_pandoc(latex_text: str, cwd: str) -> str | None:
    """Convert LaTeX to semantic HTML with Pandoc when it is installed."""
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return None
    body, title, author = _prepare_latex_body_for_pandoc(latex_text)
    command = [
        pandoc,
        "-f",
        "latex",
        "-t",
        "html5",
        "--wrap=none",
        f"--resource-path={os.path.abspath(cwd)}",
    ]
    try:
        result = subprocess.run(
            command,
            input=body,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
    except Exception as exc:
        print(f"Pandoc preview conversion failed: {exc}")
        return None
    if result.returncode != 0 or not result.stdout.strip():
        print(f"Pandoc preview conversion failed: {(result.stderr or '').strip()}")
        return None

    converted_html = re.sub(
        r'<span class="citation" data-cites="([^"]+)">.*?</span>',
        lambda match: (
            "<span class='citation'>["
            + "; ".join(html.escape(key) for key in match.group(1).split())
            + "]</span>"
        ),
        result.stdout,
        flags=re.DOTALL,
    )

    def _table_to_preview_cards(match):
        table_html = match.group(0)
        caption_match = re.search(r"<caption>(.*?)</caption>", table_html, flags=re.DOTALL)
        header_match = re.search(r"<thead>(.*?)</thead>", table_html, flags=re.DOTALL)
        headers = (
            re.findall(r"<th\b[^>]*>(.*?)</th>", header_match.group(1), flags=re.DOTALL)
            if header_match
            else []
        )
        body_match = re.search(r"<tbody>(.*?)</tbody>", table_html, flags=re.DOTALL)
        rows_html = body_match.group(1) if body_match else table_html
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", rows_html, flags=re.DOTALL)
        records = []
        for row in rows:
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
            fields = []
            for index, cell in enumerate(cells):
                label = headers[index] if index < len(headers) else f"Column {index + 1}"
                label = re.sub(r"<[^>]+>", "", label).strip()
                cell = re.sub(r"</?p\b[^>]*>", "", cell).strip()
                fields.append(
                    f"<div class='table-field'><strong>{label}:</strong> {cell}</div>"
                )
            if fields:
                records.append("<div class='table-record'>" + "".join(fields) + "</div>")
        caption = caption_match.group(1).strip() if caption_match else "Table"
        return (
            "<section class='preview-table'>"
            f"<p class='table-caption'>{caption}</p>"
            + "".join(records)
            + "</section>"
        )

    converted_html = re.sub(
        r"<table>.*?</table>", _table_to_preview_cards, converted_html, flags=re.DOTALL
    )

    front_matter = []
    if title:
        clean_title = re.sub(r"\\\\(?:\[[^\]]*\])?", " - ", title)
        clean_title = re.sub(r"\\[A-Za-z]+\{([^{}]*)\}", r"\1", clean_title)
        clean_title = re.sub(r"\\[A-Za-z]+\b", "", clean_title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -")
        front_matter.append(f"<h1 class='book-title'>{html.escape(clean_title)}</h1>")
    if author:
        clean_author = re.sub(r"\\\\", ", ", author)
        clean_author = re.sub(r"\\[A-Za-z]+\s*", "", clean_author)
        front_matter.append(f"<p class='book-author'>{html.escape(clean_author)}</p>")

    toc_items = []
    for level, attrs, heading_html in re.findall(
        r"<h([12])\b([^>]*)>(.*?)</h\1>", converted_html, flags=re.DOTALL
    ):
        id_match = re.search(r'\bid="([^"]+)"', attrs)
        if not id_match:
            continue
        heading_text = re.sub(r"<[^>]+>", "", heading_html)
        heading_text = html.unescape(heading_text).strip()
        if heading_text:
            toc_items.append(
                f"<li class='toc-level-{level}'><a href='#{html.escape(id_match.group(1))}'>"
                f"{html.escape(heading_text)}</a></li>"
            )
    if toc_items:
        front_matter.append(
            "<section class='toc'><h2 class='toc-heading'>Contents</h2><ul>"
            + "".join(toc_items)
            + "</ul></section>"
        )
    return "\n".join(front_matter) + "\n" + converted_html


def _markdown_to_html(markdown_text: str) -> str:
    """Render Markdown with Mistune, falling back to a small safe renderer."""
    try:
        import mistune  # type: ignore

        renderer = mistune.create_markdown(
            escape=True,
            plugins=["table", "strikethrough", "task_lists", "url"],
        )
        return renderer(markdown_text)
    except Exception:
        rendered = []
        in_code = False
        code_lines: list[str] = []
        for raw_line in markdown_text.splitlines():
            line = raw_line.rstrip()
            if line.startswith("```"):
                if in_code:
                    rendered.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                    code_lines = []
                in_code = not in_code
                continue
            if in_code:
                code_lines.append(raw_line)
            elif line.startswith("### "):
                rendered.append(f"<h3>{html.escape(line[4:])}</h3>")
            elif line.startswith("## "):
                rendered.append(f"<h2>{html.escape(line[3:])}</h2>")
            elif line.startswith("# "):
                rendered.append(f"<h1>{html.escape(line[2:])}</h1>")
            elif line.startswith("- "):
                rendered.append(f"<ul><li>{html.escape(line[2:])}</li></ul>")
            elif line.startswith("> "):
                rendered.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
            elif line:
                rendered.append(f"<p>{html.escape(line)}</p>")
        return "\n".join(rendered)


def _find_preview_font() -> str | None:
    """Return a local Unicode/CJK font suitable for the preview renderer."""
    candidates = [
        "/Library/Fonts/NotoSansCJKkr-Regular.otf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        r"C:\Windows\Fonts\malgun.ttf",
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def _render_html_preview_pdf(html_text: str, pdf_path: str, cwd: str) -> bool:
    """Typeset semantic HTML into a readable A4 preview with embedded CJK font."""
    if pymupdf is None or not hasattr(pymupdf, "Story"):
        return False
    font_path = _find_preview_font()
    archive = pymupdf.Archive(os.path.abspath(cwd))
    font_css = ""
    if font_path:
        archive.add(font_path, os.path.basename(font_path))
        font_css = (
            "@font-face { font-family: BookPreview; "
            f"src: url('{os.path.basename(font_path)}'); }}"
        )
    css = font_css + r"""
        body { font-family: BookPreview, sans-serif; font-size: 9.6pt;
               line-height: 1.58; color: #172033; }
        .book-title { font-size: 25pt; color: #143052; margin-top: 95pt;
                      margin-bottom: 22pt; text-align: center; }
        .book-author { font-size: 11pt; color: #475569; text-align: center;
                       margin-bottom: 45pt; }
        .toc { margin: 10pt 0 28pt 0; }
        .toc-heading { color: #143052; font-size: 16pt; margin: 0 0 8pt 0; }
        .toc ul { list-style: none; margin-left: 0; }
        .toc-level-1 { font-weight: bold; margin-top: 5pt; }
        .toc-level-2 { margin-left: 13pt; }
        h1 { color: #143052; font-size: 21pt; line-height: 1.2;
             margin-top: 18pt; padding-top: 3pt; }
        h2 { color: #0f766e; font-size: 17pt; line-height: 1.25;
             margin-top: 16pt; padding-top: 3pt; }
        h3 { color: #143052; font-size: 13pt; margin-top: 17pt; }
        h4, h5, h6 { color: #334155; font-size: 10.5pt; margin-top: 13pt; }
        p { margin: 0 0 7pt 0; text-align: justify; }
        blockquote { margin: 11pt 8pt; padding: 8pt 11pt; background: #f1f5f9;
                     border-left: 3pt solid #0f766e; color: #273449; }
        pre { font-family: monospace; font-size: 7.2pt; line-height: 1.28;
              white-space: pre-wrap; background: #f8fafc; border: 0.5pt solid #cbd5e1;
              padding: 7pt; margin: 8pt 0; }
        code { font-family: monospace; font-size: 0.91em; color: #7c2d12; }
        pre code { color: #172033; }
        table { width: 100%; border-collapse: collapse; margin: 9pt 0 12pt 0;
                font-size: 8pt; page-break-inside: avoid; }
        th { background: #e2e8f0; color: #143052; font-weight: bold; }
        th, td { border: 0.45pt solid #94a3b8; padding: 4pt; vertical-align: top; }
        table.wide-table { font-size: 6.2pt; line-height: 1.18; }
        table.wide-table th, table.wide-table td { padding: 2pt; }
        .preview-table { margin: 10pt 0 13pt 0; }
        .table-caption { color: #475569; font-size: 8pt; text-align: center;
                         margin-bottom: 5pt; }
        .table-record { border: 0.5pt solid #94a3b8; background: #f8fafc;
                        margin-bottom: 5pt; padding: 5pt; font-size: 7.5pt;
                        line-height: 1.3; }
        .table-field { margin-bottom: 2pt; }
        caption, figcaption { color: #475569; font-size: 8pt; margin: 4pt 0 9pt 0; }
        img { max-width: 100%; height: auto; }
        figure { margin: 12pt 0; text-align: center; }
        ul, ol { margin: 4pt 0 8pt 18pt; padding: 0; }
        li { margin-bottom: 3pt; }
        a { color: #0f5f8f; text-decoration: none; }
        .citation { color: #64748b; font-size: 0.86em; }
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
        story = pymupdf.Story(html=html_text, user_css=css, archive=archive)
        writer = pymupdf.DocumentWriter(pdf_path)
        mediabox = pymupdf.paper_rect("a4")
        content_rect = mediabox + (52, 50, -52, -55)
        more = True
        page_count = 0
        while more:
            page_count += 1
            if page_count > 2000:
                raise RuntimeError("Preview exceeded the 2,000 page safety limit.")
            device = writer.begin_page(mediabox)
            more, _ = story.place(content_rect)
            story.draw(device, None)
            writer.end_page()
        writer.close()

        doc = pymupdf.open(pdf_path)
        for page_number, page in enumerate(doc, 1):
            page.insert_text(
                (mediabox.width / 2 - 8, mediabox.height - 26),
                str(page_number),
                fontsize=8,
                color=(0.35, 0.4, 0.48),
            )
        temp_numbered = f"{pdf_path}.numbered.pdf"
        doc.save(temp_numbered, garbage=3, deflate=True)
        doc.close()
        os.replace(temp_numbered, pdf_path)
        return os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000
    except Exception as exc:
        print(f"PyMuPDF Story preview rendering failed: {exc}")
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except OSError:
            pass
        return False


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


def inspect_compiled_pdf_quality(
    log_path: str | None,
    pdf_path: str | None,
    *,
    tex_path: str | None = None,
    max_overfull_pt: float = 20.0,
    max_overfull_vbox_pt: float = 20.0,
) -> dict:
    """Audit compiled TeX log and PDF text for publication-blocking defects."""
    import hashlib

    def _sha256(path: str | None) -> str | None:
        if not path or not os.path.isfile(path):
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    log_text = ""
    stale_log = False
    if log_path and os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
                log_text = log_file.read()
        except OSError:
            log_text = ""
        if tex_path and os.path.isfile(tex_path):
            if os.path.getmtime(log_path) + 1.0 < os.path.getmtime(tex_path):
                stale_log = True
    else:
        stale_log = True

    undefined_refs = len(re.findall(r"Reference `[^']+'[^\n]*undefined", log_text))
    undefined_cites = len(re.findall(r"Citation `[^']+'[^\n]*undefined", log_text))
    multiply_defined = len(
        re.findall(r"multiply[- ]defined", log_text, flags=re.IGNORECASE)
    )
    missing_chars = len(re.findall(r"Missing character:", log_text))
    float_too_large = len(re.findall(r"Float too large for page", log_text))

    overfull_pts = [
        float(match.group(1))
        for match in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", log_text)
    ]
    severe_overfull = [pt for pt in overfull_pts if pt > max_overfull_pt]
    max_observed_overfull = round(max(overfull_pts), 2) if overfull_pts else 0.0

    overfull_vbox_pts = [
        float(match.group(1))
        for match in re.finditer(r"Overfull \\vbox \(([0-9.]+)pt too high\)", log_text)
    ]
    severe_overfull_vbox = [pt for pt in overfull_vbox_pts if pt > max_overfull_vbox_pt]
    max_observed_overfull_vbox = (
        round(max(overfull_vbox_pts), 2) if overfull_vbox_pts else 0.0
    )

    literal_question_marks = 0
    markdown_bold_leaks = 0
    if pdf_path and os.path.isfile(pdf_path):
        extracted_text = load_book(pdf_path, min_size=1) or ""
        literal_question_marks = len(re.findall(r"\?\?", extracted_text))
        markdown_bold_leaks = len(
            re.findall(
                r"(?<!\w)\*\*[^\s*\d][^*\n]{0,80}[^\s*]\*\*(?!\w)",
                extracted_text,
            )
        )

    failures: list[str] = []
    if undefined_refs:
        failures.append(f"undefined_references={undefined_refs}")
    if undefined_cites:
        failures.append(f"undefined_citations={undefined_cites}")
    if multiply_defined:
        failures.append(f"multiply_defined_labels={multiply_defined}")
    if missing_chars:
        failures.append(f"missing_characters={missing_chars}")
    if float_too_large:
        failures.append(f"float_too_large={float_too_large}")
    if severe_overfull:
        failures.append(
            f"severe_overfull_hboxes={len(severe_overfull)} (max={max_observed_overfull}pt > {max_overfull_pt}pt)"
        )
    if severe_overfull_vbox:
        failures.append(
            f"severe_overfull_vboxes={len(severe_overfull_vbox)} (max={max_observed_overfull_vbox}pt > {max_overfull_vbox_pt}pt)"
        )
    if literal_question_marks:
        failures.append(f"literal_question_marks={literal_question_marks}")
    if markdown_bold_leaks:
        failures.append(f"markdown_bold_leaks={markdown_bold_leaks}")
    if stale_log:
        failures.append("stale_or_missing_log=True")

    return {
        "passed": len(failures) == 0,
        "stale_log": stale_log,
        "tex_sha256": _sha256(tex_path),
        "pdf_sha256": _sha256(pdf_path),
        "undefined_references": undefined_refs,
        "undefined_citations": undefined_cites,
        "multiply_defined_labels": multiply_defined,
        "missing_characters": missing_chars,
        "float_too_large": float_too_large,
        "severe_overfull_hboxes": len(severe_overfull),
        "max_overfull_pt": max_observed_overfull,
        "overfull_threshold_pt": max_overfull_pt,
        "severe_overfull_vboxes": len(severe_overfull_vbox),
        "max_overfull_vbox_pt": max_observed_overfull_vbox,
        "overfull_vbox_threshold_pt": max_overfull_vbox_pt,
        "literal_question_marks": literal_question_marks,
        "markdown_bold_leaks": markdown_bold_leaks,
        "failures": failures,
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
    using XeLaTeX, LuaLaTeX, Tectonic, or pdfLaTeX with shell escape disabled.
    If no TeX engine is installed:
      - In `strict_latex=True` (publish mode): fails and returns False.
      - In preview mode: writes Markdown/HTML preview PDF (to `preview_pdf_file` if provided,
        otherwise `pdf_file` for internal intermediate drafts) and sets render_mode='preview'.
    """
    _LAST_COMPILE_STATE.render_mode = "failed"
    _LAST_COMPILE_STATE.preview_backend = None
    source_pdf = os.path.join(cwd, f"{texfile_name}.pdf")
    source_tex = os.path.join(cwd, f"{texfile_name}.tex")
    if os.path.exists(source_pdf):
        os.remove(source_pdf)
    for stale_ext in (".log", ".aux", ".toc"):
        stale_path = os.path.join(cwd, f"{texfile_name}{stale_ext}")
        if os.path.exists(stale_path):
            try:
                os.remove(stale_path)
            except OSError:
                pass
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
    preview_reason = None
    contains_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", tex_content))
    if engine == "pdflatex" and contains_cjk:
        if strict_latex:
            rich.print(
                "[bold red]❌ Publish Mode Error: pdfLaTeX cannot safely typeset this "
                "CJK manuscript. Install XeLaTeX, LuaLaTeX, or Tectonic.[/bold red]"
            )
            return False
        rich.print(
            "[yellow]pdfLaTeX was found but the manuscript contains CJK text; "
            "using the Unicode preview renderer instead.[/yellow]"
        )
        preview_reason = "pdfLaTeX cannot safely typeset the CJK manuscript"
        engine = None
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

        if engine in ("pdflatex", "xelatex", "lualatex"):
            engine_command = engine
            commands = [
                [
                    engine_command,
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
                        engine_command,
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        f"{texfile_name}.tex",
                    ],
                    [
                        engine_command,
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
                    "--untrusted",
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
                    # Older Tectonic releases may not expose --untrusted. The build still
                    # runs inside our isolated, allowlisted sandbox in that case.
                    tectonic_error = (result.stderr or "").lower()
                    if (
                        engine == "tectonic"
                        and result.returncode != 0
                        and "--untrusted" in tectonic_error
                        and any(
                            marker in tectonic_error
                            for marker in (
                                "unexpected argument",
                                "wasn't expected",
                                "unknown option",
                                "unrecognized option",
                            )
                        )
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
            if has_citations and engine in ("pdflatex", "xelatex", "lualatex"):
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
                    sbx_log = os.path.join(sbx_dir, f"{texfile_name}.log")
                    if os.path.exists(sbx_log):
                        shutil.copy2(sbx_log, os.path.join(cwd, f"{texfile_name}.log"))
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
        if strict_latex:
            return False
        preview_reason = f"{engine} compilation failed"
        engine = None

    if strict_latex:
        rich.print(
            "[bold red]❌ Publish Mode Error: No supported TeX engine is installed "
            "(XeLaTeX/LuaLaTeX/Tectonic/pdfLaTeX). "
            "Cannot produce a publication-grade typeset PDF.[/bold red]"
        )
        _LAST_COMPILE_STATE.render_mode = "failed"
        _LAST_COMPILE_STATE.preview_backend = None
        return False

    preview_target = preview_pdf_file or pdf_file
    if preview_reason:
        print(
            f"⚠️ Warning: {preview_reason}; LaTeX syntax validated and non-strict mode is active. "
            f"Rendering non-typeset preview PDF at '{preview_target}'."
        )
    else:
        print(
            f"⚠️ Warning: No TeX engine (XeLaTeX/LuaLaTeX/Tectonic/pdfLaTeX) found. "
            f"LaTeX syntax validated; rendering non-typeset preview PDF at '{preview_target}'."
        )

    # Fallback preview rendering (ONLY when no TeX engine is installed, strict_latex=False,
    # AND validate_latex_syntax passed)
    md_text = latex_to_markdown(tex_content)
    semantic_html = _latex_to_html_with_pandoc(tex_content, cwd)
    preview_backend = "pandoc+pymupdf-story"
    if semantic_html is None:
        semantic_html = _markdown_to_html(md_text)
        preview_backend = "markdown+pymupdf-story"
    if _render_html_preview_pdf(semantic_html, preview_target, cwd):
        _LAST_COMPILE_STATE.render_mode = "preview"
        _LAST_COMPILE_STATE.preview_backend = preview_backend
        return True

    render_minimal_pdf(md_text, preview_target)
    if os.path.exists(preview_target):
        _LAST_COMPILE_STATE.render_mode = "preview"
        _LAST_COMPILE_STATE.preview_backend = "minimal-pdf"
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
