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

import os
import re
import json
import shutil
import struct
import subprocess
import textwrap
import traceback
import zlib

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

    # Object 1: Catalog, Object 2: Pages, Object 3: Font
    # For each page p (0..N-1): Page obj = 4 + 2*p, Content obj = 5 + 2*p
    num_pages = len(pages_lines)
    page_obj_ids = [4 + 2 * i for i in range(num_pages)]
    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {num_pages} >>\nendobj\n".encode(
            "latin-1"
        ),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    for idx, page_lines in enumerate(pages_lines):
        page_id = 4 + 2 * idx
        content_id = 5 + 2 * idx
        stream_cmds = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
        for l_idx, line in enumerate(page_lines):
            escaped = _escape_pdf_text(line)
            if l_idx == 0:
                stream_cmds.append(f"({escaped}) Tj")
            else:
                stream_cmds.append(f"T* ({escaped}) Tj")
        stream_cmds.append("ET")
        stream_bytes = "\n".join(stream_cmds).encode("latin-1")

        page_obj = (
            f"{page_id} 0 obj\n"
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>\n"
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
    trailer = (
        f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
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


def compile_latex(cwd, pdf_file, texfile_name="template", timeout=30):
    source_pdf = os.path.join(cwd, f"{texfile_name}.pdf")
    source_tex = os.path.join(cwd, f"{texfile_name}.tex")
    if os.path.exists(source_pdf):
        os.remove(source_pdf)

    has_pdflatex = shutil.which("pdflatex") is not None
    if has_pdflatex:
        env = dict(os.environ)
        for var in ["TEXINPUTS", "TEXMFHOME", "TEXMF", "TEXMFVAR", "TEXMFCONFIG"]:
            env.pop(var, None)

        commands = [
            ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
            ["bibtex", texfile_name],
            ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
            ["pdflatex", "-interaction=nonstopmode", f"{texfile_name}.tex"],
        ]

        execution_logs = []

        for command in commands:
            cmd_str = " ".join(command)
            try:
                result = subprocess.run(
                    command,
                    cwd=cwd,
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

        if os.path.exists(source_pdf) and not has_timeout:
            try:
                shutil.move(source_pdf, pdf_file)
                return
            except Exception:
                rich.print(
                    f"[bold red]Error moving generated PDF to {pdf_file}[/bold red]"
                )
                print(traceback.format_exc())
        else:
            rich.print(
                f"\n[bold red]❌ PDF Generation Failed via pdflatex[/bold red]"
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
    else:
        print(
            "⚠️ Warning: 'pdflatex' binary not found on system. Rendering preview PDF via fallback renderer."
        )

    # Fallback rendering from .tex -> markdown -> clean multi-page preview PDF
    if os.path.exists(source_tex):
        with open(source_tex, "r", encoding="utf-8") as f:
            tex_content = f.read()
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
                writer = pymupdf.DocumentWriter(pdf_file)
                mediabox = pymupdf.paper_rect("letter")
                where = mediabox + (54, 54, -54, -54)
                more = True
                while more:
                    dev = writer.begin_page(mediabox)
                    more, _ = story.place(where)
                    story.draw(dev, None)
                    writer.end_page()
                writer.close()
                return
            except Exception as e:
                print(f"PyMuPDF Story fallback failed, using pure-Python PDF writer: {e}")

        render_minimal_pdf(md_text, pdf_file)


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
