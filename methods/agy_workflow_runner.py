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
Antigravity Native `run_workflow` Script for BookOrchestra.

Orchestrates the entire multi-agent IT & Engineering Book publishing pipeline
using Antigravity's built-in `phase`, `agent`, `parallel`, and `pipeline` primitives
with zero external model-provider API keys required.

Usage inside Antigravity:
  run_workflow(
      ScriptPath="workflows/book_orchestra_workflow.py",
      Args={
          "raw_materials_dir": "examples/distributed_llm_systems_book/raw_materials",
          "latex_template_dir": "templates/oreilly_style_book",
          "output_dir": "./book_output_agy",
          "use_plotting": True,
          "visual_density": "high",
      },
      Description="Author and review an IT/Engineering book with BookOrchestra"
  )
"""

import asyncio
import base64
import datetime
import json
import os
import re
import shutil
import sys

# Ensure repository root is importable even when run_workflow executes in a separate process
_WORK_ROOT = (
    WORKSPACES[0]  # type: ignore[name-defined]
    if "WORKSPACES" in globals() and WORKSPACES  # type: ignore[name-defined]
    else os.getcwd()
)
if _WORK_ROOT not in sys.path:
    sys.path.insert(0, _WORK_ROOT)

from methods.prompts.outline_agent import outline_agent_system_prompt
from methods.prompts.literature_review_agent import (
    literature_review_agent_writter_prompt,
)
from methods.prompts.section_writing_agent import section_writing_agent_prompt
from methods.prompts.content_refinement_agent import (
    content_refinement_agent_system_prompt,
)
from methods.prompts.format_agent import format_agent_system_prompt
from methods.agents.outline_agent import OutlineAgent
from autoraters.agent_review import (
    REVIEW_BOARD_PERSONAS,
    AGENTREVIEW_INSTRUCTIONS,
    get_agentreview_system_prompt,
    meta_reviewer_system_prompt,
    _populate_score_aliases,
)
from utils.agent_runtime_utils import (
    parse_agent_json_response,
    parse_agent_latex_response,
)
from utils.diagram_plot_utils import (
    DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT,
    PLOT_PLANNER_AGENT_SYSTEM_PROMPT,
    PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT,
    PLOT_CRITIC_AGENT_SYSTEM_PROMPT,
    execute_plot_code_worker,
    _find_matplotlib_python,
)
from utils.pdf_utils import (
    compile_latex,
    get_last_compile_mode,
    get_last_preview_backend,
    latex_to_markdown,
    pdf_to_grid_images,
    inspect_book_layout_issues,
)
from utils.path_security import (
    detect_media_format,
    extract_safe_generated_image_bytes,
    prepare_safe_output_workspace,
    resolve_safe_child_path,
    safe_prepare_managed_subdir,
    sanitize_slug,
    validate_codebase_output_workspace,
    validate_slide_output_workspace,
)
from utils.reference_verifier import (
    is_reference_before_cutoff,
    has_verifiable_identifier_or_grounding,
    format_bibtex_entry,
    audit_and_clean_citations,
)


OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "book_metadata": {"type": "object"},
        "plotting_plan": {"type": "array", "items": {"type": "object"}},
        "intro_related_work_plan": {"type": "object"},
        "section_plan": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["plotting_plan", "intro_related_work_plan", "section_plan"],
}

CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "section_name": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "year": {"type": "integer"},
                    "authors": {"type": "array", "items": {"type": "string"}},
                    "venue": {"type": "string"},
                    "source_type": {"type": "string"},
                    "doi": {"type": "string"},
                    "arxiv_id": {"type": "string"},
                    "rfc_number": {"type": "string"},
                    "isbn": {"type": "string"},
                    "url": {"type": "string"},
                    "publication_date": {"type": "string"},
                    "summary": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["title", "year", "reason"],
            },
        },
    },
    "required": ["candidates"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "Summary": {"type": "string"},
        "Strengths": {"type": "array", "items": {"type": "string"}},
        "Weaknesses": {"type": "array", "items": {"type": "string"}},
        "Technical_Depth": {"type": "integer"},
        "Practical_Utility": {"type": "integer"},
        "Code_and_Architecture_Quality": {"type": "integer"},
        "Pedagogical_Clarity": {"type": "integer"},
        "Structural_Coherence": {"type": "integer"},
        "Editorial_Polish": {"type": "integer"},
        "Voice_Fidelity": {"type": "integer"},
        "Readability": {"type": "integer"},
        "Warmth": {"type": "integer"},
        "Questions": {"type": "array", "items": {"type": "string"}},
        "Limitations": {"type": "array", "items": {"type": "string"}},
        "Overall": {"type": "integer"},
        "Confidence": {"type": "integer"},
        "Decision": {"type": "string"},
    },
    "required": [
        "Summary",
        "Strengths",
        "Weaknesses",
        "Technical_Depth",
        "Practical_Utility",
        "Code_and_Architecture_Quality",
        "Pedagogical_Clarity",
        "Structural_Coherence",
        "Editorial_Polish",
        "Voice_Fidelity",
        "Readability",
        "Warmth",
        "Overall",
        "Decision",
    ],
}

REVISION_SCHEMA = {
    "type": "object",
    "properties": {
        "worklog": {
            "type": "object",
            "properties": {
                "addressed_weaknesses": {"type": "array", "items": {"type": "string"}},
                "integrated_answers": {"type": "array", "items": {"type": "string"}},
                "actions_taken": {"type": "array", "items": {"type": "string"}},
            },
        },
        "latex": {"type": "string"},
    },
    "required": ["worklog", "latex"],
}


def _read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_slide_source_context(raw_materials_dir: str) -> tuple[dict, str]:
    """Load compact provenance without duplicating all extracted slide text in prompts."""
    manifest_path = os.path.join(raw_materials_dir, "slide_source_manifest.json")
    if not os.path.isfile(manifest_path):
        return {}, ""
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, json.JSONDecodeError):
        return {}, ""
    if not isinstance(manifest, dict):
        return {}, ""
    outline_lines = []
    for slide in manifest.get("slides", []):
        if not isinstance(slide, dict):
            continue
        locator = slide.get("source_locator") or f"slide {slide.get('slide_number', '?')}"
        title = slide.get("title") or "Untitled"
        figures = slide.get("figures") or []
        authoring_status = (
            "hidden; excluded from authoring"
            if not slide.get("included_in_book", True)
            else "included"
        )
        outline_lines.append(
            f"- {locator}: {title} ({authoring_status}; extracted visuals: "
            f"{len(figures) if isinstance(figures, list) else 0})"
        )
    context = (
        "Source-deck provenance:\n"
        f"- Original file: {manifest.get('source_file', 'unknown')}\n"
        f"- Type: {manifest.get('source_type', 'unknown')}\n"
        f"- SHA-256: {manifest.get('source_sha256', 'unknown')}\n"
        f"- Slide/page count: {manifest.get('slide_count', len(outline_lines))}\n"
        f"- Included for authoring: {manifest.get('included_slide_count', len(outline_lines))}\n"
        f"- Hidden/excluded: {manifest.get('hidden_slide_count', 0)}\n"
        "- Traceable sequence:\n"
        + "\n".join(outline_lines)
    )
    return manifest, context


def _load_codebase_source_context(raw_materials_dir: str) -> tuple[dict, str]:
    """Load compact code provenance without duplicating source excerpts in prompts."""
    manifest_path = os.path.join(raw_materials_dir, "codebase_source_manifest.json")
    if not os.path.isfile(manifest_path):
        return {}, ""
    try:
        with open(manifest_path, "r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
    except (OSError, json.JSONDecodeError):
        return {}, ""
    if not isinstance(manifest, dict):
        return {}, ""
    evidence_lines = []
    for source_file in manifest.get("files", []):
        if not isinstance(source_file, dict):
            continue
        symbols = source_file.get("symbols") or []
        symbol_summary = ", ".join(str(symbol) for symbol in symbols[:5])
        evidence_lines.append(
            f"- {source_file.get('path', 'unknown')} "
            f"({source_file.get('category', 'source')}; "
            f"SHA-256 {source_file.get('sha256', 'unknown')}; "
            f"symbols: {symbol_summary or 'not detected'})"
        )
    context = (
        "Codebase provenance (static inspection only):\n"
        f"- Project: {manifest.get('source_name', 'unknown')}\n"
        f"- Source-tree SHA-256: {manifest.get('source_tree_sha256', 'unknown')}\n"
        f"- Files discovered: {manifest.get('discovered_file_count', 0)}\n"
        f"- Evidence files included: {manifest.get('included_file_count', len(evidence_lines))}\n"
        f"- Secret-like values redacted: {manifest.get('redaction_count', 0)}\n"
        "- Evidence map:\n"
        + "\n".join(evidence_lines)
    )
    return manifest, context


def _ensure_body_reading_rhythm(latex_text: str) -> str:
    """Restore readable body spacing if an author/editor agent drops template settings."""
    if not latex_text or r"\begin{document}" not in latex_text:
        return latex_text

    additions = []
    if r"\usepackage{setspace}" not in latex_text:
        additions.append(r"\usepackage{setspace}")
    if not re.search(r"\\(?:setstretch|linespread)\s*\{", latex_text):
        additions.append(r"\setstretch{1.12}")
    if not re.search(r"\\setlength\s*\{\\parskip\}", latex_text):
        additions.append(r"\setlength{\parskip}{0.2\baselineskip}")
    if not re.search(r"\\setlength\s*\{\\parindent\}", latex_text):
        additions.append(r"\setlength{\parindent}{1em}")
    if not additions:
        return latex_text

    rhythm_block = (
        "% Comfortable body rhythm for Korean and English technical prose.\n"
        + "\n".join(additions)
        + "\n"
    )
    return latex_text.replace(
        r"\begin{document}", rhythm_block + r"\begin{document}", 1
    )


def _resolve_file(folder: str, primary: str, fallbacks: list[str]) -> str:
    for name in [primary] + [fb for fb in fallbacks if fb != primary]:
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(folder, primary)


_SUPPORTED_FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
_VISUALS_PER_CHAPTER = {"minimal": 0, "balanced": 1, "high": 2}


def _normalize_visual_density(value: object) -> str:
    density = str(value or "high").strip().lower()
    return density if density in _VISUALS_PER_CHAPTER else "high"


def _catalog_user_figures(figures_dir: str) -> list[dict]:
    """Register user-supplied figures that were copied into the managed workspace."""
    metadata_by_name: dict[str, dict] = {}
    metadata_path = os.path.join(figures_dir, "info.json")
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as metadata_file:
                raw_metadata = json.load(metadata_file)
            if isinstance(raw_metadata, list):
                metadata_by_name = {
                    str(item.get("name", "")).replace("\\", "/"): item
                    for item in raw_metadata
                    if isinstance(item, dict) and item.get("name")
                }
        except (OSError, json.JSONDecodeError):
            metadata_by_name = {}

    figures: list[dict] = []
    if not os.path.isdir(figures_dir):
        return figures

    for root, dirs, files in os.walk(figures_dir):
        dirs.sort()
        for filename in sorted(files):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in _SUPPORTED_FIGURE_EXTENSIONS:
                continue
            absolute_path = os.path.join(root, filename)
            relative_name = os.path.relpath(absolute_path, figures_dir).replace(
                os.sep, "/"
            )
            supplied = metadata_by_name.get(relative_name, {})
            stem = os.path.splitext(os.path.basename(relative_name))[0]
            readable_stem = re.sub(r"[_-]+", " ", stem).strip()
            title = str(supplied.get("title") or readable_stem or stem)
            caption = str(supplied.get("caption") or title)
            figures.append(
                {
                    "figure_id": sanitize_slug(
                        supplied.get("figure_id") or stem, default="user_figure"
                    ),
                    "title": title,
                    "name": relative_name,
                    "image_path": f"figures/{relative_name}",
                    "caption": caption,
                    "chapter_title": supplied.get("chapter_title", ""),
                    "placement_hint": supplied.get("placement_hint", ""),
                    "pedagogical_purpose": supplied.get(
                        "pedagogical_purpose", "User-supplied reference visual"
                    ),
                    "generated_by": "user",
                    "status": "ready",
                    "size_bytes": os.path.getsize(absolute_path),
                }
            )
    return figures


def _ensure_visual_coverage(outline: dict, visual_density: str) -> dict:
    """Fill chapter-level visual gaps without inventing quantitative plots."""
    density = _normalize_visual_density(visual_density)
    target = _VISUALS_PER_CHAPTER[density]
    raw_plans = outline.get("plotting_plan", [])
    plans = [dict(plan) for plan in raw_plans if isinstance(plan, dict)]
    if target == 0:
        outline["plotting_plan"] = plans
        return outline

    sections = outline.get("section_plan", [])
    if not isinstance(sections, list):
        sections = []

    def _norm(text: object) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().casefold()

    def _is_supplemental(title: str) -> bool:
        low = _norm(title)
        return any(
            marker in low
            for marker in ("preface", "appendix", "서문", "머리말", "부록")
        )

    used_ids = {
        sanitize_slug(plan.get("figure_id", ""), default="visual") for plan in plans
    }
    for chapter_index, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            continue
        chapter_title = str(section.get("section_title") or "").strip()
        if not chapter_title or _is_supplemental(chapter_title):
            continue

        chapter_key = _norm(chapter_title)
        matching = [
            plan
            for plan in plans
            if _norm(plan.get("chapter_title")) == chapter_key
        ]
        missing = max(0, target - len(matching))
        if not missing:
            continue

        objectives = section.get("learning_objectives", [])
        if not isinstance(objectives, list):
            objectives = []
        subsections = section.get("subsections", [])
        if not isinstance(subsections, list):
            subsections = []
        subsection_titles = [
            str(item.get("subsection_title"))
            for item in subsections
            if isinstance(item, dict) and item.get("subsection_title")
        ]
        concept_seed = "; ".join(str(item) for item in objectives[:4]) or chapter_title
        flow_seed = "; ".join(subsection_titles[:5]) or concept_seed
        candidates = [
            {
                "suffix": "concept_map",
                "title": f"{chapter_title}: 핵심 개념 지도",
                "objective": (
                    "Create a reader-facing concept map that explains the relationships, "
                    f"boundaries, inputs, outputs, and key trade-offs for: {concept_seed}. "
                    "Prefer a small number of clearly labeled elements and directional links."
                ),
                "placement_hint": "Place after the chapter opening mental model.",
                "pedagogical_purpose": "Build the reader's mental model before implementation details.",
            },
            {
                "suffix": "execution_flow",
                "title": f"{chapter_title}: 실행 흐름과 판단 지점",
                "objective": (
                    "Create a numbered process/data-flow diagram with decision points, state "
                    f"transitions, and failure or recovery paths grounded in: {flow_seed}."
                ),
                "placement_hint": "Place beside the first end-to-end walkthrough.",
                "pedagogical_purpose": "Make control flow and cause-and-effect visible at a glance.",
            },
        ]
        for candidate in candidates[:missing]:
            base_id = f"fig_ch{chapter_index:02d}_{candidate['suffix']}"
            figure_id = base_id
            serial = 2
            while figure_id in used_ids:
                figure_id = f"{base_id}_{serial}"
                serial += 1
            used_ids.add(figure_id)
            plans.append(
                {
                    "figure_id": figure_id,
                    "title": candidate["title"],
                    "plot_type": "diagram",
                    "data_source": "both",
                    "objective": candidate["objective"],
                    "aspect_ratio": "16:9",
                    "chapter_title": chapter_title,
                    "placement_hint": candidate["placement_hint"],
                    "pedagogical_purpose": candidate["pedagogical_purpose"],
                    "auto_added": True,
                }
            )

    outline["plotting_plan"] = plans
    return outline


def _generate_citation_key(authors_list: list[str], year: int, title: str) -> str:
    first_author = (authors_list[0].split()[-1] if authors_list else "Ref")
    clean_author = re.sub(r"[^a-zA-Z]", "", first_author).capitalize() or "Ref"
    year_str = str(year or 2024)
    words = [
        w.capitalize()
        for w in re.sub(r"[^a-zA-Z0-9\s]", "", title.lower()).split()
        if w not in {"the", "a", "an", "in", "on", "at", "for", "to", "of", "and", "with", "by"}
    ]
    title_part = "".join(words[:2]) if words else "Work"
    return f"{clean_author}{year_str}{title_part}"


def _collect_search_tasks(outline: dict) -> list[dict]:
    tasks = []
    plan = outline.get("intro_related_work_plan") or outline.get(
        "reference_research_plan", {}
    )
    if not isinstance(plan, dict):
        plan = {}
    intro_strat = plan.get("introduction_strategy", {})
    if not isinstance(intro_strat, dict):
        intro_strat = {}
    for direction in intro_strat.get("search_directions", []):
        if isinstance(direction, dict):
            direction_str = str(direction.get("query") or direction.get("topic") or direction)
        else:
            direction_str = str(direction)
        if not direction_str.strip():
            continue
        tasks.append(
            {
                "section": "Introduction",
                "focus": direction_str,
                "context": f"Hook: {intro_strat.get('hook_hypothesis', '')}. Gap: {intro_strat.get('problem_gap_hypothesis', '')}",
                "search_type": "exploration",
            }
        )
    rw_strat = plan.get("related_work_strategy", {})
    if not isinstance(rw_strat, dict):
        rw_strat = {}
    for sub in rw_strat.get("subsections", []):
        if isinstance(sub, str):
            sub_title = sub.strip()
            if sub_title:
                tasks.append(
                    {
                        "section": f"Related Work: {sub_title}",
                        "focus": sub_title,
                        "context": str(rw_strat.get("overview", "")),
                        "search_type": "exploration",
                    }
                )
            continue
        if not isinstance(sub, dict):
            continue
        queries = sub.get("limitation_search_queries") or [
            f"{sub.get('subsection_title', '')} {sub.get('methodology_cluster', '')}".strip()
        ]
        for q in queries:
            q_str = str(q).strip()
            if not q_str:
                continue
            tasks.append(
                {
                    "section": f"Related Work: {sub.get('subsection_title', 'General')}",
                    "focus": q_str,
                    "context": f"Mission: {sub.get('sota_investigation_mission', '')}. Hypothesis: {sub.get('limitation_hypothesis', '')}",
                    "search_type": "exploration",
                }
            )
    blueprint = outline.get("section_plan") or outline.get("chapter_plan", [])
    if not isinstance(blueprint, list):
        blueprint = []
    for section in blueprint:
        if not isinstance(section, dict):
            continue
        sec_title = section.get("section_title", "Chapter")
        for sub in section.get("subsections", []):
            if not isinstance(sub, dict):
                continue
            sub_title = sub.get("subsection_title", "Section")
            bullets = " ".join(str(b) for b in sub.get("content_bullets", []))
            for hint in sub.get("citation_hints", []):
                hint_str = str(hint).strip()
                if not hint_str:
                    continue
                tasks.append(
                    {
                        "section": f"{sec_title} - {sub_title}",
                        "focus": hint_str,
                        "context": f"Canonical reference for: {bullets}",
                        "search_type": "targeted",
                    }
                )
    return tasks


def _export_modular_chapters(latex_text: str, output_dir: str) -> list[str]:
    chapters_dir = os.path.join(output_dir, "chapters")
    if os.path.exists(chapters_dir):
        shutil.rmtree(chapters_dir, ignore_errors=True)
    os.makedirs(chapters_dir, exist_ok=True)
    exported = []
    matches = list(re.finditer(r"\\chapter\*?\{([^}]+)\}", latex_text))
    if not matches:
        matches = list(re.finditer(r"\\section\*?\{([^}]+)\}", latex_text))
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(latex_text)
        chunk = latex_text[start:end].strip()
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", match.group(1).lower()).strip("_")[:48] or "chapter"
        cpath = os.path.join(chapters_dir, f"ch{idx + 1:02d}_{slug}.tex")
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(chunk + "\n")
        exported.append(cpath)
    return exported


def _update_continuity_ledger(
    continuity_ledger: dict, latex_text: str, exported_chapters: list[str]
) -> dict:
    continuity_ledger["defined_labels"] = sorted(
        set(re.findall(r"\\label\{([^}]+)\}", latex_text))
    )
    continuity_ledger["authored_chapters"] = re.findall(
        r"\\chapter\*?\{([^}]+)\}", latex_text
    )
    continuity_ledger["code_listings"] = re.findall(
        r"caption\s*=\s*\{([^}]+)\}", latex_text
    )
    continuity_ledger["cited_keys"] = sorted(
        set(
            k.strip()
            for m in re.findall(r"\\(?:cite|citet|citep)(?:\[[^\]]*\])?\{([^}]+)\}", latex_text)
            for k in m.split(",")
            if k.strip()
        )
    )
    continuity_ledger["exported_chapter_files"] = [
        os.path.basename(p) for p in exported_chapters
    ]
    return continuity_ledger


async def run_book_orchestra_workflow(
    workflow_args: dict,
    phase_fn,
    log_fn,
    agent_fn,
    parallel_fn,
    pipeline_fn,
) -> dict:
    """
    Core async orchestration entrypoint callable both at top level inside `run_workflow`
    and in unit/integration tests with mocked workflow primitives.
    """
    raw_materials_rel = workflow_args.get(
        "raw_materials_dir", "examples/distributed_llm_systems_book/raw_materials"
    )
    slides_rel = workflow_args.get("slides_file")
    codebase_rel = workflow_args.get("codebase_dir")
    template_rel = workflow_args.get(
        "latex_template_dir", "templates/oreilly_style_book"
    )
    output_rel = workflow_args.get("output_dir", "./book_output_agy")
    proposal_filename = workflow_args.get("proposal_filename", "book_proposal.md")
    notes_filename = workflow_args.get("technical_notes_filename", "technical_notes.md")
    target_audience = workflow_args.get(
        "target_audience", "Senior Software & Systems Engineers"
    )
    book_style = workflow_args.get(
        "book_style", "Practical Architecture & Production Engineering"
    )
    author_voice = str(
        workflow_args.get("author_voice")
        or (
            "Derive and preserve the proposal's own voice. Prefer a warm, exploratory, "
            "reader-facing engineering narrative with concrete examples and varied pacing; "
            "do not default to dense institutional or reference-manual prose."
        )
    ).strip()
    use_plotting = bool(workflow_args.get("use_plotting", True))
    visual_density = _normalize_visual_density(
        workflow_args.get("visual_density", "high")
    )
    strict_latex = bool(workflow_args.get("strict_latex", False))
    max_reflections = int(workflow_args.get("max_reflections", 2))
    raw_cutoff = workflow_args.get("research_cutoff")
    if not raw_cutoff or str(raw_cutoff).strip().lower() in ("current", "auto", "latest", "none", "null"):
        research_cutoff = datetime.datetime.now().strftime("%Y-%m")
    else:
        research_cutoff = str(raw_cutoff).strip()
    model_tier = workflow_args.get(
        "writer_model_name", workflow_args.get("model", "inherit")
    )
    reflection_model_tier = workflow_args.get(
        "reflection_model_name", "pro" if model_tier == "inherit" else model_tier
    )
    plotting_model_tier = workflow_args.get("plotting_model_name", "flash")
    image_model_name = workflow_args.get("image_model_name", "image-generator")
    plotting_max_critic_rounds = max(
        0, int(workflow_args.get("plotting_max_critic_rounds", 3))
    )

    latex_template_dir = (
        template_rel
        if os.path.isabs(template_rel)
        else os.path.join(_WORK_ROOT, template_rel)
    )
    base_dir_arg = (
        output_rel
        if os.path.isabs(output_rel)
        else os.path.join(_WORK_ROOT, output_rel)
    )
    if slides_rel and codebase_rel:
        raise ValueError("slides_file and codebase_dir are mutually exclusive inputs.")
    if slides_rel:
        slides_path = (
            slides_rel
            if os.path.isabs(slides_rel)
            else os.path.join(_WORK_ROOT, slides_rel)
        )
        from utils.slide_ingestion import prepare_slide_materials

        validate_slide_output_workspace(
            slides_path, latex_template_dir, base_dir_arg
        )
        slide_result = prepare_slide_materials(
            slides_path,
            os.path.join(base_dir_arg, "raw_materials"),
            target_audience=target_audience,
            book_style=book_style,
            author_voice=author_voice,
        )
        raw_materials_dir = slide_result["raw_materials_dir"]
        log_fn(
            "Prepared slide-grounded source materials: "
            f"{slide_result['included_slide_count']} included slides/pages, "
            f"{slide_result['hidden_slide_count']} hidden, and "
            f"{slide_result['extracted_figure_count']} reusable visuals."
        )
    elif codebase_rel:
        codebase_path = (
            codebase_rel
            if os.path.isabs(codebase_rel)
            else os.path.join(_WORK_ROOT, codebase_rel)
        )
        from utils.codebase_ingestion import prepare_codebase_materials

        validate_codebase_output_workspace(
            codebase_path, latex_template_dir, base_dir_arg
        )
        codebase_result = prepare_codebase_materials(
            codebase_path,
            os.path.join(base_dir_arg, "raw_materials"),
            target_audience=target_audience,
            book_style=book_style,
            author_voice=author_voice,
        )
        raw_materials_dir = codebase_result["raw_materials_dir"]
        log_fn(
            "Prepared codebase-grounded source materials: "
            f"{codebase_result['included_file_count']} evidence files and "
            f"{codebase_result['redaction_count']} secret-like values redacted."
        )
    else:
        raw_materials_dir = (
            raw_materials_rel
            if os.path.isabs(raw_materials_rel)
            else os.path.join(_WORK_ROOT, raw_materials_rel)
        )

    (
        _,
        _,
        base_path,
        target_raw_path,
        latex_writeup_path,
    ) = prepare_safe_output_workspace(
        raw_materials_dir=raw_materials_dir,
        latex_template_dir=latex_template_dir,
        output_dir=base_dir_arg,
    )
    base_dir = str(base_path)
    target_raw_dir = str(target_raw_path)
    latex_writeup_dir = str(latex_writeup_path)

    figures_dir = os.path.join(latex_writeup_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    user_figures_dir = os.path.join(target_raw_dir, "figures")
    if os.path.exists(user_figures_dir):
        shutil.copytree(user_figures_dir, figures_dir, dirs_exist_ok=True)
    user_figures_info = _catalog_user_figures(figures_dir)

    proposal_path = _resolve_file(
        target_raw_dir,
        proposal_filename,
        ["book_proposal.md", "idea_sparse.md", "idea_dense.md", "idea.md"],
    )
    notes_path = _resolve_file(
        target_raw_dir,
        notes_filename,
        ["technical_notes.md", "experimental_log.md"],
    )
    template_path = os.path.join(latex_writeup_dir, "template.tex")
    guidelines_path = os.path.join(latex_writeup_dir, "guidelines.md")

    proposal_text = _read_text(proposal_path)
    notes_text = _read_text(notes_path)
    slide_source_manifest, slide_source_context = _load_slide_source_context(
        target_raw_dir
    )
    codebase_source_manifest, codebase_source_context = _load_codebase_source_context(
        target_raw_dir
    )
    template_text = _read_text(template_path)
    guidelines_text = _read_text(guidelines_path)

    # =========================================================================
    # PHASE 1: Book Blueprint & Table of Contents
    # =========================================================================
    phase_fn("1. Book Blueprint & Architecture Plan")
    log_fn(f"Designing Book Blueprint for target audience: {target_audience}")

    blueprint_prompt = (
        outline_agent_system_prompt.format(cutoff_date=research_cutoff)
        + f"\n\nPublishing Profile:\n- Target Audience: {target_audience}\n- Book Style: {book_style}\n- Author Voice Contract: {author_voice}\n- Visual Density: {visual_density}\n\n"
        + f"--- 'book_proposal.md' ---\n{proposal_text}\n\n"
        + f"--- 'technical_notes.md' ---\n{notes_text}\n\n"
        + (
            f"--- 'slide_source_manifest.json' (provenance summary) ---\n{slide_source_context}\n\n"
            if slide_source_context
            else ""
        )
        + (
            f"--- 'codebase_source_manifest.json' (provenance summary) ---\n{codebase_source_context}\n\n"
            if codebase_source_context
            else ""
        )
        + f"--- 'template.tex' ---\n{template_text}\n\n"
        + f"--- 'guidelines.md' ---\n{guidelines_text}\n"
    )

    raw_outline = await agent_fn(
        prompt=blueprint_prompt,
        schema=OUTLINE_SCHEMA,
        role="Book Blueprint Architect",
        model=model_tier,
    )
    if isinstance(raw_outline, str):
        raw_outline = parse_agent_json_response(raw_outline) or json.loads(raw_outline)
    outline = OutlineAgent._normalize_book_blueprint(dict(raw_outline))
    if use_plotting:
        before_visual_count = len(outline.get("plotting_plan", []))
        outline = _ensure_visual_coverage(outline, visual_density)
        after_visual_count = len(outline.get("plotting_plan", []))
        if after_visual_count > before_visual_count:
            log_fn(
                f"Visual coverage policy ({visual_density}) added "
                f"{after_visual_count - before_visual_count} chapter-learning diagrams."
            )

    outline_path = os.path.join(base_dir, "outline.json")
    with open(outline_path, "w", encoding="utf-8") as f:
        json.dump(outline, f, indent=4, ensure_ascii=False)
    log_fn(
        f"Book Blueprint saved to {outline_path} "
        f"({len(outline.get('section_plan', []))} main chapters planned)."
    )

    # =========================================================================
    # PHASE 2: Technical Ecosystem Research & Visuals (Parallel Fan-out)
    # =========================================================================
    phase_fn("2. Technical Ecosystem Research & Architecture Visuals")
    lit_output_dir = os.path.join(base_dir, "literature_agent_output")
    os.makedirs(lit_output_dir, exist_ok=True)

    search_tasks = _collect_search_tasks(outline)
    log_fn(f"Executing {len(search_tasks)} reference discovery tasks in parallel...")

    def _make_search_agent(task):
        t_prompt = (
            f"Find authoritative technical references (foundational systems papers, RFCs, "
            f"official engineering specifications, or classic books) published on or before {research_cutoff}.\n"
            f"Section: {task['section']}\n"
            f"Search Focus: {task['focus']}\n"
            f"Context: {task['context']}\n"
            f"CRITICAL: Include verifiable identifiers (doi, arxiv_id, rfc_number, isbn, or official url) "
            f"and source_type ('paper', 'book', 'rfc', 'standard', 'techreport'). "
            f"Return 1 exact canonical reference if targeted, or 3-5 top references if exploration."
        )
        return agent_fn(
            prompt=t_prompt,
            schema=CANDIDATES_SCHEMA,
            role=f"Research: {task['focus'][:32]}",
            type_name="research",
            model="flash" if model_tier == "inherit" else model_tier,
        )

    if search_tasks:
        discovery_results = await parallel_fn(search_tasks, _make_search_agent)
    else:
        discovery_results = []

    registry = {}
    grounding_notes_blob = f"{proposal_text}\n{notes_text}"
    for task, res in zip(search_tasks, discovery_results):
        if isinstance(res, str):
            res = parse_agent_json_response(res) or {}
        if not isinstance(res, dict):
            continue
        task_grounding = f"{grounding_notes_blob}\n{task.get('focus', '')}\n{task.get('context', '')}"
        cands = res.get("candidates", [])
        if not isinstance(cands, list):
            continue
        for cand in cands:
            if not isinstance(cand, dict):
                continue
            title = str(cand.get("title") or "").strip()
            if not title:
                continue
            if not is_reference_before_cutoff(cand, research_cutoff):
                log_fn(
                    f"Skipping reference after cutoff ({research_cutoff}): {title} ({cand.get('year')})"
                )
                continue
            is_verified, ver_src = has_verifiable_identifier_or_grounding(
                cand, grounding_text=task_grounding
            )
            if not is_verified:
                log_fn(f"Skipping unverified reference (no DOI/URL/RFC/ISBN or grounding): {title}")
                continue
            norm = re.sub(r"[^a-z0-9]", "", title.lower())
            if norm in registry:
                continue
            authors = cand.get("authors") or ["Technical Authors"]
            year = int(cand.get("year") or 2024)
            key = _generate_citation_key(authors, year, title)
            registry[norm] = {
                "citation_key": key,
                "title": title,
                "authors": authors,
                "venue": cand.get("venue") or "Systems & Engineering Literature",
                "year": year,
                "abstract": cand.get("summary") or cand.get("reason") or title,
                "found_in_section": task["section"],
                "reason": cand.get("reason", ""),
                "source_type": cand.get("source_type"),
                "doi": cand.get("doi"),
                "arxiv_id": cand.get("arxiv_id"),
                "rfc_number": cand.get("rfc_number"),
                "isbn": cand.get("isbn"),
                "url": cand.get("url"),
                "verification_source": ver_src,
            }

    papers_list = list(registry.values())
    # Deduplicate citation keys & build properly typed BibTeX + citation_map
    seen_keys = set()
    bib_entries = []
    citation_map = {}
    section_to_keys = {}
    for p in papers_list:
        base_key = p["citation_key"]
        ukey = base_key
        sfx = "a"
        while ukey in seen_keys:
            ukey = f"{base_key}{sfx}"
            sfx = chr(ord(sfx) + 1)
        p["citation_key"] = ukey
        seen_keys.add(ukey)
        bib_entries.append(format_bibtex_entry(p))
        citation_map[ukey] = {
            "citation_key": ukey,
            "title": p["title"],
            "authors": p["authors"],
            "venue": p["venue"],
            "year": p["year"],
            "abstract": p["abstract"],
            "source_type": p.get("source_type"),
            "doi": p.get("doi"),
            "arxiv_id": p.get("arxiv_id"),
            "rfc_number": p.get("rfc_number"),
            "isbn": p.get("isbn"),
            "url": p.get("url"),
            "verification_source": p.get("verification_source"),
        }
        sec = p["found_in_section"]
        section_to_keys.setdefault(sec, []).append(ukey)

    # Inject citation candidates into outline
    plan = outline.get("intro_related_work_plan", {})
    if isinstance(plan, dict):
        if not isinstance(plan.get("introduction_strategy"), dict):
            plan["introduction_strategy"] = {}
        plan["introduction_strategy"]["citation_candidates"] = list(
            set(section_to_keys.get("Introduction", []))
        )
        rw = plan.get("related_work_strategy")
        if isinstance(rw, dict):
            for idx, sub in enumerate(rw.get("subsections", [])):
                if isinstance(sub, dict):
                    k = f"Related Work: {sub.get('subsection_title', 'General')}"
                    sub["citation_candidates"] = section_to_keys.get(k, [])
                elif isinstance(sub, str):
                    k = f"Related Work: {sub.strip()}"
                    rw["subsections"][idx] = {
                        "subsection_title": sub.strip(),
                        "citation_candidates": section_to_keys.get(k, []),
                    }
    for sec in outline.get("section_plan", []):
        if not isinstance(sec, dict):
            continue
        sec_title = sec.get("section_title", "Chapter")
        for sub in sec.get("subsections", []):
            if isinstance(sub, dict):
                k = f"{sec_title} - {sub.get('subsection_title', 'Section')}"
                sub["citation_candidates"] = section_to_keys.get(k, [])

    with open(os.path.join(lit_output_dir, "references.bib"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(bib_entries))
    with open(
        os.path.join(lit_output_dir, "citation_map.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(citation_map, f, indent=4, ensure_ascii=False)
    with open(
        os.path.join(lit_output_dir, "outline_v1.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(outline, f, indent=4, ensure_ascii=False)
    shutil.copy(os.path.join(lit_output_dir, "outline_v1.json"), outline_path)
    if bib_entries:
        shutil.copy(
            os.path.join(lit_output_dir, "references.bib"),
            os.path.join(latex_writeup_dir, "references.bib"),
        )

    # Write Preface & Chapter 1 (Foundations & Ecosystem)
    lit_writer_sys = literature_review_agent_writter_prompt.format(
        paper_count=len(papers_list),
        min_cite_paper_count=max(1, int(len(papers_list) * 0.8)) if papers_list else 0,
        cutoff_date=research_cutoff,
    )
    lit_context_payload = {
        "template.tex": template_text,
        "book_metadata": outline.get("book_metadata", {}),
        "intro_related_work_plan": outline.get("intro_related_work_plan", {}),
        "project_idea": proposal_text,
        "project_experimental_log": notes_text,
        "citation_checklist": list(citation_map.keys()),
        "collected_papers": papers_list,
    }
    lit_latex_raw = await agent_fn(
        prompt=(
            f"{lit_writer_sys}\n\n"
            f"Generate the LaTeX for the Preface and Chapter 1 (Foundations & Ecosystem Survey) "
            f"in template.tex while keeping all downstream chapters untouched.\n\n"
            f"Context Payload:\n{json.dumps(lit_context_payload, ensure_ascii=False)}"
        ),
        schema={"type": "string"},
        role="Preface & Foundations Author",
        model=model_tier,
    )
    updated_template_tex = parse_agent_latex_response(str(lit_latex_raw))
    updated_template_tex = _ensure_body_reading_rhythm(updated_template_tex)
    updated_template_tex, _ = audit_and_clean_citations(
        updated_template_tex, set(citation_map.keys())
    )
    with open(
        os.path.join(lit_output_dir, "updated_template.tex"), "w", encoding="utf-8"
    ) as f:
        f.write(updated_template_tex)
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(updated_template_tex)

    # Generate Architecture Diagrams & Benchmark Plots in Parallel if enabled
    figures_info = list(user_figures_info)
    if figures_info:
        log_fn(f"Registered {len(figures_info)} user-supplied figure(s).")
    raw_plotting = outline.get("plotting_plan", []) if use_plotting else []
    plotting_plan = [fp for fp in raw_plotting if isinstance(fp, dict)] if isinstance(raw_plotting, list) else []
    if plotting_plan:
        log_fn(
            f"Generating {len(plotting_plan)} architecture diagrams and benchmark plots in parallel "
            f"(max_critic_rounds={plotting_max_critic_rounds})..."
        )

        # Limit concurrent image-generator calls to avoid burst 429 RESOURCE_EXHAUSTED errors
        image_gen_semaphore = asyncio.Semaphore(2)
        _image_schema = {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Exact absolute path where the generated PNG or JPEG image file was saved.",
                },
                "error": {
                    "type": "string",
                    "description": "Error reason if generation failed, or empty string on success.",
                },
            },
            "required": ["image_path"],
        }

        def _is_quota_error_response(resp) -> bool:
            text = json.dumps(resp, ensure_ascii=False) if isinstance(resp, dict) else str(resp or "")
            low = text.lower()
            return any(
                tok in low
                for tok in (
                    "resource_exhausted",
                    "429",
                    "too many requests",
                    "quota",
                    "rate limit",
                )
            )

        async def _generate_with_image_agent(
            fig_id: str,
            visual_prompt: str,
            role_label: str,
            max_attempts: int = 2,
        ) -> tuple[bytes | None, str | None]:
            for attempt in range(max(1, max_attempts)):
                try:
                    async with image_gen_semaphore:
                        diag_resp = await agent_fn(
                            prompt=visual_prompt,
                            schema=_image_schema,
                            role=role_label if attempt == 0 else f"{role_label} (retry {attempt})",
                            type_name=image_model_name,
                        )
                    raw_bytes, fmt = extract_safe_generated_image_bytes(
                        diag_resp, figures_dir=figures_dir
                    )
                    if raw_bytes and fmt in (".png", ".jpg"):
                        return raw_bytes, fmt
                    if _is_quota_error_response(diag_resp):
                        log_fn(
                            f"⚠️ Image generation quota/capacity limit hit for '{fig_id}'; stopping retries."
                        )
                        return None, None
                except Exception as exc:
                    if _is_quota_error_response(str(exc)):
                        log_fn(
                            f"⚠️ Image generation quota/capacity error for '{fig_id}'; stopping retries."
                        )
                        return None, None
            return None, None

        async def _generate_single_visual(fig_plan: dict) -> dict:
            fig_id = sanitize_slug(
                fig_plan.get("figure_id", "fig_architecture"),
                default="fig_architecture",
            )
            title = fig_plan.get("title", fig_id)
            plot_type = str(fig_plan.get("plot_type") or "diagram").lower()
            objective = fig_plan.get("objective", "")
            aspect_ratio = fig_plan.get("aspect_ratio", "16:9")
            pedagogical_purpose = fig_plan.get("pedagogical_purpose", "")
            chapter_title = str(fig_plan.get("chapter_title", "")).strip()
            chapter_blueprint = next(
                (
                    section
                    for section in outline.get("section_plan", [])
                    if isinstance(section, dict)
                    and str(section.get("section_title", "")).strip() == chapter_title
                ),
                {},
            )

            raw_bytes: bytes | None = None
            ext: str | None = None
            generated_by = "failed"

            if "plot" in plot_type or "chart" in plot_type:
                if _find_matplotlib_python() is not None:
                    code_prompt = (
                        f"{PLOT_PLANNER_AGENT_SYSTEM_PROMPT}\n\n"
                        f"{PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT}\n\n"
                        f"Title: {title}\nObjective: {objective}\nAspect Ratio: {aspect_ratio}\n"
                        f"Raw Technical Notes & Benchmarks:\n{notes_text}\n\n"
                        f"STRICT SANDBOX RULES:\n"
                        f"- ONLY import from: matplotlib, matplotlib.pyplot, matplotlib.patches, "
                        f"matplotlib.ticker, matplotlib.cm, matplotlib.colors, matplotlib.lines, "
                        f"matplotlib.gridspec, matplotlib.patheffects, numpy, math, statistics.\n"
                        f"- NEVER import os, sys, platform, pathlib, or matplotlib.font_manager "
                        f"(Korean/Unicode fonts and minus signs are ALREADY pre-configured by the sandbox).\n"
                        f"- NEVER call plt.savefig() or plt.show(); the sandbox captures the active figure automatically.\n"
                        f"- Return ONLY clean, runnable Python code wrapped in ```python ... ```."
                    )
                    code_resp = await agent_fn(
                        prompt=code_prompt,
                        schema={"type": "string"},
                        role=f"Benchmark Plotter: {fig_id}",
                        model=plotting_model_tier,
                    )
                    current_code = str(code_resp)
                    b64_img = execute_plot_code_worker(
                        current_code, allow_placeholder_fallback=False
                    )

                    for critic_round in range(plotting_max_critic_rounds):
                        if b64_img and critic_round > 0:
                            break
                        critic_prompt = (
                            f"You are a Senior Performance Engineer & Matplotlib Code Reviewer.\n"
                            f"Review the following Matplotlib plot code for numerical fidelity, clarity, and sandbox compliance.\n\n"
                            f"Figure ID: {fig_id}\nTitle: {title}\nObjective: {objective}\n"
                            f"Execution Succeeded: {bool(b64_img)}\n"
                            f"Current Matplotlib Code:\n{current_code}\n\n"
                            f"SANDBOX RULES: Only import matplotlib.pyplot/patches/ticker/lines/gridspec, numpy, math, statistics. "
                            f"Do NOT import platform, os, sys, or font_manager. Do NOT call plt.savefig() or plt.show().\n\n"
                            f"OUTPUT FORMAT: If Execution Succeeded is True and the plot code is valid and accurate, "
                            f"reply with the exact text: No changes needed.\n"
                            f"Otherwise reply ONLY with the corrected runnable Python code inside ```python ... ``` (do NOT wrap in JSON)."
                        )
                        critic_resp = await agent_fn(
                            prompt=critic_prompt,
                            schema={"type": "string"},
                            role=f"Plot Critic ({fig_id} r{critic_round + 1})",
                            model=plotting_model_tier,
                        )
                        critic_str = str(critic_resp).strip()
                        if "no changes needed" in critic_str.lower() and b64_img:
                            break
                        if "```python" in critic_str:
                            cand_b64 = execute_plot_code_worker(
                                critic_str, allow_placeholder_fallback=False
                            )
                            if cand_b64:
                                current_code = critic_str
                                b64_img = cand_b64
                                break

                    if b64_img:
                        cand_bytes = base64.b64decode(b64_img)
                        cand_fmt = detect_media_format(cand_bytes)
                        if cand_fmt in (".png", ".jpg"):
                            raw_bytes, ext = cand_bytes, cand_fmt
                            generated_by = "matplotlib"

                # If matplotlib is not installed or plot code failed, render a crisp data chart via image-generator
                if raw_bytes is None:
                    chart_prompt = (
                        f"{PLOT_PLANNER_AGENT_SYSTEM_PROMPT}\n\n"
                        f"Generate a crisp, publication-grade white-background engineering data chart image "
                        f"(aspect ratio {aspect_ratio}) for:\n"
                        f"Title: {title}\n"
                        f"Objective: {objective}\n\n"
                        f"Teaching purpose: {pedagogical_purpose}\n"
                        f"Use ONLY the exact quantitative figures from the technical notes below (never fabricate numbers). "
                        f"Keep labels sharp, legible, and clean with high contrast on a pure white background.\n\n"
                        f"Technical Notes:\n{notes_text[:8000]}\n\n"
                        f"Save the generated image file and return its exact absolute path in `image_path`."
                    )
                    raw_bytes, ext = await _generate_with_image_agent(
                        fig_id=fig_id,
                        visual_prompt=chart_prompt,
                        role_label=f"Chart Illustrator: {fig_id}",
                        max_attempts=min(2, max(1, plotting_max_critic_rounds)),
                    )
                    if raw_bytes and ext:
                        generated_by = "image-generator"
            else:
                diag_spec = (
                    f"Title: {title}\n"
                    f"Objective: {objective}\n"
                    f"Teaching purpose: {pedagogical_purpose}\n"
                    f"Aspect Ratio: {aspect_ratio}\n"
                    f"Chapter Blueprint:\n{json.dumps(chapter_blueprint, ensure_ascii=False)[:6000]}\n"
                    f"Architecture Context:\n{proposal_text[:6000]}"
                )
                diag_prompt = (
                    f"{DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT}\n\n"
                    f"Generate a crisp, pure white-background technical architecture diagram "
                    f"(O'Reilly / DDIA editorial style, legible text, no baked-in figure number caption) for:\n"
                    f"{diag_spec}\n\n"
                    f"Use the manuscript's language for labels. Keep the visual focused: prefer 5–12 labeled "
                    f"elements, explicit arrow direction, a clear reading order, and generous whitespace. "
                    f"Do not add components or claims absent from the supplied blueprint/context.\n\n"
                    f"Save the generated image file and return its exact absolute path in `image_path`."
                )
                raw_bytes, ext = await _generate_with_image_agent(
                    fig_id=fig_id,
                    visual_prompt=diag_prompt,
                    role_label=f"Architecture Illustrator: {fig_id}",
                    max_attempts=min(2, max(1, plotting_max_critic_rounds)),
                )
                if raw_bytes and ext:
                    generated_by = "image-generator"

            caption = f"{title}: {objective}".strip(": ")
            if not raw_bytes or ext not in (".png", ".jpg"):
                return {
                    "figure_id": fig_id,
                    "title": title,
                    "name": None,
                    "image_path": None,
                    "caption": caption,
                    "chapter_title": fig_plan.get("chapter_title", ""),
                    "placement_hint": fig_plan.get("placement_hint", ""),
                    "pedagogical_purpose": fig_plan.get(
                        "pedagogical_purpose", ""
                    ),
                    "generated_by": "failed",
                    "status": "failed",
                    "error": "No valid PNG or JPEG was produced after generation attempts.",
                    "size_bytes": 0,
                }

            filename = f"{fig_id}{ext}"
            if os.path.exists(os.path.join(figures_dir, filename)):
                stem = f"{fig_id}_generated"
                filename = f"{stem}{ext}"
                serial = 2
                while os.path.exists(os.path.join(figures_dir, filename)):
                    filename = f"{stem}_{serial}{ext}"
                    serial += 1
            img_rel = f"figures/{filename}"
            img_abs_path = resolve_safe_child_path(figures_dir, filename)
            img_abs = str(img_abs_path)
            os.makedirs(os.path.dirname(img_abs), exist_ok=True)
            with open(img_abs, "wb") as fh:
                fh.write(raw_bytes)

            return {
                "figure_id": fig_id,
                "title": title,
                "name": filename,
                "image_path": img_rel,
                "caption": caption,
                "chapter_title": fig_plan.get("chapter_title", ""),
                "placement_hint": fig_plan.get("placement_hint", ""),
                "pedagogical_purpose": fig_plan.get("pedagogical_purpose", ""),
                "generated_by": generated_by,
                "status": "ready",
                "size_bytes": len(raw_bytes),
            }

        plot_results = await parallel_fn(plotting_plan, _generate_single_visual)
        ready_results = [
            result
            for result in plot_results
            if isinstance(result, dict)
            and result.get("status") == "ready"
            and result.get("name")
        ]
        failed_count = len(plot_results) - len(ready_results)
        log_fn(
            f"Visual generation complete: {len(ready_results)}/{len(plot_results)} real images generated; "
            f"{failed_count} failed plan(s) were excluded from the manuscript."
        )
        with open(
            os.path.join(base_dir, "plotting_results.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(plot_results, f, indent=4, ensure_ascii=False)
        figures_info.extend(ready_results)

    with open(os.path.join(figures_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(figures_info, f, indent=4, ensure_ascii=False)

    # =========================================================================
    # PHASE 3: Multi-Chapter Authoring with Continuity Ledger
    # =========================================================================
    phase_fn("3. Multi-Chapter Authoring & Continuity Ledger")
    book_meta = outline.get("book_metadata", {})
    if not isinstance(book_meta, dict):
        book_meta = {}
    continuity_ledger = {
        "book_title": book_meta.get("title", ""),
        "target_audience": target_audience,
        "author_voice": author_voice,
        "running_case_study": book_meta.get("running_case_study", ""),
        "terminology_glossary": book_meta.get("terminology_glossary", {}),
        "existing_chapters_or_sections": re.findall(
            r"\\chapter\*?\{([^}]+)\}", updated_template_tex
        ),
        "defined_labels": re.findall(r"\\label\{([^}]+)\}", updated_template_tex),
    }

    figures_context = (
        "\n".join(
            f"- Filename: {fig['name']} | Caption: {fig['caption']}"
            f" | Chapter: {fig.get('chapter_title') or 'best editorial fit'}"
            f" | Placement: {fig.get('placement_hint') or 'near first relevant explanation'}"
            f" | Teaching purpose: {fig.get('pedagogical_purpose') or 'clarify the surrounding concept'}"
            for fig in figures_info
        )
        or "No figures provided."
    )
    citation_context = (
        "\n".join(
            f"- Key: {k} | {v['title']} ({v['year']}): {v['abstract']}"
            for k, v in citation_map.items()
        )
        or "No citation data provided."
    )

    author_prompt = (
        f"{section_writing_agent_prompt}\n\n"
        f"--- INPUT: book_continuity_ledger.json ---\n{json.dumps(continuity_ledger, indent=2, ensure_ascii=False)}\n\n"
        f"--- INPUT: author_voice ---\n{author_voice}\n\n"
        f"--- INPUT: outline.json ---\n{json.dumps(outline, indent=2, ensure_ascii=False)}\n\n"
        f"--- INPUT: citation_map.json ---\n{citation_context}\n\n"
        f"--- INPUT: book_proposal.md ---\n{proposal_text}\n\n"
        f"--- INPUT: technical_notes.md ---\n{notes_text}\n\n"
        + (
            f"--- INPUT: slide source provenance ---\n{slide_source_context}\n\n"
            if slide_source_context
            else ""
        )
        + (
            f"--- INPUT: codebase source provenance ---\n{codebase_source_context}\n\n"
            if codebase_source_context
            else ""
        )
        + f"--- INPUT: figures_list ---\n{figures_context}\n\n"
        f"--- INPUT: guidelines.md ---\n{guidelines_text}\n\n"
        f"--- INPUT: template.tex ---\n{updated_template_tex}\n\n"
        f"Return the FULL completed LaTeX manuscript wrapped in ```latex ... ```."
    )

    draft_latex_raw = await agent_fn(
        prompt=author_prompt,
        schema={"type": "string"},
        role="Principal Technical Book Author",
        model=model_tier,
    )
    current_tex = parse_agent_latex_response(str(draft_latex_raw))
    current_tex = _ensure_body_reading_rhythm(current_tex)
    current_tex, _ = audit_and_clean_citations(
        current_tex, set(citation_map.keys())
    )
    raw_draft_tex_path = os.path.join(latex_writeup_dir, "raw_draft_book.tex")
    with open(raw_draft_tex_path, "w", encoding="utf-8") as f:
        f.write(current_tex)

    exported_chapters = _export_modular_chapters(current_tex, latex_writeup_dir)
    _update_continuity_ledger(continuity_ledger, current_tex, exported_chapters)
    with open(
        os.path.join(base_dir, "book_continuity_ledger.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(continuity_ledger, f, indent=4, ensure_ascii=False)
    log_fn(
        f"Exported {len(exported_chapters)} modular chapters and updated book_continuity_ledger.json."
    )

    # =========================================================================
    # PHASE 4: 3-Persona Editorial Review Board & Typesetting QA
    # =========================================================================
    phase_fn("4. 3-Persona Editorial Review Board & Typesetting QA")
    from pathlib import Path

    refinement_workdir = str(
        safe_prepare_managed_subdir(
            Path(base_dir) / "content_refinement_workdir", role="refinement_workdir"
        )
    )
    shutil.copytree(latex_writeup_dir, refinement_workdir, dirs_exist_ok=True)
    os.makedirs(os.path.join(refinement_workdir, "peer_reviews"), exist_ok=True)

    initial_tex_path = os.path.join(refinement_workdir, "initial_draft.tex")
    initial_pdf_path = os.path.join(refinement_workdir, "initial_draft.pdf")
    with open(initial_tex_path, "w", encoding="utf-8") as f:
        f.write(current_tex)
    initial_compiled = compile_latex(
        refinement_workdir, initial_pdf_path, "initial_draft"
    )
    current_render_mode = get_last_compile_mode()
    if not initial_compiled or not os.path.exists(initial_pdf_path):
        raise RuntimeError(
            f"Initial LaTeX book manuscript failed syntax validation or compilation: {initial_tex_path}"
        )
    final_valid_pdf_path = initial_pdf_path

    async def _run_editorial_review_board(tex_text: str, label: str) -> tuple[dict, float]:
        # Editorial review is semantic, so always use source-derived text. Preview PDFs
        # are visual conveniences and may lose indentation or unsupported TeX details.
        manuscript_text = latex_to_markdown(tex_text)

        def _spawn_persona_reviewer(persona_tuple):
            persona_id, _ = persona_tuple
            sys_prompt = get_agentreview_system_prompt(
                is_knowledgeable=True,
                is_responsible=True,
                is_benign=True,
                persona=persona_id,
            )
            r_prompt = (
                f"{sys_prompt}\n\n{AGENTREVIEW_INSTRUCTIONS}\n\n"
                f"Here is the technical book manuscript you are asked to review:\n```\n{manuscript_text}\n```"
            )
            return agent_fn(
                prompt=r_prompt,
                schema=REVIEW_SCHEMA,
                role=f"Review Board ({label}): {persona_id}",
                model=reflection_model_tier,
            )

        raw_reviews = await parallel_fn(REVIEW_BOARD_PERSONAS, _spawn_persona_reviewer)
        parsed_reviews = []
        for r in raw_reviews:
            if isinstance(r, str):
                r = parse_agent_json_response(r) or {}
            parsed_reviews.append(_populate_score_aliases(dict(r)))

        meta_prompt = (
            meta_reviewer_system_prompt.format(reviewer_count=len(parsed_reviews))
            + "\n\n"
            + AGENTREVIEW_INSTRUCTIONS
            + "\n\n"
            + "\n".join(
                f"Review {idx+1} ({REVIEW_BOARD_PERSONAS[idx][0]}):\n```json\n{json.dumps(rv, ensure_ascii=False)}\n```"
                for idx, rv in enumerate(parsed_reviews)
            )
        )
        meta_raw = await agent_fn(
            prompt=meta_prompt,
            schema=REVIEW_SCHEMA,
            role=f"Editor-in-Chief Meta-Review ({label})",
            model=reflection_model_tier,
        )
        if isinstance(meta_raw, str):
            meta_raw = parse_agent_json_response(meta_raw) or parsed_reviews[0]
        meta_review = _populate_score_aliases(dict(meta_raw))
        score = float(meta_review.get("Overall", 0))
        review_out = os.path.join(
            refinement_workdir, "peer_reviews", f"review_{label}.json"
        )
        with open(review_out, "w", encoding="utf-8") as f:
            json.dump(meta_review, f, indent=4, ensure_ascii=False)
        log_fn(f"Editorial Review Board score for {label}: {score}/10")
        return meta_review, score

    current_review, current_score = await _run_editorial_review_board(
        current_tex, "v0"
    )
    worklogs = {}

    review_sub_axes = (
        "Technical_Depth",
        "Practical_Utility",
        "Code_and_Architecture_Quality",
        "Pedagogical_Clarity",
        "Structural_Coherence",
        "Editorial_Polish",
        "Voice_Fidelity",
        "Readability",
        "Warmth",
    )

    def _extract_sub_scores(rev_dict: dict) -> dict:
        out = {"Overall": float(rev_dict.get("Overall", 0.0))}
        for ax in review_sub_axes:
            try:
                out[ax] = float(rev_dict.get(ax, 0.0))
            except (TypeError, ValueError):
                out[ax] = 0.0
        return out

    for round_idx in range(max_reflections):
        v_label = f"v{round_idx + 1}"
        rev_prompt = (
            f"{content_refinement_agent_system_prompt}\n\n"
            f"EDITORIAL REVISION ITERATION {round_idx + 1}.\n"
            f"Current Editorial Review Board Score: {current_score}/10\n"
            f"Review Feedback:\n{json.dumps(current_review, indent=2, ensure_ascii=False)}\n\n"
            f"Author Voice Contract (binding):\n{author_voice}\n\n"
            f"Book Proposal (binding voice, scope, and chapter promise):\n{proposal_text}\n\n"
            f"Publisher Guidelines:\n{guidelines_text}\n\n"
            f"Technical Notes (Ground Truth):\n{notes_text}\n\n"
            f"Citation Map:\n{json.dumps(citation_map, indent=2, ensure_ascii=False)}\n\n"
            f"Current LaTeX Manuscript:\n{current_tex}\n\n"
            f"Return JSON with keys 'worklog' and 'latex' (full revised LaTeX code)."
        )
        rev_res = await agent_fn(
            prompt=rev_prompt,
            schema=REVISION_SCHEMA,
            role=f"Editorial Revision ({v_label})",
            model=reflection_model_tier,
        )
        if isinstance(rev_res, str):
            rev_res = parse_agent_json_response(rev_res) or {}
        new_tex = parse_agent_latex_response(str(rev_res.get("latex", "")))
        if not new_tex:
            break
        new_tex = _ensure_body_reading_rhythm(new_tex)
        new_tex, _ = audit_and_clean_citations(new_tex, set(citation_map.keys()))

        cur_name = f"refined_book_{v_label}"
        cur_tex_path = os.path.join(refinement_workdir, f"{cur_name}.tex")
        cur_pdf_path = os.path.join(refinement_workdir, f"{cur_name}.pdf")
        with open(cur_tex_path, "w", encoding="utf-8") as f:
            f.write(new_tex)
        compiled_ok = compile_latex(refinement_workdir, cur_pdf_path, cur_name)
        candidate_render_mode = get_last_compile_mode()
        if not compiled_ok or not os.path.exists(cur_pdf_path):
            log_fn(f"Revision {v_label} failed LaTeX validation/compilation; reverting.")
            continue

        new_review, new_score = await _run_editorial_review_board(
            new_tex, v_label
        )
        old_metrics = _extract_sub_scores(current_review)
        new_metrics = _extract_sub_scores(new_review)
        deltas = {
            ax: new_metrics[ax] - old_metrics[ax] for ax in review_sub_axes
        }
        total_gain = sum(d for d in deltas.values() if d > 0)
        total_drop = sum(abs(d) for d in deltas.values() if d < 0)

        if new_score > current_score:
            worklogs[v_label] = {
                "round": round_idx + 1,
                "agent_plan": rev_res.get("worklog", {}),
                "score_before": current_score,
                "score_after": new_score,
                "scores_before": old_metrics,
                "scores_after": new_metrics,
                "deltas": deltas,
                "total_gain": total_gain,
                "total_drop": total_drop,
                "outcome": "ACCEPTED_SCORE_INCREASE",
            }
            current_tex = new_tex
            current_score = new_score
            current_review = new_review
            final_valid_pdf_path = cur_pdf_path
            current_render_mode = candidate_render_mode
        elif new_score < current_score:
            worklogs[v_label] = {
                "round": round_idx + 1,
                "agent_plan": rev_res.get("worklog", {}),
                "score_before": current_score,
                "score_after": new_score,
                "scores_before": old_metrics,
                "scores_after": new_metrics,
                "deltas": deltas,
                "total_gain": total_gain,
                "total_drop": total_drop,
                "outcome": "REJECTED_SCORE_DECREASE",
            }
            break
        else:
            if total_drop > total_gain:
                log_fn(
                    f"Revision {v_label} kept Overall={new_score} but sub-axes degraded (drop={total_drop} > gain={total_gain}); reverting."
                )
                worklogs[v_label] = {
                    "round": round_idx + 1,
                    "agent_plan": rev_res.get("worklog", {}),
                    "score_before": current_score,
                    "score_after": new_score,
                    "scores_before": old_metrics,
                    "scores_after": new_metrics,
                    "deltas": deltas,
                    "total_gain": total_gain,
                    "total_drop": total_drop,
                    "outcome": "REJECTED_DEGRADATION",
                }
                break
            worklogs[v_label] = {
                "round": round_idx + 1,
                "agent_plan": rev_res.get("worklog", {}),
                "score_before": current_score,
                "score_after": new_score,
                "scores_before": old_metrics,
                "scores_after": new_metrics,
                "deltas": deltas,
                "total_gain": total_gain,
                "total_drop": total_drop,
                "outcome": "ACCEPTED_NEUTRAL_IMPROVEMENT",
            }
            current_tex = new_tex
            current_score = new_score
            current_review = new_review
            final_valid_pdf_path = cur_pdf_path
            current_render_mode = candidate_render_mode

    with open(
        os.path.join(refinement_workdir, "content_refinement_worklog.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(worklogs, f, indent=4, ensure_ascii=False)

    # Production Typesetting QA pass (combines rendered PDF page snapshots + static layout diagnostics)
    layout_diagnostics = inspect_book_layout_issues(
        current_tex, cwd=refinement_workdir
    )

    if current_render_mode == "preview":
        log_fn(
            "Preview render detected: skipping screenshot-driven LaTeX rewriting; "
            "using deterministic source diagnostics only."
        )
        polished_tex = current_tex
    else:
        fmt_screenshots_dir = os.path.join(
            refinement_workdir, "pdf_screenshots", "format_refinement", "v0"
        )
        grid_images = pdf_to_grid_images(final_valid_pdf_path, fmt_screenshots_dir)
        fmt_prompt = (
            f"{format_agent_system_prompt}\n\n"
            f"Rendered Page Snapshots for Visual Inspection: {grid_images}\n"
            f"Automated Layout & Overflow Diagnostics:\n{json.dumps(layout_diagnostics, indent=2, ensure_ascii=False)}\n\n"
            f"Verify code listings (breaklines=true, <=88 chars/line), booktabs table widths, "
            f"callout boxes, figure paths (figures/ prefix), and citation keys.\n\n"
            f"Publisher Guidelines:\n{guidelines_text}\n\n"
            f"Current LaTeX Manuscript:\n{current_tex}\n\n"
            f"Return the FULL polished LaTeX wrapped in ```latex ... ```."
        )
        fmt_raw = await agent_fn(
            prompt=fmt_prompt,
            schema={"type": "string"},
            role="Production Typesetting QA Editor",
            model=reflection_model_tier,
        )
        polished_tex = parse_agent_latex_response(str(fmt_raw)) or current_tex
    polished_tex = _ensure_body_reading_rhythm(polished_tex)
    polished_tex, _ = audit_and_clean_citations(
        polished_tex, set(citation_map.keys())
    )

    # Validate & compile the polished candidate and re-verify layout diagnostics via pipeline_fn
    candidate_fmt_tex = os.path.join(refinement_workdir, "formatted_candidate_v1.tex")
    candidate_fmt_pdf = os.path.join(refinement_workdir, "formatted_candidate_v1.pdf")
    with open(candidate_fmt_tex, "w", encoding="utf-8") as f:
        f.write(polished_tex)

    def _count_issues(diag: dict) -> int:
        c = sum(
            1
            for v in (diag.get("figure_and_tables") or {}).values()
            if isinstance(v, dict)
            and str(v.get("detected_issue", "none")).lower() != "none"
        )
        c += sum(
            1
            for x in (diag.get("other_issues") or [])
            if (isinstance(x, dict) and str(x.get("detected_issue", "none")).lower() != "none")
            or (isinstance(x, str) and x.lower() != "none")
        )
        return c

    if compile_latex(refinement_workdir, candidate_fmt_pdf, "formatted_candidate_v1"):
        after_layout = inspect_book_layout_issues(polished_tex, cwd=refinement_workdir)
        if _count_issues(after_layout) <= _count_issues(layout_diagnostics):
            current_tex = polished_tex
            final_valid_pdf_path = candidate_fmt_pdf
            fmt_after_dir = os.path.join(
                refinement_workdir, "pdf_screenshots", "format_refinement", "v1"
            )
            pdf_to_grid_images(candidate_fmt_pdf, fmt_after_dir)
        else:
            log_fn(
                "Typesetting QA candidate introduced layout issues; keeping previous manuscript."
            )
    else:
        log_fn(
            "Typesetting QA candidate failed LaTeX compilation; keeping last valid manuscript."
        )

    # Save final artifacts and ensure base_dir is 100% self-contained
    final_refined_tex = os.path.join(refinement_workdir, "final_refined_book.tex")
    final_refined_pdf = os.path.join(refinement_workdir, "final_refined_book.pdf")
    with open(final_refined_tex, "w", encoding="utf-8") as f:
        f.write(current_tex)
    final_compiled = compile_latex(
        refinement_workdir,
        final_refined_pdf,
        "final_refined_book",
        strict_latex=strict_latex,
    )
    render_mode = get_last_compile_mode()
    if not final_compiled or not os.path.exists(final_refined_pdf):
        if strict_latex or not os.path.exists(final_valid_pdf_path):
            raise RuntimeError(
                f"Final book manuscript failed LaTeX compilation (strict_latex={strict_latex}, mode={render_mode})."
            )
        shutil.copy(final_valid_pdf_path, final_refined_pdf)
        render_mode = "preview"

    is_publication_grade = render_mode in ("xelatex", "lualatex", "tectonic", "pdflatex")
    final_book_pdf = os.path.join(base_dir, "final_book.pdf")
    preview_book_pdf = os.path.join(base_dir, "preview_book.pdf")
    final_book_tex = os.path.join(base_dir, "final_book.tex")
    final_book_md = os.path.join(base_dir, "final_book.md")
    manifest_path = os.path.join(base_dir, "build_manifest.json")

    # Ensure stale final_book.pdf from earlier runs does not mislead users on preview-only builds
    if is_publication_grade:
        shutil.copy(final_refined_pdf, final_book_pdf)
        delivered_pdf = final_book_pdf
    else:
        if os.path.exists(final_book_pdf):
            os.remove(final_book_pdf)
        shutil.copy(final_refined_pdf, preview_book_pdf)
        delivered_pdf = preview_book_pdf

    with open(final_book_tex, "w", encoding="utf-8") as f:
        f.write(current_tex)
    shutil.copy(final_book_tex, os.path.join(latex_writeup_dir, "main.tex"))

    # Copy references.bib and figures/ into base_dir so final_book.tex compiles standalone in base_dir
    src_bib = os.path.join(latex_writeup_dir, "references.bib")
    if os.path.exists(src_bib):
        shutil.copy(src_bib, os.path.join(base_dir, "references.bib"))
    src_figs = os.path.join(latex_writeup_dir, "figures")
    dst_figs = os.path.join(base_dir, "figures")
    if os.path.exists(src_figs) and os.path.abspath(src_figs) != os.path.abspath(dst_figs):
        shutil.copytree(src_figs, dst_figs, dirs_exist_ok=True)

    # Final staged export & verification pipeline via pipeline_fn
    async def _finalize_stage(stage_name: str):
        if stage_name == "markdown_export":
            md_content = latex_to_markdown(current_tex)
            with open(final_book_md, "w", encoding="utf-8") as f:
                f.write(md_content)
            return final_book_md
        if stage_name == "chapter_and_ledger_sync":
            chapters = _export_modular_chapters(current_tex, latex_writeup_dir)
            _update_continuity_ledger(continuity_ledger, current_tex, chapters)
            with open(
                os.path.join(base_dir, "book_continuity_ledger.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(continuity_ledger, f, indent=4, ensure_ascii=False)
            return chapters
        if stage_name == "build_manifest":
            manifest = {
                "render_mode": render_mode,
                "preview_backend": (
                    get_last_preview_backend() if render_mode == "preview" else None
                ),
                "is_publication_grade": is_publication_grade,
                "delivered_pdf": os.path.basename(delivered_pdf),
                "final_book_tex": os.path.basename(final_book_tex),
                "final_book_md": os.path.basename(final_book_md),
                "research_cutoff": research_cutoff,
                "strict_latex": strict_latex,
                "author_voice": author_voice,
                "visual_density": visual_density,
                "ready_figure_count": len(figures_info),
                "user_figure_count": len(user_figures_info),
                "slide_source": (
                    {
                        "source_file": slide_source_manifest.get("source_file"),
                        "source_type": slide_source_manifest.get("source_type"),
                        "source_sha256": slide_source_manifest.get("source_sha256"),
                        "slide_count": slide_source_manifest.get("slide_count"),
                        "included_slide_count": slide_source_manifest.get(
                            "included_slide_count"
                        ),
                        "hidden_slide_count": slide_source_manifest.get(
                            "hidden_slide_count"
                        ),
                        "manifest": "raw_materials/slide_source_manifest.json",
                    }
                    if slide_source_manifest
                    else None
                ),
                "codebase_source": (
                    {
                        "source_name": codebase_source_manifest.get("source_name"),
                        "source_type": codebase_source_manifest.get("source_type"),
                        "source_tree_sha256": codebase_source_manifest.get(
                            "source_tree_sha256"
                        ),
                        "discovered_file_count": codebase_source_manifest.get(
                            "discovered_file_count"
                        ),
                        "included_file_count": codebase_source_manifest.get(
                            "included_file_count"
                        ),
                        "redaction_count": codebase_source_manifest.get(
                            "redaction_count"
                        ),
                        "manifest": "raw_materials/codebase_source_manifest.json",
                    }
                    if codebase_source_manifest
                    else None
                ),
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=4, ensure_ascii=False)
            return manifest
        return None

    pipeline_outputs = await pipeline_fn(
        ["markdown_export", "chapter_and_ledger_sync", "build_manifest"],
        _finalize_stage,
    )
    final_chapters = (
        pipeline_outputs[1]
        if isinstance(pipeline_outputs, list) and len(pipeline_outputs) > 1
        else _export_modular_chapters(current_tex, latex_writeup_dir)
    )

    result_status = "success" if is_publication_grade else "preview"
    log_fn(
        f"✅ BookOrchestra complete ({result_status}, render_mode={render_mode})! "
        f"Final Score: {current_score}/10 | PDF: {delivered_pdf} | "
        f"Markdown: {final_book_md} | Chapters: {len(final_chapters)}"
    )

    return {
        "status": result_status,
        "render_mode": render_mode,
        "is_publication_grade": is_publication_grade,
        "final_score": current_score,
        "final_book_pdf": final_book_pdf if is_publication_grade else None,
        "preview_book_pdf": preview_book_pdf if not is_publication_grade else None,
        "delivered_pdf": delivered_pdf,
        "build_manifest": manifest_path,
        "final_book_tex": final_book_tex,
        "final_book_md": final_book_md,
        "chapters": final_chapters,
    }


def run_book_orchestra_sync(workflow_args: dict) -> dict:
    """
    Synchronous bridge that executes `run_book_orchestra_workflow(...)` using
    `utils.agent_runtime_utils` so CLI and Streamlit run the exact same core
    orchestration engine as Antigravity native `run_workflow`.
    """
    import asyncio
    from utils.agent_runtime_utils import (
        call_agent_with_text_prompt,
        generate_image_with_agent,
    )

    def _phase(name: str):
        print(f"\n=== Phase: {name} ===")

    def _log(*items):
        print(" ".join(str(x) for x in items))

    async def _agent(
        prompt: str,
        schema: dict | None = None,
        role: str = "Agent",
        type_name: str = "self",
        workspace: str = "inherit",
        model: str | None = None,
    ):
        if type_name == "image-generator":
            return generate_image_with_agent(
                model_name=model or "image-generator",
                prompt=prompt,
            )
        gen_cfg: dict = {}
        if schema and schema.get("type") == "object":
            gen_cfg["response_schema"] = schema
        if type_name == "research":
            gen_cfg["tools"] = [{"google_search": {}}]

        parse_fn = (
            None
            if (schema and schema.get("type") == "string")
            else (lambda x: x)
        )
        res = await asyncio.to_thread(
            call_agent_with_text_prompt,
            prompt=prompt,
            model_name=model or "inherit",
            result_parsing_func=parse_fn,
            generation_configs=gen_cfg,
            check_parsed_response_not_none=False,
        )
        return res.get("raw_response", "")

    async def _parallel(items, fn):
        async def _eval(item):
            out = fn(item)
            if asyncio.iscoroutine(out):
                return await out
            return out

        return await asyncio.gather(*(_eval(it) for it in items))

    async def _pipeline(items, fn):
        outputs = []
        for item in items:
            out = fn(item)
            if asyncio.iscoroutine(out):
                out = await out
            outputs.append(out)
        return outputs

    return asyncio.run(
        run_book_orchestra_workflow(
            workflow_args=workflow_args,
            phase_fn=_phase,
            log_fn=_log,
            agent_fn=_agent,
            parallel_fn=_parallel,
            pipeline_fn=_pipeline,
        )
    )
