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
with ZERO external Gemini/OpenAI API keys required.

Usage inside Antigravity:
  run_workflow(
      ScriptPath="/Users/yeongjun/Develops/book-orchestra/workflows/book_orchestra_workflow.py",
      Args={
          "raw_materials_dir": "examples/distributed_llm_systems_book/raw_materials",
          "latex_template_dir": "templates/oreilly_style_book",
          "output_dir": "./book_output_agy",
          "use_plotting": True,
      },
      Description="Author and review an IT/Engineering book with BookOrchestra"
  )
"""

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
from utils.gemini_utils import (
    parse_gemini_json_results,
    parse_gemini_latex_results,
)
from utils.paper_banana_utils import (
    DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT,
    PLOT_PLANNER_AGENT_SYSTEM_PROMPT,
    PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT,
    execute_plot_code_worker,
    _minimal_png_base64,
)
from utils.pdf_utils import compile_latex, latex_to_markdown, load_book


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


def _resolve_file(folder: str, primary: str, fallbacks: list[str]) -> str:
    for name in [primary] + [fb for fb in fallbacks if fb != primary]:
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(folder, primary)


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
    intro_strat = plan.get("introduction_strategy", {})
    for direction in intro_strat.get("search_directions", []):
        tasks.append(
            {
                "section": "Introduction",
                "focus": direction,
                "context": f"Hook: {intro_strat.get('hook_hypothesis', '')}. Gap: {intro_strat.get('problem_gap_hypothesis', '')}",
                "search_type": "exploration",
            }
        )
    rw_strat = plan.get("related_work_strategy", {})
    for sub in rw_strat.get("subsections", []):
        queries = sub.get("limitation_search_queries") or [
            f"{sub.get('subsection_title', '')} {sub.get('methodology_cluster', '')}"
        ]
        for q in queries:
            tasks.append(
                {
                    "section": f"Related Work: {sub.get('subsection_title', 'General')}",
                    "focus": q,
                    "context": f"Mission: {sub.get('sota_investigation_mission', '')}. Hypothesis: {sub.get('limitation_hypothesis', '')}",
                    "search_type": "exploration",
                }
            )
    blueprint = outline.get("section_plan") or outline.get("chapter_plan", [])
    for section in blueprint:
        sec_title = section.get("section_title", "Chapter")
        for sub in section.get("subsections", []):
            sub_title = sub.get("subsection_title", "Section")
            bullets = " ".join(sub.get("content_bullets", []))
            for hint in sub.get("citation_hints", []):
                tasks.append(
                    {
                        "section": f"{sec_title} - {sub_title}",
                        "focus": hint,
                        "context": f"Canonical reference for: {bullets}",
                        "search_type": "targeted",
                    }
                )
    return tasks


def _export_modular_chapters(latex_text: str, output_dir: str) -> list[str]:
    chapters_dir = os.path.join(output_dir, "chapters")
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
    use_plotting = bool(workflow_args.get("use_plotting", True))
    max_reflections = int(workflow_args.get("max_reflections", 2))
    research_cutoff = workflow_args.get(
        "research_cutoff", datetime.datetime.now().strftime("%Y-%m")
    )
    model_tier = workflow_args.get("model", "inherit")

    raw_materials_dir = (
        raw_materials_rel
        if os.path.isabs(raw_materials_rel)
        else os.path.join(_WORK_ROOT, raw_materials_rel)
    )
    latex_template_dir = (
        template_rel
        if os.path.isabs(template_rel)
        else os.path.join(_WORK_ROOT, template_rel)
    )
    base_dir = (
        output_rel
        if os.path.isabs(output_rel)
        else os.path.join(_WORK_ROOT, output_rel)
    )

    os.makedirs(base_dir, exist_ok=True)
    target_raw_dir = os.path.join(base_dir, "raw_materials")
    if os.path.abspath(target_raw_dir) != os.path.abspath(raw_materials_dir):
        if os.path.exists(target_raw_dir):
            shutil.rmtree(target_raw_dir)
        shutil.copytree(raw_materials_dir, target_raw_dir)

    latex_writeup_dir = os.path.join(base_dir, "latex_writeup")
    if os.path.exists(latex_writeup_dir):
        shutil.rmtree(latex_writeup_dir)
    shutil.copytree(latex_template_dir, latex_writeup_dir, dirs_exist_ok=True)

    figures_dir = os.path.join(latex_writeup_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    user_figures_dir = os.path.join(target_raw_dir, "figures")
    if os.path.exists(user_figures_dir):
        shutil.copytree(user_figures_dir, figures_dir, dirs_exist_ok=True)

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
    template_text = _read_text(template_path)
    guidelines_text = _read_text(guidelines_path)

    # =========================================================================
    # PHASE 1: Book Blueprint & Table of Contents
    # =========================================================================
    phase_fn("1. Book Blueprint & Architecture Plan")
    log_fn(f"Designing Book Blueprint for target audience: {target_audience}")

    blueprint_prompt = (
        outline_agent_system_prompt.format(cutoff_date=research_cutoff)
        + f"\n\nPublishing Profile:\n- Target Audience: {target_audience}\n- Book Style: {book_style}\n\n"
        + f"--- 'book_proposal.md' ---\n{proposal_text}\n\n"
        + f"--- 'technical_notes.md' ---\n{notes_text}\n\n"
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
        raw_outline = parse_gemini_json_results(raw_outline) or json.loads(raw_outline)
    outline = OutlineAgent._normalize_book_blueprint(dict(raw_outline))

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
            f"official engineering specifications, or classic books) published before {research_cutoff}.\n"
            f"Section: {task['section']}\n"
            f"Search Focus: {task['focus']}\n"
            f"Context: {task['context']}\n"
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
    for task, res in zip(search_tasks, discovery_results):
        if isinstance(res, str):
            res = parse_gemini_json_results(res) or {}
        for cand in (res or {}).get("candidates", []):
            title = cand.get("title", "").strip()
            if not title:
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
            }

    papers_list = list(registry.values())
    # Deduplicate citation keys & build BibTeX + citation_map
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
        author_str = " and ".join(p["authors"])
        bib_entries.append(
            f"@inproceedings{{{ukey},\n  title={{{p['title']}}},\n  author={{{author_str}}},\n  booktitle={{{p['venue']}}},\n  year={{{p['year']}}}\n}}"
        )
        citation_map[ukey] = {
            "citation_key": ukey,
            "title": p["title"],
            "authors": p["authors"],
            "venue": p["venue"],
            "year": p["year"],
            "abstract": p["abstract"],
        }
        sec = p["found_in_section"]
        section_to_keys.setdefault(sec, []).append(ukey)

    # Inject citation candidates into outline
    plan = outline.get("intro_related_work_plan", {})
    plan.setdefault("introduction_strategy", {})["citation_candidates"] = list(
        set(section_to_keys.get("Introduction", []))
    )
    for sub in plan.get("related_work_strategy", {}).get("subsections", []):
        k = f"Related Work: {sub.get('subsection_title', 'General')}"
        sub["citation_candidates"] = section_to_keys.get(k, [])
    for sec in outline.get("section_plan", []):
        sec_title = sec.get("section_title", "Chapter")
        for sub in sec.get("subsections", []):
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
    updated_template_tex = parse_gemini_latex_results(str(lit_latex_raw))
    with open(
        os.path.join(lit_output_dir, "updated_template.tex"), "w", encoding="utf-8"
    ) as f:
        f.write(updated_template_tex)
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(updated_template_tex)

    # Generate Architecture Diagrams & Benchmark Plots in Parallel if enabled
    figures_info = []
    plotting_plan = outline.get("plotting_plan", []) if use_plotting else []
    if plotting_plan:
        log_fn(
            f"Generating {len(plotting_plan)} architecture diagrams and benchmark plots in parallel..."
        )

        async def _generate_single_visual(fig_plan: dict) -> dict:
            fig_id = (
                fig_plan.get("figure_id", "fig_architecture")
                .replace(" ", "_")
                .lower()
            )
            title = fig_plan.get("title", fig_id)
            plot_type = fig_plan.get("plot_type", "diagram").lower()
            objective = fig_plan.get("objective", "")
            img_rel = f"figures/{fig_id}.jpg"
            img_abs = os.path.join(latex_writeup_dir, img_rel)
            os.makedirs(os.path.dirname(img_abs), exist_ok=True)

            if "plot" in plot_type or "chart" in plot_type:
                code_prompt = (
                    f"{PLOT_PLANNER_AGENT_SYSTEM_PROMPT}\n\n"
                    f"{PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT}\n\n"
                    f"Title: {title}\nObjective: {objective}\n"
                    f"Raw Technical Notes & Benchmarks:\n{notes_text}\n\n"
                    f"Write clean Python matplotlib code wrapped in ```python ... ```."
                )
                code_resp = await agent_fn(
                    prompt=code_prompt,
                    schema={"type": "string"},
                    role=f"Benchmark Plotter: {fig_id}",
                    model=model_tier,
                )
                b64_img = execute_plot_code_worker(str(code_resp)) or _minimal_png_base64()
                with open(img_abs, "wb") as fh:
                    fh.write(base64.b64decode(b64_img))
            else:
                diag_prompt = (
                    f"{DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT}\n\n"
                    f"Generate a crisp white-background technical architecture diagram for:\n"
                    f"Title: {title}\nObjective: {objective}\n"
                    f"Architecture Context:\n{proposal_text}\n\n"
                    f"Save or return the image path."
                )
                try:
                    diag_resp = await agent_fn(
                        prompt=diag_prompt,
                        schema={"type": "string"},
                        role=f"Architecture Illustrator: {fig_id}",
                        type_name="image-generator",
                    )
                    # If image-generator returned a valid local file path, copy it; otherwise write fallback PNG
                    cand_path = str(diag_resp).strip()
                    if os.path.exists(cand_path) and os.path.isfile(cand_path):
                        shutil.copy(cand_path, img_abs)
                    else:
                        with open(img_abs, "wb") as fh:
                            fh.write(base64.b64decode(_minimal_png_base64()))
                except Exception:
                    with open(img_abs, "wb") as fh:
                        fh.write(base64.b64decode(_minimal_png_base64()))

            caption = f"{title}: {objective}".strip(": ")
            return {
                "figure_id": fig_id,
                "title": title,
                "name": f"{fig_id}.jpg",
                "image_path": img_rel,
                "caption": caption,
            }

        plot_results = await parallel_fn(plotting_plan, _generate_single_visual)
        with open(
            os.path.join(base_dir, "plotting_results.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(plot_results, f, indent=4, ensure_ascii=False)
        figures_info = [
            {"name": r["name"], "caption": r["caption"]} for r in plot_results
        ]

    with open(os.path.join(figures_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(figures_info, f, indent=4, ensure_ascii=False)

    # =========================================================================
    # PHASE 3: Multi-Chapter Authoring with Continuity Ledger
    # =========================================================================
    phase_fn("3. Multi-Chapter Authoring & Continuity Ledger")
    book_meta = outline.get("book_metadata", {})
    continuity_ledger = {
        "book_title": book_meta.get("title", ""),
        "target_audience": target_audience,
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
        f"--- INPUT: outline.json ---\n{json.dumps(outline, indent=2, ensure_ascii=False)}\n\n"
        f"--- INPUT: citation_map.json ---\n{citation_context}\n\n"
        f"--- INPUT: book_proposal.md ---\n{proposal_text}\n\n"
        f"--- INPUT: technical_notes.md ---\n{notes_text}\n\n"
        f"--- INPUT: figures_list ---\n{figures_context}\n\n"
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
    current_tex = parse_gemini_latex_results(str(draft_latex_raw))
    raw_draft_tex_path = os.path.join(latex_writeup_dir, "raw_draft_paper.tex")
    with open(raw_draft_tex_path, "w", encoding="utf-8") as f:
        f.write(current_tex)

    exported_chapters = _export_modular_chapters(current_tex, latex_writeup_dir)
    continuity_ledger["defined_labels"] = sorted(
        set(re.findall(r"\\label\{([^}]+)\}", current_tex))
    )
    continuity_ledger["authored_chapters"] = re.findall(
        r"\\chapter\*?\{([^}]+)\}", current_tex
    )
    continuity_ledger["exported_chapter_files"] = [
        os.path.basename(p) for p in exported_chapters
    ]
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
    refinement_workdir = os.path.join(base_dir, "content_refinement_workdir")
    if os.path.exists(refinement_workdir):
        shutil.rmtree(refinement_workdir)
    shutil.copytree(latex_writeup_dir, refinement_workdir, dirs_exist_ok=True)
    os.makedirs(os.path.join(refinement_workdir, "peer_reviews"), exist_ok=True)

    initial_tex_path = os.path.join(refinement_workdir, "initial_draft.tex")
    initial_pdf_path = os.path.join(refinement_workdir, "initial_draft.pdf")
    with open(initial_tex_path, "w", encoding="utf-8") as f:
        f.write(current_tex)
    compile_latex(refinement_workdir, initial_pdf_path, "initial_draft")
    final_valid_pdf_path = initial_pdf_path

    async def _run_editorial_review_board(pdf_path: str, label: str) -> tuple[dict, float]:
        manuscript_text = load_book(pdf_path, min_size=10)

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
                model=model_tier,
            )

        raw_reviews = await parallel_fn(REVIEW_BOARD_PERSONAS, _spawn_persona_reviewer)
        parsed_reviews = []
        for r in raw_reviews:
            if isinstance(r, str):
                r = parse_gemini_json_results(r) or {}
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
            model=model_tier,
        )
        if isinstance(meta_raw, str):
            meta_raw = parse_gemini_json_results(meta_raw) or parsed_reviews[0]
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
        initial_pdf_path, "v0"
    )
    worklogs = {}

    for round_idx in range(max_reflections):
        v_label = f"v{round_idx + 1}"
        rev_prompt = (
            f"{content_refinement_agent_system_prompt}\n\n"
            f"EDITORIAL REVISION ITERATION {round_idx + 1}.\n"
            f"Current Editorial Review Board Score: {current_score}/10\n"
            f"Review Feedback:\n{json.dumps(current_review, indent=2, ensure_ascii=False)}\n\n"
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
            model=model_tier,
        )
        if isinstance(rev_res, str):
            rev_res = parse_gemini_json_results(rev_res) or {}
        new_tex = parse_gemini_latex_results(str(rev_res.get("latex", "")))
        if not new_tex:
            break

        cur_name = f"refined_paper_{v_label}"
        cur_tex_path = os.path.join(refinement_workdir, f"{cur_name}.tex")
        cur_pdf_path = os.path.join(refinement_workdir, f"{cur_name}.pdf")
        with open(cur_tex_path, "w", encoding="utf-8") as f:
            f.write(new_tex)
        compile_latex(refinement_workdir, cur_pdf_path, cur_name)
        if not os.path.exists(cur_pdf_path):
            continue

        new_review, new_score = await _run_editorial_review_board(
            cur_pdf_path, v_label
        )
        if new_score >= current_score:
            worklogs[v_label] = {
                "round": round_idx + 1,
                "agent_plan": rev_res.get("worklog", {}),
                "score_before": current_score,
                "score_after": new_score,
                "outcome": (
                    "ACCEPTED_SCORE_INCREASE"
                    if new_score > current_score
                    else "ACCEPTED_NEUTRAL_IMPROVEMENT"
                ),
            }
            current_tex = new_tex
            current_score = new_score
            current_review = new_review
            final_valid_pdf_path = cur_pdf_path
        else:
            worklogs[v_label] = {
                "round": round_idx + 1,
                "agent_plan": rev_res.get("worklog", {}),
                "score_before": current_score,
                "score_after": new_score,
                "outcome": "REJECTED_SCORE_DECREASE",
            }
            break

    with open(
        os.path.join(refinement_workdir, "content_refinement_worklog.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(worklogs, f, indent=4, ensure_ascii=False)

    # Production Typesetting QA pass
    fmt_prompt = (
        f"{format_agent_system_prompt}\n\n"
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
        model=model_tier,
    )
    polished_tex = parse_gemini_latex_results(str(fmt_raw)) or current_tex
    if r"\documentclass" in polished_tex and r"\end{document}" in polished_tex:
        current_tex = polished_tex

    # Save final artifacts
    final_refined_tex = os.path.join(refinement_workdir, "final_refined_book.tex")
    final_refined_pdf = os.path.join(refinement_workdir, "final_refined_book.pdf")
    with open(final_refined_tex, "w", encoding="utf-8") as f:
        f.write(current_tex)
    compile_latex(refinement_workdir, final_refined_pdf, "final_refined_book")
    if not os.path.exists(final_refined_pdf) and os.path.exists(final_valid_pdf_path):
        shutil.copy(final_valid_pdf_path, final_refined_pdf)

    final_book_pdf = os.path.join(base_dir, "final_book.pdf")
    final_paper_pdf = os.path.join(base_dir, "final_paper.pdf")
    final_book_tex = os.path.join(base_dir, "final_book.tex")
    final_book_md = os.path.join(base_dir, "final_book.md")

    shutil.copy(final_refined_pdf, final_book_pdf)
    shutil.copy(final_refined_pdf, final_paper_pdf)
    with open(final_book_tex, "w", encoding="utf-8") as f:
        f.write(current_tex)
    shutil.copy(final_book_tex, os.path.join(latex_writeup_dir, "main.tex"))

    md_content = latex_to_markdown(current_tex)
    with open(final_book_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    final_chapters = _export_modular_chapters(current_tex, latex_writeup_dir)
    log_fn(
        f"✅ BookOrchestra complete! Final Score: {current_score}/10 | "
        f"PDF: {final_book_pdf} | Markdown: {final_book_md} | Chapters: {len(final_chapters)}"
    )

    return {
        "status": "success",
        "final_score": current_score,
        "final_book_pdf": final_book_pdf,
        "final_book_tex": final_book_tex,
        "final_book_md": final_book_md,
        "chapters": final_chapters,
    }
