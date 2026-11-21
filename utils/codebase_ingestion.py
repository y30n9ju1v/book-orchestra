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

"""Turn a local source tree into grounded, secret-redacted book materials."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from utils.path_security import resolve_path, safe_replace_managed_dir


_MAX_DISCOVERED_FILES = 50_000
_MAX_CANDIDATE_FILES = 10_000
_MAX_FILE_BYTES = 512 * 1024
_MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024
_MAX_NOTES_CHARS = 160_000
_MAX_EXCERPT_CHARS = 14_000
_MAX_TREE_ENTRIES = 1_200

_IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".next",
    ".nuxt",
    ".turbo",
    "__pycache__",
    "node_modules",
    "vendor",
    "vendors",
    "venv",
    ".venv",
    "env",
    ".envdir",
    "dist",
    "build",
    "target",
    "coverage",
    "htmlcov",
    "tmp",
    "temp",
}
_IGNORED_DIR_PREFIXES = ("book_output", ".slide_materials_", ".codebase_materials_")
_IGNORED_FILE_NAMES = {
    ".env",
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "cargo.lock",
    "composer.lock",
}
_SAFE_ENV_TEMPLATE_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
}
_SECRET_EXTENSION_RE = re.compile(
    r"\.(?:pem|key|p12|pfx|jks|keystore|kdbx|ovpn)$",
    re.IGNORECASE,
)
_SECRET_CONFIG_EXTENSIONS = {
    "",
    ".cfg",
    ".conf",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_SECRET_FILENAME_RE = re.compile(
    r"(?:^|[._-])(?:secret|secrets|credential|credentials|password|passwd|token|"
    r"private[_-]?key)(?:[._-]|$)",
    re.IGNORECASE,
)
_GENERATED_FILENAME_RE = re.compile(
    r"(?:\.min\.(?:js|css)$|\.map$|\.generated\.|\.g\.(?:cs|dart)$)",
    re.IGNORECASE,
)
_TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".ex",
    ".exs",
    ".go",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".lua",
    ".md",
    ".php",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".tex",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_SPECIAL_TEXT_NAMES = {
    "dockerfile",
    "makefile",
    "justfile",
    "gemfile",
    "procfile",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "compose.yaml",
    "compose.yml",
}
_LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".dart": "dart",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "jsx",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".md": "markdown",
    ".php": "php",
    ".proto": "protobuf",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".sql": "sql",
    ".swift": "swift",
    ".tf": "hcl",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "cli.py",
    "server.py",
    "index.js",
    "index.ts",
    "main.go",
    "main.rs",
    "program.cs",
}
_MANIFEST_NAMES = {
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "compose.yaml",
    "compose.yml",
    "dockerfile",
    "makefile",
    "requirements.txt",
}

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_TOKEN_RE = re.compile(
    r"\b(?:(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[0-9A-Za-z-]{10,}|sk-[A-Za-z0-9_-]{20,})\b"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{20,})")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)((?:['\"])?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|password|passwd|secret)\b(?:['\"])?\s*[=:]\s*)"
    r"(['\"])([^'\"\n]{4,})(\2)"
)
_SECRET_UNQUOTED_RE = re.compile(
    r"(?im)^(\s*(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|passwd|secret)\s*[=:]\s*)([A-Za-z0-9_./+@:-]{4,})(\s*(?:#.*)?$)"
)
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)((?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|rediss?|amqps?|ftp)://[^\s/:]+:)([^@\s/]+)(@)"
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_ignored_directory(name: str) -> bool:
    lowered = name.lower()
    return lowered in _IGNORED_DIR_NAMES or lowered.startswith(_IGNORED_DIR_PREFIXES)


def _is_candidate_file(path: Path) -> bool:
    lowered_name = path.name.lower()
    suffix = path.suffix.lower()
    if lowered_name in _IGNORED_FILE_NAMES:
        return False
    if lowered_name.startswith(".env.") and lowered_name not in _SAFE_ENV_TEMPLATE_NAMES:
        return False
    if _SECRET_EXTENSION_RE.search(lowered_name) or _GENERATED_FILENAME_RE.search(lowered_name):
        return False
    if suffix in _SECRET_CONFIG_EXTENSIONS and _SECRET_FILENAME_RE.search(lowered_name):
        return False
    return (
        suffix in _TEXT_EXTENSIONS
        or lowered_name in _SPECIAL_TEXT_NAMES
        or lowered_name.startswith(("readme", "license", "changelog"))
    )


def _category(relative_path: str) -> str:
    lowered = relative_path.lower()
    name = Path(relative_path).name.lower()
    parts = set(Path(lowered).parts)
    if (
        name.startswith(("readme", "license", "changelog"))
        or Path(name).suffix in {".md", ".rst"}
        or "docs" in parts
    ):
        return "documentation"
    if name in _MANIFEST_NAMES or name.endswith((".yaml", ".yml", ".toml")):
        return "configuration"
    if "test" in parts or "tests" in parts or re.search(r"(?:^|[._-])test(?:[._-]|$)", name):
        return "tests"
    return "source"


def _priority(relative_path: str, category: str) -> int:
    path = Path(relative_path)
    name = path.name.lower()
    depth_penalty = min(len(path.parts) * 8, 80)
    if name.startswith("readme"):
        return 1_200 - depth_penalty
    if name in _MANIFEST_NAMES:
        return 1_100 - depth_penalty
    if name.startswith("license"):
        return 1_050 - depth_penalty
    if name in _ENTRYPOINT_NAMES:
        return 1_000 - depth_penalty
    return {
        "documentation": 900,
        "configuration": 800,
        "source": 600,
        "tests": 350,
    }.get(category, 300) - depth_penalty


def _language(path: Path) -> str:
    return _LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "text")


def _decode_text(data: bytes) -> tuple[str, str]:
    if b"\x00" in data[:8192]:
        raise ValueError("binary content")
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        decoded = data.decode("utf-8", errors="replace")
        if decoded.count("\ufffd") > max(8, len(decoded) // 100):
            raise ValueError("non-text encoding")
        return decoded, "utf-8-with-replacements"


def _redact_secrets(text: str) -> tuple[str, int]:
    redactions = 0

    def replace(_match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return "<REDACTED>"

    text = _PRIVATE_KEY_RE.sub(replace, text)
    text = _TOKEN_RE.sub(replace, text)

    def replace_bearer(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}<REDACTED>"

    text = _BEARER_TOKEN_RE.sub(replace_bearer, text)

    def replace_assignment(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}{match.group(2)}<REDACTED>{match.group(4)}"

    text = _SECRET_ASSIGNMENT_RE.sub(replace_assignment, text)

    def replace_unquoted(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}<REDACTED>{match.group(3)}"

    text = _SECRET_UNQUOTED_RE.sub(replace_unquoted, text)

    def replace_url(match: re.Match) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}<REDACTED>{match.group(3)}"

    return _CREDENTIAL_URL_RE.sub(replace_url, text), redactions


def _symbol_outline(text: str, language: str) -> list[str]:
    symbols: list[str] = []
    if language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    symbols.append(f"{kind} {node.name} (line {node.lineno})")
                    if isinstance(node, ast.ClassDef):
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                symbols.append(
                                    f"method {node.name}.{child.name} (line {child.lineno})"
                                )
    else:
        symbol_re = re.compile(
            r"(?m)^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|static\s+|"
            r"async\s+)*(class|interface|struct|enum|func|function|def|fn)\s+([A-Za-z_][\w$]*)"
        )
        for match in symbol_re.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            symbols.append(f"{match.group(1)} {match.group(2)} (line {line})")
    return symbols[:80]


def _excerpt(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text.rstrip(), False
    head_chars = max(int(max_chars * 0.78), 1)
    tail_chars = max(max_chars - head_chars, 1)
    return (
        text[:head_chars].rstrip()
        + "\n\n... [middle omitted by BookOrchestra ingestion] ...\n\n"
        + text[-tail_chars:].lstrip(),
        True,
    )


def _candidate_excerpt_limit(candidate: dict, remaining_chars: int) -> int:
    name = Path(candidate["path"]).name.lower()
    if name.startswith("license"):
        preferred = 2_500
    elif name in {"code_of_conduct.md", "contributing.md"}:
        preferred = 4_000
    elif candidate["category"] == "configuration":
        preferred = 8_000
    elif candidate["category"] == "documentation":
        preferred = 10_000
    else:
        preferred = _MAX_EXCERPT_CHARS
    return min(preferred, max(remaining_chars - 600, 500))


def _markdown_fence(text: str) -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest_run + 1)


def _discover_candidates(root: Path, excluded_roots: list[Path]) -> tuple[list[dict], dict]:
    candidates: list[dict] = []
    stats = {"discovered": 0, "ignored": 0, "too_large": 0, "symlinks": 0}
    for current_root, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        kept_dirs = []
        for dir_name in sorted(dir_names):
            child = current / dir_name
            if child.is_symlink():
                stats["symlinks"] += 1
                continue
            resolved_child = child.resolve()
            if _is_ignored_directory(dir_name) or any(
                resolved_child == excluded or resolved_child.is_relative_to(excluded)
                for excluded in excluded_roots
            ):
                stats["ignored"] += 1
                continue
            kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs

        for file_name in sorted(file_names):
            stats["discovered"] += 1
            if stats["discovered"] > _MAX_DISCOVERED_FILES:
                raise ValueError(
                    f"Codebase contains more than {_MAX_DISCOVERED_FILES} files; narrow the source directory."
                )
            path = current / file_name
            if path.is_symlink():
                stats["symlinks"] += 1
                continue
            if not _is_candidate_file(path):
                stats["ignored"] += 1
                continue
            try:
                stat = path.stat()
            except OSError:
                stats["ignored"] += 1
                continue
            if not path.is_file() or stat.st_size > _MAX_FILE_BYTES:
                stats["too_large"] += 1
                continue
            relative_path = path.relative_to(root).as_posix()
            category = _category(relative_path)
            candidates.append(
                {
                    "path": relative_path,
                    "absolute_path": path,
                    "size_bytes": stat.st_size,
                    "category": category,
                    "language": _language(path),
                    "priority": _priority(relative_path, category),
                }
            )
            if len(candidates) > _MAX_CANDIDATE_FILES:
                raise ValueError(
                    f"Codebase contains more than {_MAX_CANDIDATE_FILES} supported text files; narrow the source directory."
                )
    candidates.sort(key=lambda item: (-item["priority"], item["path"].lower()))
    return candidates, stats


def _tree_listing(candidates: list[dict]) -> str:
    paths = [item["path"] for item in candidates[:_MAX_TREE_ENTRIES]]
    lines = [f"- `{path}`" for path in sorted(paths)]
    if len(candidates) > len(paths):
        lines.append(f"- ... {len(candidates) - len(paths)} additional supported files omitted")
    return "\n".join(lines)


def _balanced_candidate_order(candidates: list[dict]) -> list[dict]:
    """Put representative tests beside docs/config/source before filling by score."""
    selection_plan = (
        ("documentation", 6),
        ("configuration", 8),
        ("source", 8),
        ("tests", 8),
        ("source", 6),
    )
    selected: list[dict] = []
    selected_paths: set[str] = set()
    for category, quota in selection_plan:
        for item in (
            candidate
            for candidate in candidates
            if candidate["category"] == category and candidate["path"] not in selected_paths
        ):
            if quota <= 0:
                break
            selected.append(item)
            selected_paths.add(item["path"])
            quota -= 1
    for category in ("source", "tests", "configuration", "documentation"):
        selected.extend(
            item
            for item in candidates
            if item["category"] == category and item["path"] not in selected_paths
        )
    return selected


def _build_proposal(
    project_name: str,
    target_audience: str,
    book_style: str,
    author_voice: str,
    included_files: list[dict],
) -> str:
    paths = "\n".join(f"- `{item['path']}` ({item['category']})" for item in included_files)
    return f"""# Book Proposal: Understanding {project_name}

## Source and purpose

Turn the `{project_name}` codebase into a self-contained explanatory engineering book. The
repository is the primary source of truth. Explain the system's purpose, architecture, control
flow, data model, extension points, tests, and operational trade-offs without turning the book
into a file-by-file API dump.

## Reader and editorial profile

- Target audience: {target_audience}
- Book style: {book_style}
- Author voice: {author_voice}

## Binding code-explanation contract

- Ground implementation claims in exact repository paths and symbols from `technical_notes.md`.
- Distinguish directly observed behavior from static-analysis inference. Never claim that code
  was executed, benchmarked, or production-validated by the ingestion step.
- Begin with the problem and a high-level mental model, then trace representative end-to-end
  flows before explaining subsystems in depth.
- Define project-specific terms before relying on them. Explain framework and language concepts
  that the target reader may not know.
- Use small, faithful code excerpts. Prefer explaining why a design exists and how components
  collaborate over reproducing long source files.
- Treat tests, configuration, documentation, and build files as behavioral evidence.
- Do not reveal, reconstruct, or speculate about credentials and secrets. Some literals may be
  replaced with `<REDACTED>` during ingestion.
- Do not invent endpoints, types, performance numbers, failure modes, or deployment guarantees.
- Respect the repository's license and attribution requirements when publishing derived material.

## Selected evidence map

{paths}
"""


def _build_notes(project_name: str, tree: str, included_files: list[dict], stats: dict) -> str:
    sections = [
        "# Codebase-grounded technical notes",
        "",
        f"Project: `{project_name}`",
        "",
        "These notes were produced by static source inspection only; no project code was executed.",
        "Every implementation claim in the book should cite a repository path or be labeled as an inference.",
        "Secret-like literals are redacted, and dependency/build/generated directories are excluded.",
        "",
        "## Ingestion summary",
        "",
        f"- Files discovered: {stats['discovered']}",
        f"- Supported text candidates: {stats['candidate_count']}",
        f"- Included evidence files: {len(included_files)}",
        f"- Ignored files/directories: {stats['ignored']}",
        f"- Oversized files skipped: {stats['too_large']}",
        f"- Symlinks skipped: {stats['symlinks']}",
        f"- Secret-like values redacted: {stats['redaction_count']}",
        "",
        "## Supported source tree",
        "",
        tree or "- No supported files found.",
    ]
    for item in included_files:
        sections.extend(
            [
                "",
                f"## File: `{item['path']}`",
                "",
                f"- Category: {item['category']}",
                f"- Language: {item['language']}",
                f"- Original SHA-256: `{item['sha256']}`",
                f"- Original size: {item['size_bytes']} bytes; {item['line_count']} lines",
                f"- Sanitized snapshot: `source_code/{item['path']}`",
            ]
        )
        if item["symbols"]:
            sections.extend(
                ["- Top-level symbols:"] + [f"  - {symbol}" for symbol in item["symbols"]]
            )
        if item["truncated"]:
            sections.append(
                "- Excerpt status: middle omitted; inspect the sanitized snapshot for full selected-file context."
            )
        fence = _markdown_fence(item["excerpt"])
        sections.extend(["", f"{fence}{item['language']}", item["excerpt"], fence])
    return "\n".join(sections).rstrip() + "\n"


def prepare_codebase_materials(
    codebase_dir: str | os.PathLike,
    output_dir: str | os.PathLike,
    *,
    target_audience: str = "Software engineers new to this codebase",
    book_style: str = "Architecture-first, example-rich code walkthrough",
    author_voice: str = "Warm, precise, and explicit about evidence versus inference",
    title: str | None = None,
) -> dict[str, Any]:
    """Create managed BookOrchestra raw materials from a local source directory."""
    source_root = resolve_path(codebase_dir)
    materials_dir = resolve_path(output_dir)
    if not source_root.is_dir():
        raise ValueError(f"Codebase source '{codebase_dir}' does not exist or is not a directory.")
    if source_root == materials_dir or source_root.is_relative_to(materials_dir):
        raise ValueError("Codebase source cannot be inside the generated raw-materials directory.")
    if materials_dir.parent == source_root:
        raise ValueError(
            "Generated raw materials must be placed in a dedicated output subdirectory, "
            "not directly under the codebase root."
        )

    materials_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=".codebase_materials_", dir=str(materials_dir.parent))
    )
    try:
        excluded_roots = [materials_dir, staging_dir]
        if (
            materials_dir.parent.is_relative_to(source_root)
            and materials_dir.parent != source_root
        ):
            excluded_roots.append(materials_dir.parent)
        candidates, discovery_stats = _discover_candidates(source_root, excluded_roots)
        if not candidates:
            raise ValueError(
                "The codebase contains no supported, non-secret text files to explain."
            )

        snapshot_dir = staging_dir / "source_code"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        included_files: list[dict] = []
        snapshot_bytes = 0
        redaction_count = 0
        tree = _tree_listing(candidates)
        notes_chars = len(tree) + 5_000
        for candidate in _balanced_candidate_order(candidates):
            if snapshot_bytes + candidate["size_bytes"] > _MAX_SNAPSHOT_BYTES:
                continue
            remaining_notes = _MAX_NOTES_CHARS - notes_chars
            if remaining_notes < 1_000:
                break
            try:
                raw_bytes = candidate["absolute_path"].read_bytes()
                if len(raw_bytes) > _MAX_FILE_BYTES:
                    discovery_stats["too_large"] += 1
                    continue
                decoded, encoding = _decode_text(raw_bytes)
            except (OSError, ValueError):
                discovery_stats["ignored"] += 1
                continue
            sanitized, redactions = _redact_secrets(decoded)
            redaction_count += redactions
            excerpt_limit = _candidate_excerpt_limit(candidate, remaining_notes)
            excerpt, truncated = _excerpt(sanitized, excerpt_limit)
            destination = snapshot_dir / candidate["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(sanitized, encoding="utf-8")
            snapshot_bytes += len(sanitized.encode("utf-8"))
            notes_chars += len(excerpt) + 600
            included_files.append(
                {
                    "path": candidate["path"],
                    "category": candidate["category"],
                    "language": candidate["language"],
                    "size_bytes": len(raw_bytes),
                    "line_count": decoded.count("\n") + (1 if decoded else 0),
                    "sha256": _sha256_bytes(raw_bytes),
                    "encoding": encoding,
                    "redaction_count": redactions,
                    "symbols": _symbol_outline(sanitized, candidate["language"]),
                    "excerpt": excerpt,
                    "truncated": truncated,
                }
            )
        if not included_files:
            raise ValueError("No readable source files remained after codebase safety filtering.")

        project_name = title or source_root.name
        stats = {
            **discovery_stats,
            "candidate_count": len(candidates),
            "redaction_count": redaction_count,
        }
        proposal = _build_proposal(
            project_name, target_audience, book_style, author_voice, included_files
        )
        notes = _build_notes(project_name, tree, included_files, stats)
        tree_hasher = hashlib.sha256()
        manifest_files = []
        for item in included_files:
            tree_hasher.update(item["path"].encode("utf-8"))
            tree_hasher.update(item["sha256"].encode("ascii"))
            manifest_files.append({key: value for key, value in item.items() if key != "excerpt"})
        manifest = {
            "schema_version": 1,
            "source_type": "codebase",
            "source_name": source_root.name,
            "source_tree_sha256": tree_hasher.hexdigest(),
            "preserved_source": "source_code/",
            "snapshot_is_secret_redacted": True,
            "project_title": project_name,
            "discovered_file_count": stats["discovered"],
            "candidate_file_count": len(candidates),
            "included_file_count": len(included_files),
            "snapshot_bytes": snapshot_bytes,
            "redaction_count": redaction_count,
            "files": manifest_files,
        }
        (staging_dir / "book_proposal.md").write_text(proposal, encoding="utf-8")
        (staging_dir / "technical_notes.md").write_text(notes, encoding="utf-8")
        (staging_dir / "codebase_source_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        safe_replace_managed_dir(staging_dir, materials_dir, role="codebase_materials")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return {
        "raw_materials_dir": str(materials_dir),
        "proposal_path": str(materials_dir / "book_proposal.md"),
        "technical_notes_path": str(materials_dir / "technical_notes.md"),
        "manifest_path": str(materials_dir / "codebase_source_manifest.json"),
        "discovered_file_count": stats["discovered"],
        "candidate_file_count": len(candidates),
        "included_file_count": len(included_files),
        "redaction_count": redaction_count,
    }
