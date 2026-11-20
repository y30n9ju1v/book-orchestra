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

import argparse
import sys
import os
from datetime import datetime


def build_arg_parser(default_description: str = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=default_description
        or "CLI for generating a technical IT/engineering book using BookOrchestra."
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--raw_materials_dir",
        help="Directory containing raw book materials (book_proposal.md / idea_sparse.md and technical_notes.md / experimental_log.md).",
    )
    source_group.add_argument(
        "--slides_file",
        help="Lecture slides to turn into a friendly explanatory book (.pptx or slide .pdf).",
    )
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Directory to output the generated book. If omitted, an automatic timestamped folder is created.",
    )
    parser.add_argument(
        "--latex_template_dir",
        default="templates/oreilly_style_book",
        help="Directory containing the LaTeX book template (e.g., templates/oreilly_style_book, templates/manning_in_action_book, templates/systems_handbook).",
    )
    parser.add_argument(
        "--proposal_filename",
        "--idea_filename",
        dest="proposal_filename",
        default="book_proposal.md",
        help="Filename of the book proposal/vision document within raw_materials. Falls back to idea_sparse.md / idea.md automatically.",
    )
    parser.add_argument(
        "--technical_notes_filename",
        "--experimental_log_filename",
        dest="technical_notes_filename",
        default="technical_notes.md",
        help="Filename of the technical notes / benchmarks / code specs document within raw_materials. Falls back to experimental_log.md automatically.",
    )
    parser.add_argument(
        "--target_audience",
        default="Senior Software & Systems Engineers",
        help="Target reader persona for calibration (e.g., 'Senior Software & Systems Engineers', 'Staff Platform Architects').",
    )
    parser.add_argument(
        "--book_style",
        default="Practical Architecture & Production Engineering",
        help="Editorial and pedagogical style (e.g., 'Practical Architecture & Production Engineering', 'Project-Based Hands-On', 'Deep Systems Reference').",
    )
    parser.add_argument(
        "--author_voice",
        default=(
            "Derive and preserve the proposal's own voice; use a warm, exploratory, "
            "reader-facing engineering narrative with concrete examples and varied pacing."
        ),
        help="Binding prose voice contract. The proposal remains the primary source of truth.",
    )
    parser.add_argument(
        "--writer_model_name",
        default="inherit",
        help="Antigravity model tier for blueprint, research, and chapter writing agents ('inherit', 'pro', 'flash', 'flash_lite').",
    )
    parser.add_argument(
        "--reflection_model_name",
        default="inherit",
        help="Antigravity model tier for the 3-Persona Editorial Review Board and refinement agents.",
    )
    parser.add_argument(
        "--research_cutoff",
        default=None,
        help="Year-Month cutoff date for technical reference research (e.g., 2026-11). Defaults to current Year-Month.",
    )
    parser.add_argument(
        "--use_plotting",
        type=lambda x: (str(x).lower() in ["true", "1", "yes"]),
        default=True,
        help="Enable Technical Illustration & Benchmark Plotting agent workflow (default: True).",
    )
    parser.add_argument(
        "--visual_density",
        choices=("minimal", "balanced", "high"),
        default="high",
        help="Teaching-visual coverage: minimal, balanced (>=1 per substantive chapter), or high (>=2; default).",
    )
    parser.add_argument(
        "--plotting_model_name",
        default="inherit",
        help="Antigravity model tier for the technical diagram & plotting agent.",
    )
    parser.add_argument(
        "--image_model_name",
        default="image-generator",
        help="Antigravity subagent or model for architecture diagrams.",
    )
    parser.add_argument(
        "--plotting_max_critic_rounds",
        type=int,
        default=3,
        help="Maximum rounds of visual QA critic self-correction.",
    )
    parser.add_argument(
        "--max_reflections",
        type=int,
        default=2,
        help="Maximum number of 3-Persona Editorial Review Board refinement rounds.",
    )
    parser.add_argument(
        "--strict_latex",
        action="store_true",
        help="Require a real LaTeX compiler (pdflatex/tectonic); fail instead of writing a preview PDF.",
    )
    parser.add_argument(
        "--emit_workflow_only",
        action="store_true",
        help="Write the Antigravity run_workflow payload JSON to output_dir and exit.",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.research_cutoff or str(args.research_cutoff).strip().lower() in (
        "current",
        "auto",
        "latest",
        "none",
        "null",
    ):
        args.research_cutoff = datetime.now().strftime("%Y-%m")

    if args.raw_materials_dir and not os.path.exists(args.raw_materials_dir):
        print(
            f"Error: Raw materials directory '{args.raw_materials_dir}' does not exist."
        )
        sys.exit(1)
    if args.slides_file and not os.path.isfile(args.slides_file):
        print(f"Error: Slides file '{args.slides_file}' does not exist.")
        sys.exit(1)

    if args.output_dir:
        base_dir = args.output_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = f"./book_output_{timestamp}"

    from utils.path_security import (
        prepare_safe_output_workspace,
        validate_slide_output_workspace,
    )

    raw_materials_dir = args.raw_materials_dir
    if args.slides_file:
        from utils.slide_ingestion import prepare_slide_materials

        try:
            validate_slide_output_workspace(
                args.slides_file, args.latex_template_dir, base_dir
            )
            slide_result = prepare_slide_materials(
                args.slides_file,
                os.path.join(base_dir, "raw_materials"),
                target_audience=args.target_audience,
                book_style=args.book_style,
                author_voice=args.author_voice,
            )
            raw_materials_dir = slide_result["raw_materials_dir"]
            print(
                "📚 Prepared slide-grounded source materials "
                f"({slide_result['included_slide_count']} included slides/pages, "
                f"{slide_result['hidden_slide_count']} hidden, "
                f"{slide_result['extracted_figure_count']} reusable visuals)."
            )
        except (OSError, RuntimeError, ValueError) as err:
            print(f"❌ Could not ingest lecture slides: {err}", file=sys.stderr)
            sys.exit(1)

    try:
        (
            _,
            _,
            resolved_base,
            _,
            _,
        ) = prepare_safe_output_workspace(
            raw_materials_dir=raw_materials_dir,
            latex_template_dir=args.latex_template_dir,
            output_dir=base_dir,
        )
        base_dir = str(resolved_base)
    except ValueError as err:
        print(f"❌ Unsafe workspace path configuration: {err}", file=sys.stderr)
        sys.exit(1)

    import json
    from utils.agent_runtime_utils import (
        enable_agy_cli_runner,
        get_antigravity_agent_runner,
    )

    repo_root = os.path.dirname(os.path.abspath(__file__))
    workflow_script = "workflows/book_orchestra_workflow.py"
    workflow_payload = {
        "ScriptPath": workflow_script,
        "Cwd": repo_root,
        "Description": "Run BookOrchestra multi-agent technical book publishing pipeline",
        "Args": {
            "raw_materials_dir": raw_materials_dir,
            "latex_template_dir": args.latex_template_dir,
            "output_dir": base_dir,
            "proposal_filename": args.proposal_filename,
            "technical_notes_filename": args.technical_notes_filename,
            "target_audience": args.target_audience,
            "book_style": args.book_style,
            "author_voice": args.author_voice,
            "use_plotting": args.use_plotting,
            "visual_density": args.visual_density,
            "max_reflections": args.max_reflections,
            "research_cutoff": args.research_cutoff,
            "strict_latex": args.strict_latex,
            "model": args.writer_model_name,
            "writer_model_name": args.writer_model_name,
            "reflection_model_name": args.reflection_model_name,
            "plotting_model_name": args.plotting_model_name,
            "image_model_name": args.image_model_name,
            "plotting_max_critic_rounds": args.plotting_max_critic_rounds,
        },
    }
    payload_path = os.path.join(base_dir, "agy_run_workflow_payload.json")
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(workflow_payload, f, indent=2, ensure_ascii=False)

    if args.emit_workflow_only:
        print(
            f"✅ Antigravity run_workflow payload written to: {payload_path}\n"
            f"Run inside Antigravity without external API keys using `run_workflow`:\n"
            f"{json.dumps(workflow_payload, indent=2, ensure_ascii=False)}"
        )
        return 0

    if get_antigravity_agent_runner() is None:
        if enable_agy_cli_runner(workspace_dir=repo_root):
            print("🔌 Connected to local Antigravity CLI (`agy --print`) runner.")
        else:
            print(
                f"❌ Error: No in-process Antigravity runner or `agy` CLI binary found.\n"
                f"A `run_workflow` payload has been written to: {payload_path}\n"
                f"To execute natively inside Antigravity chat, invoke `run_workflow` with `workflows/book_orchestra_workflow.py`, "
                f"or pass `--emit_workflow_only` if you only want to generate the payload.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not args.use_plotting:
        has_figures = False
        figures_dir = os.path.join(raw_materials_dir, "figures")
        if os.path.exists(figures_dir):
            for f in os.listdir(figures_dir):
                if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
                    has_figures = True
                    break

        if not has_figures:
            print("\n" + "=" * 80)
            print(
                f"ℹ️ INFO: No pre-supplied figures detected in {figures_dir}."
            )
            print(
                "Running without --use_plotting; chapters will rely on tables, code listings, and callout boxes unless you enable --use_plotting true."
            )
            print("=" * 80 + "\n")

    from methods.agy_workflow_runner import run_book_orchestra_sync

    print(
        f"🚀 Starting Unified BookOrchestra Pipeline (use_plotting={args.use_plotting}, visual_density={args.visual_density}, strict_latex={args.strict_latex}) in {base_dir}"
    )
    try:
        result = run_book_orchestra_sync(workflow_payload["Args"])
    except Exception as exc:
        print(f"❌ Book generation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    delivered_pdf = result.get("delivered_pdf") if isinstance(result, dict) else None
    if not delivered_pdf or not os.path.exists(delivered_pdf):
        print(
            f"❌ Book generation failed (no PDF delivered). Output dir: {base_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.strict_latex and not result.get("is_publication_grade", False):
        print(
            "❌ Strict LaTeX publish mode failed: only preview PDF was produced.",
            file=sys.stderr,
        )
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
