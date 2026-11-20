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

"""Turn a PPTX or slide PDF into grounded BookOrchestra source materials."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from utils.path_security import (
    detect_media_format,
    is_within_directory,
    resolve_path,
    safe_prepare_managed_subdir,
    sanitize_slug,
)


_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS = {"p": _P, "a": _A, "r": _R, "pr": _PR, "c": _C}

_MAX_SOURCE_BYTES = 150 * 1024 * 1024
_MAX_ZIP_MEMBERS = 5000
_MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_PDF_PAGES = 1000
_SUPPORTED_SOURCE_EXTENSIONS = {".pptx", ".pdf"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_text(value: Any) -> str:
    return re.sub(r"[\t\r ]+", " ", str(value or "")).strip()


def _unique_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for raw_line in lines:
        line = _clean_text(raw_line)
        if line and (not result or result[-1] != line):
            result.append(line)
    return result


def _xml_from_zip(package: zipfile.ZipFile, member: str) -> ET.Element:
    try:
        info = package.getinfo(member)
    except KeyError as exc:
        raise ValueError(f"PPTX package is missing required member '{member}'.") from exc
    if info.file_size > _MAX_XML_BYTES:
        raise ValueError(f"PPTX XML member '{member}' is unreasonably large.")
    try:
        return ET.fromstring(package.read(member))
    except ET.ParseError as exc:
        raise ValueError(f"PPTX XML member '{member}' is malformed.") from exc


def _relationship_map(package: zipfile.ZipFile, owner_path: str) -> dict[str, dict[str, str]]:
    owner_dir = posixpath.dirname(owner_path)
    rels_path = posixpath.join(owner_dir, "_rels", posixpath.basename(owner_path) + ".rels")
    if rels_path not in package.namelist():
        return {}
    root = _xml_from_zip(package, rels_path)
    result: dict[str, dict[str, str]] = {}
    for rel in root.findall(f"{{{_PR}}}Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if not rel_id or not target or rel.get("TargetMode") == "External":
            continue
        resolved = posixpath.normpath(posixpath.join(owner_dir, target))
        if resolved.startswith("../") or resolved.startswith("/"):
            continue
        result[rel_id] = {
            "type": rel.get("Type", ""),
            "target": resolved,
        }
    return result


def _ordered_slide_paths(package: zipfile.ZipFile) -> list[str]:
    """Return the presentation's real slide order, falling back for minimal packages."""
    numbered_paths = sorted(
        (
            name
            for name in package.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ),
        key=lambda name: int(re.search(r"(\d+)", posixpath.basename(name)).group(1)),
    )
    presentation_path = "ppt/presentation.xml"
    if presentation_path in package.namelist():
        root = _xml_from_zip(package, presentation_path)
        relationships = _relationship_map(package, presentation_path)
        ordered: list[str] = []
        for slide_id in root.findall(".//p:sldIdLst/p:sldId", _NS):
            rel_id = slide_id.get(f"{{{_R}}}id")
            rel = relationships.get(rel_id or "", {})
            target = rel.get("target", "")
            if (
                rel.get("type", "").endswith("/slide")
                and target in package.namelist()
                and target not in ordered
            ):
                ordered.append(target)
        if ordered:
            return ordered + [path for path in numbered_paths if path not in ordered]
    return numbered_paths


def _shape_paragraphs(shape: ET.Element) -> list[str]:
    return _unique_lines(
        [
            "".join(node.text or "" for node in paragraph.findall(".//a:t", _NS))
            for paragraph in shape.findall(".//a:p", _NS)
        ]
    )


def _slide_title(root: ET.Element, text_lines: list[str], slide_number: int) -> str:
    for shape in root.findall(".//p:sp", _NS):
        placeholder = shape.find("./p:nvSpPr/p:nvPr/p:ph", _NS)
        if placeholder is not None and placeholder.get("type", "") in {"title", "ctrTitle"}:
            title_lines = _shape_paragraphs(shape)
            if title_lines:
                return title_lines[0]
    return text_lines[0] if text_lines else f"Slide {slide_number}"


def _extract_slide_tables(root: ET.Element) -> list[str]:
    tables: list[str] = []
    for table_index, table in enumerate(root.findall(".//a:tbl", _NS), start=1):
        rows: list[list[str]] = []
        for row in table.findall("./a:tr", _NS):
            cells = []
            for cell in row.findall("./a:tc", _NS):
                value = " ".join(_shape_paragraphs(cell)).replace("|", "\\|")
                cells.append(value)
            if cells:
                rows.append(cells)
        if not rows:
            continue
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        header = padded[0]
        body = padded[1:]
        markdown = [f"Table {table_index}:", "| " + " | ".join(header) + " |"]
        markdown.append("| " + " | ".join(["---"] * width) + " |")
        markdown.extend("| " + " | ".join(row) + " |" for row in body)
        tables.append("\n".join(markdown))
    return tables


def _extract_notes(package: zipfile.ZipFile, relationships: dict[str, dict[str, str]]) -> str:
    notes_target = next(
        (
            rel["target"]
            for rel in relationships.values()
            if rel["type"].endswith("/notesSlide") and rel["target"] in package.namelist()
        ),
        None,
    )
    if not notes_target:
        return ""
    root = _xml_from_zip(package, notes_target)
    note_lines: list[str] = []
    for shape in root.findall(".//p:sp", _NS):
        placeholder = shape.find("./p:nvSpPr/p:nvPr/p:ph", _NS)
        placeholder_type = placeholder.get("type", "body") if placeholder is not None else "body"
        if placeholder_type in {"sldImg", "sldNum", "dt", "ftr", "hdr"}:
            continue
        note_lines.extend(_shape_paragraphs(shape))
    return "\n".join(_unique_lines(note_lines))


def _extract_chart_text(
    package: zipfile.ZipFile, relationships: dict[str, dict[str, str]]
) -> list[str]:
    charts: list[str] = []
    for rel in relationships.values():
        target = rel["target"]
        if not rel["type"].endswith("/chart") or target not in package.namelist():
            continue
        root = _xml_from_zip(package, target)
        values = _unique_lines(
            [node.text or "" for node in root.findall(".//a:t", _NS)]
            + [node.text or "" for node in root.findall(".//c:v", _NS)]
        )
        if values:
            charts.append("Chart labels and cached values: " + ", ".join(values[:500]))
    return charts


def _extract_related_diagram_text(
    package: zipfile.ZipFile, relationships: dict[str, dict[str, str]]
) -> list[str]:
    diagrams: list[str] = []
    for rel in relationships.values():
        target = rel["target"]
        if not rel["type"].endswith("/diagramData") or target not in package.namelist():
            continue
        root = _xml_from_zip(package, target)
        values = _unique_lines([node.text or "" for node in root.findall(".//a:t", _NS)])
        if values:
            diagrams.append("SmartArt / diagram text: " + " | ".join(values))
    return diagrams


def _write_figure(
    figures_dir: Path,
    raw_bytes: bytes,
    slide_number: int,
    ordinal: int,
) -> str | None:
    detected_extension = detect_media_format(raw_bytes)
    if detected_extension is None and b"<svg" in raw_bytes.lstrip()[:2048].lower():
        try:
            import fitz

            svg_document = fitz.open(stream=raw_bytes, filetype="svg")
            try:
                pixmap = svg_document.load_page(0).get_pixmap(
                    matrix=fitz.Matrix(2, 2), alpha=True
                )
                raw_bytes = pixmap.tobytes("png")
                detected_extension = ".png"
            finally:
                svg_document.close()
        except Exception:
            return None
    if detected_extension not in {".png", ".jpg", ".pdf"}:
        return None
    filename = f"slide_{slide_number:03d}_visual_{ordinal:02d}{detected_extension}"
    destination = figures_dir / filename
    destination.write_bytes(raw_bytes)
    return filename


def _extract_pptx(source_path: Path, figures_dir: Path) -> tuple[list[dict], list[dict]]:
    slides: list[dict] = []
    figure_info: list[dict] = []
    try:
        package = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("The supplied .pptx file is not a valid PowerPoint package.") from exc

    with package:
        infos = package.infolist()
        if len(infos) > _MAX_ZIP_MEMBERS:
            raise ValueError("The PPTX package contains too many members.")
        if sum(item.file_size for item in infos) > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("The PPTX package expands beyond the safety limit.")
        if any(item.flag_bits & 0x1 for item in infos):
            raise ValueError("Encrypted PPTX package members are not supported.")

        slide_paths = _ordered_slide_paths(package)
        if not slide_paths:
            raise ValueError("The PPTX contains no readable slides.")

        extracted_hashes: dict[str, str] = {}
        for slide_number, slide_path in enumerate(slide_paths, start=1):
            root = _xml_from_zip(package, slide_path)
            relationships = _relationship_map(package, slide_path)
            text_lines: list[str] = []
            for shape in root.findall(".//p:sp", _NS):
                text_lines.extend(_shape_paragraphs(shape))
            text_lines = _unique_lines(text_lines)
            tables = _extract_slide_tables(root)
            chart_text = _extract_chart_text(package, relationships)
            diagram_text = _extract_related_diagram_text(package, relationships)
            visual_names: list[str] = []
            unsupported_visual_count = 0
            seen_rel_ids: set[str] = set()
            visual_ordinal = 0
            for blip in root.findall(".//a:blip", _NS):
                rel_id = blip.get(f"{{{_R}}}embed")
                if not rel_id or rel_id in seen_rel_ids:
                    continue
                seen_rel_ids.add(rel_id)
                rel = relationships.get(rel_id, {})
                target = rel.get("target", "")
                if not rel.get("type", "").endswith("/image") or target not in package.namelist():
                    continue
                raw_bytes = package.read(target)
                content_hash = hashlib.sha256(raw_bytes).hexdigest()
                if content_hash in extracted_hashes:
                    visual_names.append(extracted_hashes[content_hash])
                    continue
                visual_ordinal += 1
                figure_name = _write_figure(
                    figures_dir, raw_bytes, slide_number, visual_ordinal
                )
                if not figure_name:
                    unsupported_visual_count += 1
                    continue
                extracted_hashes[content_hash] = figure_name
                visual_names.append(figure_name)
                figure_info.append(
                    _figure_metadata(figure_name, slide_number, _slide_title(root, text_lines, slide_number))
                )

            slides.append(
                {
                    "slide_number": slide_number,
                    "source_locator": f"slide {slide_number}",
                    "title": _slide_title(root, text_lines, slide_number),
                    "visible_text": text_lines,
                    "tables": tables,
                    "chart_data": chart_text,
                    "diagram_text": diagram_text,
                    "speaker_notes": _extract_notes(package, relationships),
                    "figures": list(dict.fromkeys(visual_names)),
                    "unsupported_visual_count": unsupported_visual_count,
                }
            )
    return slides, figure_info


def _extract_pdf(source_path: Path, figures_dir: Path) -> tuple[list[dict], list[dict]]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to ingest slide PDFs.") from exc

    try:
        document = fitz.open(source_path)
    except Exception as exc:
        raise ValueError("The supplied PDF cannot be opened.") from exc

    slides: list[dict] = []
    figure_info: list[dict] = []
    extracted_hashes: dict[str, str] = {}
    try:
        if document.needs_pass:
            raise ValueError("Password-protected slide PDFs are not supported.")
        if document.page_count > _MAX_PDF_PAGES:
            raise ValueError(f"Slide PDF exceeds the {_MAX_PDF_PAGES}-page safety limit.")
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            slide_number = page_index + 1
            text_lines = _unique_lines(page.get_text("text").splitlines())
            title = text_lines[0] if text_lines else f"Slide {slide_number}"
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            visual_names: list[str] = []
            visual_ordinal = 0
            seen_xrefs: set[int] = set()
            for image_tuple in page.get_images(full=True):
                xref = int(image_tuple[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                rects = page.get_image_rects(xref)
                largest_area = max(
                    (float(rect.width * rect.height) for rect in rects), default=0.0
                )
                if largest_area / page_area < 0.04:
                    continue
                try:
                    image_data = document.extract_image(xref).get("image", b"")
                    if detect_media_format(image_data) not in {".png", ".jpg"}:
                        image_data = fitz.Pixmap(document, xref).tobytes("png")
                except Exception:
                    continue
                content_hash = hashlib.sha256(image_data).hexdigest()
                if content_hash in extracted_hashes:
                    visual_names.append(extracted_hashes[content_hash])
                    continue
                visual_ordinal += 1
                figure_name = _write_figure(
                    figures_dir, image_data, slide_number, visual_ordinal
                )
                if not figure_name:
                    continue
                extracted_hashes[content_hash] = figure_name
                visual_names.append(figure_name)
                figure_info.append(_figure_metadata(figure_name, slide_number, title))
            slides.append(
                {
                    "slide_number": slide_number,
                    "source_locator": f"page {slide_number}",
                    "title": title,
                    "visible_text": text_lines,
                    "tables": [],
                    "chart_data": [],
                    "diagram_text": [],
                    "speaker_notes": "",
                    "figures": list(dict.fromkeys(visual_names)),
                    "unsupported_visual_count": 0,
                }
            )
    finally:
        document.close()
    return slides, figure_info


def _figure_metadata(filename: str, slide_number: int, slide_title: str) -> dict:
    readable_title = slide_title or f"Slide {slide_number} visual"
    return {
        "figure_id": sanitize_slug(Path(filename).stem, default=f"slide_{slide_number}_visual"),
        "name": filename,
        "title": f"{readable_title} — source visual",
        "caption": f"Visual extracted from source slide {slide_number}: {readable_title}",
        "chapter_title": "",
        "placement_hint": f"Near the explanation grounded in source slide {slide_number}",
        "pedagogical_purpose": "Preserve and explain the visual evidence from the lecture slides",
        "source_slide": slide_number,
        "generated_by": "slide_ingestion",
        "status": "ready",
    }


def _build_proposal(
    source_name: str,
    title: str,
    slides: list[dict],
    target_audience: str,
    book_style: str,
    author_voice: str,
) -> str:
    outline = "\n".join(
        f"- {slide['source_locator']}: {slide['title']}" for slide in slides
    )
    return f"""# Book Proposal: {title}

## Source and purpose

Turn the lecture deck `{source_name}` into a self-contained explanatory book. Preserve the
deck's factual meaning and sequence, but do not write a slide-by-slide transcript. Group
adjacent slides into coherent chapters and make every transition understandable to a reader
who was not present for the lecture.

## Reader and editorial profile

- Target audience: {target_audience}
- Book style: {book_style}
- Author voice: {author_voice}
- Language: write in the primary language used by the source slides unless the user explicitly requests another language.

## Binding explanation contract

- Expand compressed bullets into complete explanations of the premise, mechanism, and consequence.
- Define a term before relying on it. Explain prerequisites that the deck assumes.
- Use small worked examples, concrete analogies, and brief comprehension checks where they reduce confusion.
- Explain every included source visual: what to notice, how to read it, and why it matters.
- Preserve the lecturer's examples and speaker-note context, while clearly separating sourced facts from new illustrative examples.
- Do not invent missing measurements, claims, citations, code behavior, or speaker intent. Mark genuine gaps as gaps.
- Keep source slide/page locators available during planning and review so claims can be traced back to the deck.
- Avoid stiff reference-manual prose, repetitive bullet expansion, and needless jargon. Vary paragraph length and pacing.

## Source sequence

{outline}
"""


def _build_notes(slides: list[dict]) -> str:
    sections = [
        "# Slide-grounded technical notes",
        "",
        "The entries below are source evidence. Preserve slide order when grouping them into chapters.",
        "Visible text, speaker notes, tables, chart caches, and extracted figures remain distinct so",
        "the author can judge what the audience actually saw and what the lecturer supplied as context.",
    ]
    for slide in slides:
        sections.extend(
            [
                "",
                f"## Source {slide['source_locator']}: {slide['title']}",
                "",
                f"Source locator: `{slide['source_locator']}`",
                "",
                "### Visible slide text",
                "",
            ]
        )
        visible_text = slide.get("visible_text") or []
        sections.extend(f"- {line}" for line in visible_text)
        if not visible_text:
            sections.append("- No extractable text. Use the source visual as the evidence and do not infer hidden claims.")
        if slide.get("tables"):
            sections.extend(["", "### Tables", "", "\n\n".join(slide["tables"])])
        if slide.get("chart_data"):
            sections.extend(["", "### Chart labels and cached values", ""])
            sections.extend(f"- {item}" for item in slide["chart_data"])
        if slide.get("diagram_text"):
            sections.extend(["", "### SmartArt and related diagram text", ""])
            sections.extend(f"- {item}" for item in slide["diagram_text"])
        sections.extend(["", "### Speaker notes", ""])
        sections.append(slide.get("speaker_notes") or "No speaker notes were available.")
        sections.extend(["", "### Extracted source visuals", ""])
        figures = slide.get("figures") or []
        sections.extend(f"- `figures/{name}`" for name in figures)
        if not figures:
            sections.append("- No reusable raster/PDF visual was extracted from this slide.")
    return "\n".join(sections).rstrip() + "\n"


def prepare_slide_materials(
    slides_file: str | os.PathLike,
    output_dir: str | os.PathLike,
    *,
    target_audience: str = "Senior Software & Systems Engineers",
    book_style: str = "Friendly, example-rich technical explanation",
    author_voice: str = "Warm, patient, reader-facing, and technically precise",
    title: str | None = None,
) -> dict:
    """Extract slides and create a managed raw-materials directory for BookOrchestra."""
    source_path = resolve_path(slides_file)
    materials_dir = resolve_path(output_dir)
    if not source_path.is_file():
        raise ValueError(f"Slide source '{slides_file}' does not exist or is not a file.")
    extension = source_path.suffix.lower()
    if extension not in _SUPPORTED_SOURCE_EXTENSIONS:
        raise ValueError("Slide source must be a .pptx or .pdf file.")
    if source_path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("Slide source exceeds the 150 MiB safety limit.")
    if is_within_directory(source_path, materials_dir):
        raise ValueError("Slide source must be outside the generated raw-materials directory.")

    safe_prepare_managed_subdir(materials_dir, role="slide_materials")
    figures_dir = materials_dir / "figures"
    source_dir = materials_dir / "source_slides"
    figures_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    if extension == ".pptx":
        slides, figure_info = _extract_pptx(source_path, figures_dir)
        source_type = "pptx"
    else:
        slides, figure_info = _extract_pdf(source_path, figures_dir)
        source_type = "pdf"

    inferred_title = title or next(
        (slide["title"] for slide in slides if slide.get("title")), source_path.stem
    )
    safe_source_name = f"{sanitize_slug(source_path.stem, default='lecture_slides')}{extension}"
    copied_source = source_dir / safe_source_name
    shutil.copy2(source_path, copied_source)

    manifest = {
        "schema_version": 1,
        "source_file": source_path.name,
        "preserved_source": f"source_slides/{safe_source_name}",
        "source_type": source_type,
        "source_sha256": _sha256(source_path),
        "book_title": inferred_title,
        "slide_count": len(slides),
        "extracted_figure_count": len(figure_info),
        "slides": slides,
    }
    (materials_dir / "book_proposal.md").write_text(
        _build_proposal(
            source_path.name,
            inferred_title,
            slides,
            target_audience,
            book_style,
            author_voice,
        ),
        encoding="utf-8",
    )
    (materials_dir / "technical_notes.md").write_text(
        _build_notes(slides), encoding="utf-8"
    )
    (materials_dir / "slide_source_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (figures_dir / "info.json").write_text(
        json.dumps(figure_info, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "raw_materials_dir": str(materials_dir),
        "proposal_path": str(materials_dir / "book_proposal.md"),
        "technical_notes_path": str(materials_dir / "technical_notes.md"),
        "manifest_path": str(materials_dir / "slide_source_manifest.json"),
        "slide_count": len(slides),
        "extracted_figure_count": len(figure_info),
    }
