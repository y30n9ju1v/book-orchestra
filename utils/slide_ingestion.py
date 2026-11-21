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
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from utils.path_security import (
    detect_media_format,
    is_within_directory,
    resolve_path,
    safe_replace_managed_dir,
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
_MAX_EMBEDDED_VISUAL_BYTES = 50 * 1024 * 1024
_MAX_PDF_PAGES = 1000
_MAX_PDF_TEXT_CHARS_PER_PAGE = 2 * 1024 * 1024
_MAX_PDF_ANNOTATION_CHARS_PER_PAGE = 64 * 1024
_MAX_PDF_TABLE_CHARS_PER_PAGE = 1024 * 1024
_MAX_PDF_TABLE_CELLS_PER_PAGE = 10_000
_MAX_PDF_LINKS_PER_PAGE = 100
_MAX_PDF_LINK_CHARS = 8192
_MAX_RASTER_DIMENSION = 4096
_MAX_RASTER_PIXELS = 16 * 1024 * 1024
_SUPPORTED_SOURCE_EXTENSIONS = {".pptx", ".pdf"}
_ZIP_READ_TOTAL_ATTR = "_book_orchestra_actual_uncompressed_bytes"
_ZIP_MEMBER_NAMES_ATTR = "_book_orchestra_member_names"
_MONOSPACE_FONT_RE = re.compile(
    r"(?:mono|courier|consolas|menlo|monaco|inconsolata|fira\s*code|jetbrains|"
    r"source\s*code|hack|d2coding|nanumgothiccoding|fixed|terminal|typewriter|cmtt|lmtt)",
    re.IGNORECASE,
)
_PAGE_NUMBER_MARGIN_RE = re.compile(
    r"^(?:page\s*)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?$|^-\s*\d{1,4}\s*-$",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_text(value: Any) -> str:
    return re.sub(r"[\t\r\u00a0 ]+", " ", str(value or "")).strip()


def _unique_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for raw_line in lines:
        line = _clean_text(raw_line)
        if line and (not result or result[-1] != line):
            result.append(line)
    return result


def _read_zip_member_bounded(
    package: zipfile.ZipFile, member: str, max_bytes: int
) -> bytes:
    try:
        info = package.getinfo(member)
    except KeyError as exc:
        raise ValueError(f"PPTX package is missing required member '{member}'.") from exc
    if info.file_size > max_bytes:
        raise ValueError(f"PPTX member '{member}' exceeds the extraction safety limit.")
    try:
        with package.open(info, "r") as member_stream:
            data = member_stream.read(max_bytes + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"PPTX member '{member}' could not be read safely.") from exc
    if len(data) > max_bytes:
        raise ValueError(f"PPTX member '{member}' exceeds the extraction safety limit.")
    total_read = int(getattr(package, _ZIP_READ_TOTAL_ATTR, 0)) + len(data)
    if total_read > _MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError("PPTX extraction exceeded the cumulative safety limit.")
    setattr(package, _ZIP_READ_TOTAL_ATTR, total_read)
    return data


def _zip_member_names(package: zipfile.ZipFile) -> set[str]:
    member_names = getattr(package, _ZIP_MEMBER_NAMES_ATTR, None)
    if member_names is None:
        member_names = set(package.namelist())
        setattr(package, _ZIP_MEMBER_NAMES_ATTR, member_names)
    return member_names


def _xml_from_zip(package: zipfile.ZipFile, member: str) -> ET.Element:
    try:
        return ET.fromstring(_read_zip_member_bounded(package, member, _MAX_XML_BYTES))
    except ET.ParseError as exc:
        raise ValueError(f"PPTX XML member '{member}' is malformed.") from exc


def _relationship_map(package: zipfile.ZipFile, owner_path: str) -> dict[str, dict[str, str]]:
    owner_dir = posixpath.dirname(owner_path)
    rels_path = posixpath.join(owner_dir, "_rels", posixpath.basename(owner_path) + ".rels")
    if rels_path not in _zip_member_names(package):
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
            for name in _zip_member_names(package)
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ),
        key=lambda name: int(re.search(r"(\d+)", posixpath.basename(name)).group(1)),
    )
    presentation_path = "ppt/presentation.xml"
    if presentation_path in _zip_member_names(package):
        root = _xml_from_zip(package, presentation_path)
        relationships = _relationship_map(package, presentation_path)
        ordered: list[str] = []
        for slide_id in root.findall(".//p:sldIdLst/p:sldId", _NS):
            rel_id = slide_id.get(f"{{{_R}}}id")
            rel = relationships.get(rel_id or "", {})
            target = rel.get("target", "")
            if (
                rel.get("type", "").endswith("/slide")
                and target in _zip_member_names(package)
                and target not in ordered
            ):
                ordered.append(target)
        if ordered:
            return ordered + [path for path in numbered_paths if path not in ordered]
    return numbered_paths


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{_A}}}t":
            parts.append(node.text or "")
        elif node.tag in {f"{{{_A}}}br", f"{{{_A}}}tab"}:
            parts.append(" ")
    return _clean_text("".join(parts))


def _shape_paragraphs(shape: ET.Element) -> list[str]:
    return _unique_lines(
        [_paragraph_text(paragraph) for paragraph in shape.findall(".//a:p", _NS)]
    )


def _layout_placeholder_types(
    package: zipfile.ZipFile,
    relationships: dict[str, dict[str, str]],
    layout_cache: dict[str, dict[str, str]],
) -> dict[str, str]:
    layout_target = next(
        (
            rel["target"]
            for rel in relationships.values()
            if rel["type"].endswith("/slideLayout")
            and rel["target"] in _zip_member_names(package)
        ),
        None,
    )
    if not layout_target:
        return {}
    if layout_target in layout_cache:
        return layout_cache[layout_target]
    root = _xml_from_zip(package, layout_target)
    placeholder_types: dict[str, str] = {}
    for shape in root.findall(".//p:sp", _NS):
        placeholder = shape.find("./p:nvSpPr/p:nvPr/p:ph", _NS)
        if placeholder is None:
            continue
        placeholder_type = placeholder.get("type", "")
        if not placeholder_type:
            continue
        placeholder_index = placeholder.get("idx", "")
        existing_type = placeholder_types.get(placeholder_index, "")
        if existing_type in {"title", "ctrTitle"} and placeholder_type not in {
            "title",
            "ctrTitle",
        }:
            continue
        placeholder_types[placeholder_index] = placeholder_type
    layout_cache[layout_target] = placeholder_types
    return placeholder_types


def _slide_title(
    root: ET.Element,
    text_lines: list[str],
    slide_number: int,
    inherited_placeholder_types: dict[str, str] | None = None,
) -> str:
    inherited_placeholder_types = inherited_placeholder_types or {}
    named_title_candidates: list[str] = []
    for shape in root.findall(".//p:sp", _NS):
        placeholder = shape.find("./p:nvSpPr/p:nvPr/p:ph", _NS)
        placeholder_type = ""
        if placeholder is not None:
            placeholder_type = placeholder.get("type", "") or inherited_placeholder_types.get(
                placeholder.get("idx", ""), ""
            )
        if placeholder_type in {"title", "ctrTitle"}:
            title_lines = _shape_paragraphs(shape)
            if title_lines:
                return title_lines[0]
        name_node = shape.find("./p:nvSpPr/p:cNvPr", _NS)
        shape_name = name_node.get("name", "") if name_node is not None else ""
        if re.search(r"(?:^|\s)(?:title|제목|titre|titel)(?:\s|$)", shape_name, re.I):
            named_title_candidates.extend(_shape_paragraphs(shape))
    if named_title_candidates:
        return named_title_candidates[0]
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
            if rel["type"].endswith("/notesSlide")
            and rel["target"] in _zip_member_names(package)
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
    def cached_values(container: ET.Element | None) -> dict[int, str]:
        if container is None:
            return {}
        indexed_values: dict[int, str] = {}
        for point in container.findall(".//c:pt", _NS):
            raw_index = point.get("idx", "0")
            try:
                point_index = int(raw_index)
            except ValueError:
                point_index = len(indexed_values)
            value_node = point.find("./c:v", _NS)
            if value_node is None:
                value_node = point.find(".//a:t", _NS)
            value = _clean_text(value_node.text if value_node is not None else "")
            indexed_values[point_index] = value
        if indexed_values:
            return dict(sorted(indexed_values.items())[:500])
        raw_values = [node.text or "" for node in container.findall(".//c:v", _NS)]
        if not raw_values:
            raw_values = [node.text or "" for node in container.findall(".//a:t", _NS)]
        return {index: _clean_text(value) for index, value in enumerate(raw_values[:500])}

    charts: list[str] = []
    for rel in relationships.values():
        target = rel["target"]
        if not rel["type"].endswith("/chart") or target not in _zip_member_names(package):
            continue
        root = _xml_from_zip(package, target)
        chart_title = " ".join(
            _unique_lines(
                [node.text or "" for node in root.findall("./c:chart/c:title//a:t", _NS)]
            )
        )
        for series_index, series in enumerate(root.findall(".//c:ser", _NS), start=1):
            series_name_values = cached_values(series.find("./c:tx", _NS))
            series_name = next(
                (value for value in series_name_values.values() if value),
                f"Series {series_index}",
            )
            categories = cached_values(series.find("./c:cat", _NS))
            values = cached_values(series.find("./c:val", _NS))
            if not values:
                x_values = cached_values(series.find("./c:xVal", _NS))
                y_values = cached_values(series.find("./c:yVal", _NS))
                if x_values and y_values:
                    pairs = [
                        f"x={x_values[point_index]}, y={y_values[point_index]}"
                        for point_index in sorted(x_values.keys() & y_values.keys())
                    ]
                    charts.append(
                        f"{chart_title + ' — ' if chart_title else ''}{series_name}: "
                        + "; ".join(pairs)
                    )
                continue
            if categories:
                pairs = []
                for point_index, value in sorted(values.items()):
                    category = categories.get(point_index)
                    if category is None:
                        category = f"Point {point_index + 1}"
                    elif not category:
                        category = "(blank)"
                    pairs.append(f"{category} = {value}")
                chart_values = "; ".join(pairs)
            else:
                chart_values = "; ".join(
                    value for _, value in sorted(values.items())
                )
            charts.append(
                f"{chart_title + ' — ' if chart_title else ''}{series_name}: {chart_values}"
            )
    return charts


def _extract_related_diagram_text(
    package: zipfile.ZipFile, relationships: dict[str, dict[str, str]]
) -> list[str]:
    diagrams: list[str] = []
    for rel in relationships.values():
        target = rel["target"]
        if not rel["type"].endswith("/diagramData") or target not in _zip_member_names(
            package
        ):
            continue
        root = _xml_from_zip(package, target)
        values = _unique_lines([node.text or "" for node in root.findall(".//a:t", _NS)])
        if values:
            diagrams.append("SmartArt / diagram text: " + " | ".join(values))
    return diagrams


def _bounded_render_scale(
    width: float, height: float, preferred_scale: float = 2.0
) -> float:
    if width <= 0 or height <= 0:
        raise ValueError("Visual canvas has invalid dimensions.")
    dimension_scale = min(
        _MAX_RASTER_DIMENSION / width,
        _MAX_RASTER_DIMENSION / height,
    )
    pixel_scale = (_MAX_RASTER_PIXELS / (width * height)) ** 0.5
    scale = min(preferred_scale, dimension_scale, pixel_scale)
    if scale < 0.0001:
        raise ValueError("Visual canvas dimensions are too large to rasterize safely.")
    return scale


def _render_fitz_page_png(page, fitz_module, preferred_scale: float = 2.0) -> bytes:
    scale = _bounded_render_scale(
        float(page.rect.width), float(page.rect.height), preferred_scale
    )
    pixmap = page.get_pixmap(
        matrix=fitz_module.Matrix(scale, scale), colorspace=fitz_module.csRGB, alpha=False
    )
    return pixmap.tobytes("png")


def _extract_pdf_image_bytes(document, fitz_module, image_tuple: tuple) -> bytes:
    xref = int(image_tuple[0])
    smask_xref = int(image_tuple[1] or 0)
    width = int(image_tuple[2] or 0)
    height = int(image_tuple[3] or 0)
    if (
        width <= 0
        or height <= 0
        or width > _MAX_RASTER_DIMENSION * 4
        or height > _MAX_RASTER_DIMENSION * 4
        or width * height > _MAX_RASTER_PIXELS
    ):
        raise ValueError("Embedded PDF image exceeds the raster safety limit.")

    image_info = document.extract_image(xref)
    extracted = image_info.get("image", b"")
    detected_format = detect_media_format(extracted)
    colorspace_metadata = image_info.get("colorspace")
    colorspace_name = str(image_info.get("cs-name") or "").lower()
    ordinary_colorspace = colorspace_metadata in {1, 3} and not any(
        marker in colorspace_name for marker in ("cmyk", "indexed", "separation")
    )
    if not smask_xref and ordinary_colorspace and detected_format in {".png", ".jpg"}:
        return extracted

    base_pixmap = fitz_module.Pixmap(document, xref)
    base_components = (
        base_pixmap.colorspace.n if base_pixmap.colorspace is not None else 0
    )
    if base_components != 3:
        base_pixmap = fitz_module.Pixmap(fitz_module.csRGB, base_pixmap)

    pixmap = base_pixmap
    if smask_xref:
        try:
            mask_pixmap = fitz_module.Pixmap(document, smask_xref)
            pixmap = fitz_module.Pixmap(base_pixmap, mask_pixmap)
        except Exception:
            pixmap = base_pixmap
    return pixmap.tobytes("png")


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
                raw_bytes = _render_fitz_page_png(
                    svg_document.load_page(0), fitz, preferred_scale=2.0
                )
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
        setattr(package, _ZIP_READ_TOTAL_ATTR, 0)
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
        layout_cache: dict[str, dict[str, str]] = {}
        for slide_number, slide_path in enumerate(slide_paths, start=1):
            root = _xml_from_zip(package, slide_path)
            relationships = _relationship_map(package, slide_path)
            inherited_placeholder_types = _layout_placeholder_types(
                package, relationships, layout_cache
            )
            hidden = root.get("show", "1").strip().lower() in {"0", "false", "off"}
            text_lines: list[str] = []
            for shape in root.findall(".//p:sp", _NS):
                text_lines.extend(_shape_paragraphs(shape))
            text_lines = _unique_lines(text_lines)
            slide_title = _slide_title(
                root,
                text_lines,
                slide_number,
                inherited_placeholder_types,
            )
            tables = _extract_slide_tables(root)
            chart_text = _extract_chart_text(package, relationships)
            diagram_text = _extract_related_diagram_text(package, relationships)
            visual_names: list[str] = []
            unsupported_visual_count = 0
            seen_rel_ids: set[str] = set()
            visual_ordinal = 0
            for blip in root.findall(".//a:blip", _NS):
                if hidden:
                    break
                rel_id = blip.get(f"{{{_R}}}embed")
                if not rel_id or rel_id in seen_rel_ids:
                    continue
                seen_rel_ids.add(rel_id)
                rel = relationships.get(rel_id, {})
                target = rel.get("target", "")
                if not rel.get("type", "").endswith("/image") or target not in _zip_member_names(
                    package
                ):
                    continue
                raw_bytes = _read_zip_member_bounded(
                    package, target, _MAX_EMBEDDED_VISUAL_BYTES
                )
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
                    _figure_metadata(figure_name, slide_number, slide_title)
                )

            slides.append(
                {
                    "slide_number": slide_number,
                    "source_locator": f"slide {slide_number}",
                    "title": slide_title,
                    "hidden": hidden,
                    "included_in_book": not hidden,
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


def _is_monospace_span(span: dict) -> bool:
    flags = int(span.get("flags", 0) or 0)
    if flags & 8:  # PyMuPDF TEXT_FONT_MONOSPACED bit
        return True
    font_name = str(span.get("font") or "")
    return bool(_MONOSPACE_FONT_RE.search(font_name))


def _extract_pdf_lines(page) -> list[dict]:
    """Extract positioned text lines with font size and monospace metadata."""
    try:
        try:
            page_dict = page.get_text("dict", sort=True)
        except TypeError:
            # Test doubles and older PyMuPDF versions may not expose ``sort``.
            page_dict = page.get_text("dict")
    except Exception:
        page_dict = None
    if not isinstance(page_dict, dict) or not isinstance(page_dict.get("blocks"), list):
        try:
            fallback_text = page.get_text("text", sort=True)
        except TypeError:
            fallback_text = page.get_text("text")
        except Exception:
            fallback_text = ""
        if len(fallback_text) > _MAX_PDF_TEXT_CHARS_PER_PAGE:
            raise ValueError("PDF page text exceeds the extraction safety limit.")
        fallback_lines = _unique_lines(fallback_text.splitlines())
        return [
            {
                "text": line,
                "raw_text": line,
                "bbox": (0.0, float(idx * 20), 0.0, float(idx * 20 + 16)),
                "font_size": 12.0,
                "is_monospace": False,
            }
            for idx, line in enumerate(fallback_lines)
        ]

    extracted: list[dict] = []
    extracted_chars = 0
    for block in page_dict.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [
                span
                for span in line.get("spans", [])
                if str(span.get("text") or "").replace("\u00a0", " ")
            ]
            if not spans:
                continue
            raw_parts: list[str] = []
            mono_chars = 0
            total_chars = 0
            max_font_size = 0.0
            for span in spans:
                span_text = str(span.get("text") or "").replace("\u00a0", " ")
                raw_parts.append(span_text)
                stripped_len = len(span_text.strip())
                if stripped_len:
                    total_chars += stripped_len
                    max_font_size = max(max_font_size, float(span.get("size") or 0.0))
                    if _is_monospace_span(span):
                        mono_chars += stripped_len
            raw_joined = "".join(raw_parts).rstrip("\r\n")
            extracted_chars += len(raw_joined)
            if extracted_chars > _MAX_PDF_TEXT_CHARS_PER_PAGE:
                raise ValueError("PDF page text exceeds the extraction safety limit.")
            cleaned = _clean_text(raw_joined)
            if not cleaned:
                continue
            bbox_raw = line.get("bbox") or (0.0, 0.0, 0.0, 0.0)
            bbox = (
                float(bbox_raw[0]),
                float(bbox_raw[1]),
                float(bbox_raw[2]),
                float(bbox_raw[3]),
            )
            extracted.append(
                {
                    "text": cleaned,
                    "raw_text": raw_joined,
                    "bbox": bbox,
                    "font_size": max_font_size or 12.0,
                    "is_monospace": total_chars > 0 and (mono_chars / total_chars) >= 0.6,
                }
            )
    return extracted


def _is_margin_line(line: dict, page_height: float) -> bool:
    if page_height <= 0:
        return False
    _, y0, _, y1 = line["bbox"]
    return y1 <= page_height * 0.12 or y0 >= page_height * 0.88


def _detect_repeated_margin_texts(
    pages_lines: list[list[dict]], page_heights: list[float]
) -> set[str]:
    """Find recurring slide headers/footers appearing across >=50% of pages."""
    page_count = len(pages_lines)
    if page_count < 3:
        return set()
    counts: dict[str, int] = {}
    for lines, height in zip(pages_lines, page_heights):
        seen_on_page: set[str] = set()
        for line in lines:
            if _is_margin_line(line, height):
                text = line["text"]
                if text and text not in seen_on_page:
                    seen_on_page.add(text)
                    counts[text] = counts.get(text, 0) + 1
    threshold = max(3, int(page_count * 0.5 + 0.5))
    return {text for text, count in counts.items() if count >= threshold}


def _group_pdf_code_blocks(code_lines: list[dict]) -> list[str]:
    """Reconstruct indentation for contiguous monospace lines using x0 offsets."""
    if not code_lines:
        return []
    groups: list[list[dict]] = []
    current: list[dict] = []
    for line in code_lines:
        if not current:
            current = [line]
            continue
        prev = current[-1]
        prev_height = max(prev["bbox"][3] - prev["bbox"][1], prev["font_size"], 8.0)
        vertical_gap = line["bbox"][1] - prev["bbox"][3]
        if -prev_height <= vertical_gap <= prev_height * 2.5:
            current.append(line)
        else:
            groups.append(current)
            current = [line]
    if current:
        groups.append(current)

    blocks: list[str] = []
    for group in groups:
        min_x0 = min(item["bbox"][0] for item in group)
        avg_font = sum(item["font_size"] for item in group) / len(group)
        char_width = max(avg_font * 0.6, 4.0)
        formatted_lines: list[str] = []
        for item in group:
            raw_text = item["raw_text"]
            leading_raw_spaces = len(raw_text) - len(raw_text.lstrip(" "))
            if leading_raw_spaces > 0:
                formatted_lines.append(raw_text.rstrip())
            else:
                indent_spaces = int(round(max(item["bbox"][0] - min_x0, 0.0) / char_width))
                indent_spaces = min(max(indent_spaces, 0), 24)
                formatted_lines.append((" " * indent_spaces) + raw_text.strip())
        snippet = "\n".join(formatted_lines).strip("\n")
        if snippet.strip():
            blocks.append(snippet)
    return blocks


def _parse_pdf_page_text(
    lines: list[dict],
    page_height: float,
    slide_number: int,
    repeated_margins: set[str],
) -> tuple[str, list[str], list[str]]:
    """Return (title, visible_text_lines, code_blocks) for one PDF slide page."""
    filtered: list[dict] = []
    for line in lines:
        text = line["text"]
        if _is_margin_line(line, page_height):
            if text in repeated_margins or _PAGE_NUMBER_MARGIN_RE.match(text):
                continue
        filtered.append(line)

    non_code_lines = [line for line in filtered if not line["is_monospace"]]
    code_lines = [line for line in filtered if line["is_monospace"]]

    title = ""
    if non_code_lines:
        upper_candidates = [
            line
            for line in non_code_lines
            if page_height <= 0 or line["bbox"][1] <= page_height * 0.6
        ]
        title_pool = upper_candidates or non_code_lines
        max_size = max(line["font_size"] for line in title_pool)
        title_line = next(
            (line for line in title_pool if line["font_size"] >= max_size - 0.5),
            title_pool[0],
        )
        title = re.sub(r"^[\s•◦▪▫‣⁃–—*-]+", "", title_line["text"]).strip()
        if not title:
            title = title_line["text"]
    elif filtered:
        title = filtered[0]["text"]
    else:
        title = f"Slide {slide_number}"

    visible_text = _unique_lines([line["text"] for line in non_code_lines])
    code_blocks = _group_pdf_code_blocks(code_lines)
    if not visible_text and code_blocks:
        visible_text = _unique_lines([line["text"] for line in filtered])
    return title, visible_text, code_blocks


def _extract_pdf_tables(page) -> list[str]:
    """Extract structured tables from a PDF page into Markdown tables."""
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return []
    try:
        table_finder = find_tables()
        detected_tables = getattr(table_finder, "tables", None) or []
    except Exception:
        return []

    tables: list[str] = []
    table_chars = 0
    table_cells = 0
    for table_index, table in enumerate(detected_tables, start=1):
        try:
            raw_rows = table.extract() or []
        except Exception:
            continue
        rows: list[list[str]] = []
        for raw_row in raw_rows:
            if not raw_row:
                continue
            cells = []
            for cell in raw_row:
                raw_cell = str(cell or "")
                table_chars += len(raw_cell)
                table_cells += 1
                if (
                    table_chars > _MAX_PDF_TABLE_CHARS_PER_PAGE
                    or table_cells > _MAX_PDF_TABLE_CELLS_PER_PAGE
                ):
                    raise ValueError("PDF page tables exceed the extraction safety limit.")
                normalized = re.sub(
                    r"\s*\n\s*", "<br>", raw_cell.replace("\r\n", "\n")
                )
                cells.append(_clean_text(normalized).replace("|", "\\|"))
            if any(cells):
                rows.append(cells)
        if len(rows) < 2:
            continue
        width = max(len(row) for row in rows)
        if width < 2:
            continue
        padded = [row + [""] * (width - len(row)) for row in rows]
        header = padded[0]
        body = padded[1:]
        markdown = [f"Table {table_index}:", "| " + " | ".join(header) + " |"]
        markdown.append("| " + " | ".join(["---"] * width) + " |")
        markdown.extend("| " + " | ".join(row) + " |" for row in body)
        tables.append("\n".join(markdown))
    return tables


def _extract_pdf_annotations(page) -> str:
    """Extract text annotations / sticky notes attached to a PDF slide."""
    annots_fn = getattr(page, "annots", None)
    if not callable(annots_fn):
        return ""
    notes: list[str] = []
    note_chars = 0
    try:
        for annot in annots_fn() or []:
            info = getattr(annot, "info", None) or {}
            content = _clean_text(info.get("content") or "")
            if content:
                note_chars += len(content)
                if note_chars > _MAX_PDF_ANNOTATION_CHARS_PER_PAGE:
                    raise ValueError(
                        "PDF page annotations exceed the extraction safety limit."
                    )
                notes.append(content)
    except ValueError:
        raise
    except Exception:
        return ""
    return "\n".join(_unique_lines(notes))


def _extract_pdf_links(page) -> list[str]:
    """Extract unique HTTP(S) reference URLs embedded in a PDF slide."""
    get_links = getattr(page, "get_links", None)
    if not callable(get_links):
        return []
    urls: list[str] = []
    try:
        for link in get_links() or []:
            uri = str(link.get("uri") or "").strip()
            try:
                parsed = urlsplit(uri)
            except ValueError:
                continue
            if (
                len(uri) <= _MAX_PDF_LINK_CHARS
                and not any(char.isspace() or ord(char) < 32 for char in uri)
                and parsed.scheme in {"http", "https"}
                and bool(parsed.netloc)
                and uri not in urls
            ):
                urls.append(uri)
                if len(urls) >= _MAX_PDF_LINKS_PER_PAGE:
                    break
    except Exception:
        return []
    return urls


def _markdown_code_block(snippet: str) -> list[str]:
    """Wrap extracted code without letting embedded backticks terminate the block."""
    longest_run = max((len(run) for run in re.findall(r"`+", snippet)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return [fence, snippet, fence, ""]


def _has_meaningful_vector_diagram(
    drawings: list[dict],
    page_width: float,
    page_height: float,
    text_lines: list[str],
) -> bool:
    """Distinguish real vector diagrams from slide-theme headers, footers, and backgrounds."""
    if not drawings or page_width <= 0 or page_height <= 0:
        return False
    page_area = max(page_width * page_height, 1.0)
    content_drawings = 0
    content_items = 0
    union_x0: float | None = None
    union_y0: float | None = None
    union_x1: float | None = None
    union_y1: float | None = None

    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
        width = max(x1 - x0, 0.0)
        height = max(y1 - y0, 0.0)
        area_ratio = (width * height) / page_area

        # Ignore full-slide background fills/borders
        if area_ratio >= 0.70:
            continue
        # Ignore theme header/footer margin bars
        if y1 <= page_height * 0.12 or y0 >= page_height * 0.88:
            continue
        # Ignore tiny bullets/dots or single thin horizontal divider rules
        if width < 4.0 and height < 4.0:
            continue
        if height <= 3.0 and width >= page_width * 0.5:
            continue

        content_drawings += 1
        content_items += len(drawing.get("items", []))
        union_x0 = x0 if union_x0 is None else min(union_x0, x0)
        union_y0 = y0 if union_y0 is None else min(union_y0, y0)
        union_x1 = x1 if union_x1 is None else max(union_x1, x1)
        union_y1 = y1 if union_y1 is None else max(union_y1, y1)

    if content_drawings == 0 or union_x0 is None or union_y0 is None or union_x1 is None or union_y1 is None:
        return False

    coverage_ratio = ((union_x1 - union_x0) * (union_y1 - union_y0)) / page_area
    sparse_text = len(text_lines) <= 12 or len(" ".join(text_lines)) <= 800
    if coverage_ratio >= 0.12 and content_items >= 10:
        return True
    if sparse_text and coverage_ratio >= 0.04 and (
        (content_drawings >= 4 and content_items >= 10) or content_items >= 15
    ):
        return True
    return False


def _extract_pdf(source_path: Path, figures_dir: Path) -> tuple[list[dict], list[dict]]:
    try:
        import pymupdf as fitz
    except ImportError:
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

        pages_lines: list[list[dict]] = []
        page_heights: list[float] = []
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            pages_lines.append(_extract_pdf_lines(page))
            page_heights.append(float(page.rect.height))
        repeated_margins = _detect_repeated_margin_texts(pages_lines, page_heights)

        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            slide_number = page_index + 1
            page_width = float(page.rect.width)
            page_height = page_heights[page_index]
            title, text_lines, code_blocks = _parse_pdf_page_text(
                pages_lines[page_index],
                page_height,
                slide_number,
                repeated_margins,
            )
            tables = _extract_pdf_tables(page)
            speaker_notes = _extract_pdf_annotations(page)
            links = _extract_pdf_links(page)

            page_area = max(page_width * page_height, 1.0)
            visual_names: list[str] = []
            visual_ordinal = 0
            seen_xrefs: set[int] = set()
            try:
                page_images = page.get_images(full=True)
            except Exception:
                page_images = []
            for image_tuple in page_images:
                xref = int(image_tuple[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    continue
                largest_area = max(
                    (float(rect.width * rect.height) for rect in rects), default=0.0
                )
                if largest_area / page_area < 0.04:
                    continue
                try:
                    image_data = _extract_pdf_image_bytes(document, fitz, image_tuple)
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

            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []
            if not visual_names and _has_meaningful_vector_diagram(
                drawings, page_width, page_height, text_lines
            ):
                try:
                    page_image = _render_fitz_page_png(page, fitz, preferred_scale=2.0)
                    figure_name = _write_figure(
                        figures_dir, page_image, slide_number, 1
                    )
                except Exception:
                    figure_name = None
                if figure_name:
                    visual_names.append(figure_name)
                    metadata = _figure_metadata(figure_name, slide_number, title)
                    metadata["visual_kind"] = "rendered_vector_slide"
                    metadata["pedagogical_purpose"] = (
                        "Preserve a vector diagram that could not be extracted as a standalone image"
                    )
                    figure_info.append(metadata)
            slides.append(
                {
                    "slide_number": slide_number,
                    "source_locator": f"page {slide_number}",
                    "title": title,
                    "hidden": False,
                    "included_in_book": True,
                    "visible_text": text_lines,
                    "code_blocks": code_blocks,
                    "tables": tables,
                    "chart_data": [],
                    "diagram_text": [],
                    "links": links,
                    "speaker_notes": speaker_notes,
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
        f"- {slide['source_locator']}: {slide['title']}"
        for slide in slides
        if slide.get("included_in_book", True)
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
    hidden_locators = [
        slide["source_locator"]
        for slide in slides
        if not slide.get("included_in_book", True)
    ]
    sections = [
        "# Slide-grounded technical notes",
        "",
        "The entries below are source evidence. Preserve slide order when grouping them into chapters.",
        "Visible text, speaker notes, tables, chart caches, and extracted figures remain distinct so",
        "the author can judge what the audience actually saw and what the lecturer supplied as context.",
    ]
    if hidden_locators:
        sections.extend(
            [
                "",
                "Hidden source slides excluded from authoring: "
                + ", ".join(hidden_locators)
                + ". Their metadata remains in slide_source_manifest.json for provenance.",
            ]
        )
    for slide in slides:
        if not slide.get("included_in_book", True):
            continue
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
        if slide.get("code_blocks"):
            sections.extend(["", "### Code snippets", ""])
            for snippet in slide["code_blocks"]:
                sections.extend(_markdown_code_block(snippet))
        if slide.get("tables"):
            sections.extend(["", "### Tables", "", "\n\n".join(slide["tables"])])
        if slide.get("chart_data"):
            sections.extend(["", "### Chart labels and cached values", ""])
            sections.extend(f"- {item}" for item in slide["chart_data"])
        if slide.get("diagram_text"):
            sections.extend(["", "### SmartArt and related diagram text", ""])
            sections.extend(f"- {item}" for item in slide["diagram_text"])
        if slide.get("links"):
            sections.extend(["", "### Source links", ""])
            sections.extend(f"- {url}" for url in slide["links"])
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

    materials_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".slide_materials_", dir=str(materials_dir.parent))
    )
    try:
        figures_dir = staging_dir / "figures"
        source_dir = staging_dir / "source_slides"
        figures_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)

        if extension == ".pptx":
            slides, figure_info = _extract_pptx(source_path, figures_dir)
            source_type = "pptx"
        else:
            slides, figure_info = _extract_pdf(source_path, figures_dir)
            source_type = "pdf"

        included_slides = [
            slide for slide in slides if slide.get("included_in_book", True)
        ]
        if not included_slides:
            raise ValueError("The slide source contains no visible slides to author from.")
        inferred_title = title or next(
            (slide["title"] for slide in included_slides if slide.get("title")),
            source_path.stem,
        )
        safe_source_name = (
            f"{sanitize_slug(source_path.stem, default='lecture_slides')}{extension}"
        )
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
            "included_slide_count": len(included_slides),
            "hidden_slide_count": len(slides) - len(included_slides),
            "extracted_figure_count": len(figure_info),
            "slides": slides,
        }
        (staging_dir / "book_proposal.md").write_text(
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
        (staging_dir / "technical_notes.md").write_text(
            _build_notes(slides), encoding="utf-8"
        )
        (staging_dir / "slide_source_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (figures_dir / "info.json").write_text(
            json.dumps(figure_info, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        safe_replace_managed_dir(staging_dir, materials_dir, role="slide_materials")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return {
        "raw_materials_dir": str(materials_dir),
        "proposal_path": str(materials_dir / "book_proposal.md"),
        "technical_notes_path": str(materials_dir / "technical_notes.md"),
        "manifest_path": str(materials_dir / "slide_source_manifest.json"),
        "slide_count": len(slides),
        "included_slide_count": len(included_slides),
        "hidden_slide_count": len(slides) - len(included_slides),
        "extracted_figure_count": len(figure_info),
    }
