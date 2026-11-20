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

"""
Path Safety, Workspace Isolation, Slug/Upload Sanitization, and Image Verification
for BookOrchestra.
"""

import base64
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple

WORKSPACE_MARKER_FILENAME = ".book_orchestra_managed.json"
ALLOWED_FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
ALLOWED_GENERATED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_UUID_DIR_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MAX_GENERATED_IMAGE_BYTES = 25 * 1024 * 1024


def resolve_path(path_value: str | os.PathLike) -> Path:
    """Normalizes and resolves a filesystem path cleanly."""
    return Path(path_value).expanduser().resolve()


def is_within_directory(target: str | os.PathLike, parent: str | os.PathLike) -> bool:
    """Returns True if resolved `target` is inside (or equal to) resolved `parent`."""
    try:
        t_res = resolve_path(target)
        p_res = resolve_path(parent)
        return t_res == p_res or t_res.is_relative_to(p_res)
    except Exception:
        return False


def sanitize_slug(raw_value: str, default: str = "figure") -> str:
    """
    Strictly sanitizes an identifier (e.g., figure_id) to `[a-z0-9_-]+`.
    Strips traversal segments (`..`, `/`, `\\`) and unsafe characters.
    """
    raw = str(raw_value or "").strip().lower()
    # Strip path separators and '..' sequences
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = raw.replace("..", "")
    slug = re.sub(r"[^a-z0-9_-]+", "_", raw)
    slug = re.sub(r"_+", "_", slug).strip("_-")
    if not slug or not re.fullmatch(r"[a-z0-9_-]+", slug):
        return default
    return slug[:64]


def resolve_safe_child_path(base_dir: str | os.PathLike, rel_name: str) -> Path:
    """
    Resolves `rel_name` inside `base_dir` and raises ValueError if it escapes `base_dir`.
    """
    base_res = resolve_path(base_dir)
    target_res = (base_res / rel_name).resolve()
    if not target_res.is_relative_to(base_res):
        raise ValueError(
            f"Path traversal detected: '{rel_name}' escapes base directory '{base_res}'."
        )
    return target_res


def detect_media_format(data: bytes) -> Optional[str]:
    """
    Validates raw image/PDF bytes by inspecting magic headers and decoding when possible.
    Returns canonical extension ('.png', '.jpg', '.pdf') or None if invalid.
    """
    if not data or len(data) < 8:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"%PDF-"):
        return ".pdf"
    return None


def sanitize_upload_filename(
    raw_filename: str, fallback_ext: str = ".png"
) -> Tuple[str, str]:
    """
    Extracts basename from user upload filename, enforces allowed figure extensions,
    and prepends a short UUID to eliminate collisions and traversal attacks.
    Returns (safe_filename, normalized_extension).
    """
    base = Path(str(raw_filename or "uploaded_figure.png").replace("\\", "/")).name
    stem = Path(base).stem
    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_FIGURE_EXTENSIONS:
        ext = fallback_ext if fallback_ext in ALLOWED_FIGURE_EXTENSIONS else ".png"
    safe_stem = sanitize_slug(stem, default="figure")
    safe_name = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
    return safe_name, ext


def sanitize_slide_source_filename(
    raw_filename: str, fallback_ext: str = ".pptx"
) -> str:
    """Preserve a readable PPTX/PDF source name without allowing path traversal."""
    base = Path(str(raw_filename or "lecture_slides.pptx").replace("\\", "/")).name
    stem = Path(base).stem
    extension = Path(base).suffix.lower()
    allowed_extensions = {".pptx", ".pdf"}
    if extension not in allowed_extensions:
        extension = fallback_ext if fallback_ext in allowed_extensions else ".pptx"
    safe_stem = re.sub(r"[^\w-]+", "_", stem, flags=re.UNICODE).strip("_-")
    if not safe_stem:
        safe_stem = "lecture_slides"
    return f"{safe_stem[:120]}{extension}"


def _write_marker(directory: Path, role: str) -> None:
    marker_path = directory / WORKSPACE_MARKER_FILENAME
    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump({"managed_by": "book-orchestra", "role": role}, f)


def safe_replace_managed_dir(source_dir: Path, target_dir: Path, role: str) -> None:
    """
    Copies `source_dir` into a temporary sibling folder first, marks it as BookOrchestra-managed,
    and replaces `target_dir`. Refuses to delete an existing non-empty `target_dir` unless it
    carries BookOrchestra's managed marker file.
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        marker = target_dir / WORKSPACE_MARKER_FILENAME
        has_contents = any(target_dir.iterdir())
        if has_contents and not marker.exists():
            raise ValueError(
                f"Refusing to overwrite pre-existing unmanaged directory '{target_dir}'. "
                f"Use a dedicated output directory or remove existing files manually."
            )

    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".staging_{role}_", dir=str(target_dir.parent))
    )
    try:
        shutil.copytree(source_dir, staging_dir, dirs_exist_ok=True)
        _write_marker(staging_dir, role=role)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        os.replace(str(staging_dir), str(target_dir))
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def safe_prepare_managed_subdir(target_dir: Path, role: str) -> Path:
    """
    Clears/creates a managed subdirectory only if it is empty or marked by BookOrchestra.
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        marker = target_dir / WORKSPACE_MARKER_FILENAME
        has_contents = any(target_dir.iterdir())
        if has_contents and not marker.exists():
            raise ValueError(
                f"Refusing to delete pre-existing unmanaged directory '{target_dir}'."
            )
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    _write_marker(target_dir, role=role)
    return target_dir


def validate_slide_output_workspace(
    slides_file: str | os.PathLike,
    latex_template_dir: str | os.PathLike,
    output_dir: str | os.PathLike,
) -> Tuple[Path, Path, Path]:
    """Validate slide-source paths before creating or replacing generated materials."""
    source_resolved = resolve_path(slides_file)
    template_resolved = resolve_path(latex_template_dir)
    output_resolved = resolve_path(output_dir)

    if not source_resolved.exists() or not source_resolved.is_file():
        raise ValueError(f"Slide source '{slides_file}' does not exist or is not a file.")
    if not template_resolved.exists() or not template_resolved.is_dir():
        raise ValueError(
            f"LaTeX template directory '{latex_template_dir}' does not exist."
        )
    missing_template_files = [
        filename
        for filename in ("template.tex", "guidelines.md")
        if not (template_resolved / filename).is_file()
    ]
    if missing_template_files:
        raise ValueError(
            f"LaTeX template directory '{template_resolved}' is missing required "
            f"file(s): {', '.join(missing_template_files)}."
        )
    if output_resolved.exists() and not output_resolved.is_dir():
        raise ValueError(f"Output path '{output_dir}' exists and is not a directory.")

    target_raw_dir = (output_resolved / "raw_materials").resolve()
    latex_writeup_dir = (output_resolved / "latex_writeup").resolve()
    refinement_dir = (output_resolved / "content_refinement_workdir").resolve()
    if is_within_directory(source_resolved, target_raw_dir):
        raise ValueError(
            "Slide source cannot be placed inside output_dir/raw_materials because "
            "that managed directory is replaced during ingestion."
        )
    if is_within_directory(source_resolved, latex_writeup_dir) or is_within_directory(
        source_resolved, refinement_dir
    ):
        raise ValueError("Slide source overlaps a managed output subdirectory.")
    if template_resolved == output_resolved or output_resolved.is_relative_to(
        template_resolved
    ):
        raise ValueError(
            f"Output directory '{output_resolved}' cannot overlap latex_template_dir "
            f"'{template_resolved}'."
        )
    if any(
        template_resolved.is_relative_to(managed_dir)
        for managed_dir in (target_raw_dir, latex_writeup_dir, refinement_dir)
    ):
        raise ValueError(
            f"LaTeX template directory '{template_resolved}' overlaps managed output "
            f"subdirectories in '{output_resolved}'."
        )
    return source_resolved, template_resolved, output_resolved


def prepare_safe_output_workspace(
    raw_materials_dir: str,
    latex_template_dir: str,
    output_dir: str,
) -> Tuple[Path, Path, Path, Path, Path]:
    """
    Validates that `raw_materials_dir`, `latex_template_dir`, and `output_dir` do not
    dangerously overlap, then safely populates `output_dir/raw_materials` and
    `output_dir/latex_writeup`.

    Returns:
        (raw_resolved, template_resolved, output_resolved, target_raw_dir, latex_writeup_dir)
    """
    raw_resolved = resolve_path(raw_materials_dir)
    template_resolved = resolve_path(latex_template_dir)
    output_resolved = resolve_path(output_dir)

    if not raw_resolved.exists() or not raw_resolved.is_dir():
        raise ValueError(
            f"Raw materials directory '{raw_materials_dir}' does not exist."
        )
    if not template_resolved.exists() or not template_resolved.is_dir():
        raise ValueError(
            f"LaTeX template directory '{latex_template_dir}' does not exist."
        )

    target_raw_dir = (output_resolved / "raw_materials").resolve()
    latex_writeup_dir = (output_resolved / "latex_writeup").resolve()
    refinement_dir = (output_resolved / "content_refinement_workdir").resolve()

    # Check raw_materials vs output_dir / managed subdirectories
    if raw_resolved == output_resolved or raw_resolved.is_relative_to(output_resolved) and raw_resolved != target_raw_dir:
        # e.g., raw is output/raw_materials/book1 -> would be deleted if target_raw_dir is replaced!
        if raw_resolved.is_relative_to(target_raw_dir) and raw_resolved != target_raw_dir:
            raise ValueError(
                f"Input raw_materials_dir '{raw_resolved}' lies inside output target '{target_raw_dir}' "
                f"and would be destroyed during staging. Choose a separate output_dir."
            )
        if raw_resolved.is_relative_to(latex_writeup_dir) or raw_resolved.is_relative_to(refinement_dir):
            raise ValueError(
                f"Input raw_materials_dir '{raw_resolved}' overlaps managed output subdirectory in '{output_resolved}'."
            )
    if output_resolved.is_relative_to(raw_resolved):
        raise ValueError(
            f"Output directory '{output_resolved}' cannot be placed inside raw_materials_dir '{raw_resolved}'."
        )

    # Check template vs output_dir / managed subdirectories
    if template_resolved == output_resolved or output_resolved.is_relative_to(template_resolved):
        raise ValueError(
            f"Output directory '{output_resolved}' cannot overlap latex_template_dir '{template_resolved}'."
        )
    if template_resolved.is_relative_to(latex_writeup_dir) or template_resolved.is_relative_to(target_raw_dir) or template_resolved.is_relative_to(refinement_dir):
        raise ValueError(
            f"LaTeX template directory '{template_resolved}' overlaps managed output subdirectories in '{output_resolved}'."
        )

    output_resolved.mkdir(parents=True, exist_ok=True)
    _write_marker(output_resolved, role="output_root")

    if raw_resolved != target_raw_dir:
        safe_replace_managed_dir(raw_resolved, target_raw_dir, role="raw_materials")
    else:
        _write_marker(target_raw_dir, role="raw_materials")

    safe_replace_managed_dir(template_resolved, latex_writeup_dir, role="latex_writeup")

    return (
        raw_resolved,
        template_resolved,
        output_resolved,
        target_raw_dir,
        latex_writeup_dir,
    )


def get_antigravity_brain_dir() -> Path:
    """Returns Antigravity's managed brain artifact root (`<appDataDir>/brain`)."""
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
    if app_data:
        return (Path(app_data).expanduser() / "brain").resolve()
    return (Path.home() / ".gemini" / "antigravity" / "brain").resolve()


def is_trusted_generated_image_path(
    path_str: str,
    figures_dir: str | os.PathLike,
    brain_dir: Optional[str | os.PathLike] = None,
) -> Optional[Path]:
    """
    Validates that `path_str` points to a genuine generated image inside an explicitly
    trusted output directory:
      1. Directly inside `figures_dir`, OR
      2. Directly inside an Antigravity conversation artifact folder:
         `<brain_dir>/<uuid>/<filename>.{png,jpg,jpeg}` (no nested `.system_generated`
         or arbitrary host files, and no symlinks escaping the trusted root).
    Returns the resolved `Path` if trusted and existing, else `None`.
    """
    if not path_str or not isinstance(path_str, str):
        return None
    cleaned = path_str.strip().strip("`'\"<>()[]")
    if not cleaned:
        return None
    try:
        raw_path = Path(cleaned)
        if not raw_path.is_absolute():
            return None
        if raw_path.is_symlink():
            return None
        resolved = raw_path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None

    if not resolved.is_file():
        return None
    if resolved.suffix.lower() not in ALLOWED_GENERATED_IMAGE_EXTENSIONS:
        return None

    try:
        size = resolved.stat().st_size
        if size < 8 or size > _MAX_GENERATED_IMAGE_BYTES:
            return None
    except OSError:
        return None

    # Trusted Root 1: inside figures_dir
    try:
        fig_root = resolve_path(figures_dir)
        if resolved.is_relative_to(fig_root) and resolved != fig_root:
            return resolved
    except Exception:
        pass

    # Trusted Root 2: direct `<brain_dir>/<uuid>/<image>.{png,jpg,jpeg}` produced by image-generator
    try:
        b_root = resolve_path(brain_dir) if brain_dir is not None else get_antigravity_brain_dir()
        if resolved.is_relative_to(b_root):
            rel_parts = resolved.relative_to(b_root).parts
            if len(rel_parts) == 2 and _UUID_DIR_RE.fullmatch(rel_parts[0]):
                return resolved
    except Exception:
        pass

    return None


def _extract_candidate_image_paths(text: str) -> list[str]:
    """Extracts candidate absolute image paths from plain text, Markdown, or JSON strings."""
    if not text:
        return []
    candidates: list[str] = []
    # Unix absolute paths and Windows drive paths ending in image extensions
    for m in re.finditer(
        r"(?:[A-Za-z]:[\\/]|/)[^\s`'\"<>|*?]+?\.(?:png|jpg|jpeg)\b",
        text,
        flags=re.IGNORECASE,
    ):
        cand = m.group(0).rstrip(".,;:)")
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def extract_safe_generated_image_bytes(
    response: Any,
    figures_dir: str | os.PathLike,
    brain_dir: Optional[str | os.PathLike] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Safely extracts validated PNG/JPEG bytes and canonical extension (`.png` or `.jpg`)
    from an image-generation agent response.

    Supports:
    - Raw `bytes`
    - `dict` containing `image_base64` or `image_path` / `path` / `file_path`
    - `str` containing a JSON object, a trusted Antigravity `image-generator` artifact path
      (possibly embedded in prose/backticks), or raw/data-URI base64 image bytes.

    Rejects arbitrary host paths outside `figures_dir` and `<brain_dir>/<uuid>/<file>`.
    """
    raw_bytes: Optional[bytes] = None

    if isinstance(response, bytes):
        raw_bytes = response
    elif isinstance(response, dict):
        if response.get("image_base64"):
            try:
                b64_payload = str(response["image_base64"]).strip().split(",", 1)[-1]
                raw_bytes = base64.b64decode(b64_payload, validate=True)
            except Exception:
                raw_bytes = None
        if raw_bytes is None:
            for key in ("image_path", "path", "file_path", "saved_path", "output_path"):
                val = response.get(key)
                if isinstance(val, str) and val.strip():
                    trusted = is_trusted_generated_image_path(
                        val, figures_dir=figures_dir, brain_dir=brain_dir
                    )
                    if trusted is not None:
                        try:
                            raw_bytes = trusted.read_bytes()
                            break
                        except OSError:
                            raw_bytes = None
    elif isinstance(response, str):
        stripped = response.strip()
        # 1. If the string is JSON (e.g., {"image_path": "..."}), unwrap it recursively
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    return extract_safe_generated_image_bytes(
                        parsed, figures_dir=figures_dir, brain_dir=brain_dir
                    )
            except Exception:
                pass

        # 2. Extract any trusted image file paths embedded in prose or backticks
        for cand in _extract_candidate_image_paths(stripped):
            trusted = is_trusted_generated_image_path(
                cand, figures_dir=figures_dir, brain_dir=brain_dir
            )
            if trusted is not None:
                try:
                    raw_bytes = trusted.read_bytes()
                    break
                except OSError:
                    raw_bytes = None

        # 3. Fallback: direct base64 or data:image/...;base64,... string
        if raw_bytes is None and len(stripped) > 64:
            candidate_b64 = stripped
            if candidate_b64.startswith("data:image/"):
                candidate_b64 = candidate_b64.split(",", 1)[-1].strip()
            compact_b64 = re.sub(r"\s+", "", candidate_b64)
            if len(compact_b64) > 64 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact_b64):
                try:
                    raw_bytes = base64.b64decode(compact_b64, validate=True)
                except Exception:
                    raw_bytes = None

    if not raw_bytes:
        return None, None
    fmt = detect_media_format(raw_bytes)
    if fmt not in (".png", ".jpg"):
        return None, None
    return raw_bytes, fmt
