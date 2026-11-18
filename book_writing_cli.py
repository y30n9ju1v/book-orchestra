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
import shutil
from datetime import datetime


def build_arg_parser(default_description: str = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=default_description
        or "CLI for generating a technical IT/engineering book using BookOrchestra."
    )

    parser.add_argument(
        "--raw_materials_dir",
        required=True,
        help="Directory containing raw book materials (book_proposal.md / idea_sparse.md and technical_notes.md / experimental_log.md).",
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
        default=False,
        help="Enable Technical Illustration & Benchmark Plotting agent workflow.",
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
        default=3,
        help="Maximum number of 3-Persona Editorial Review Board refinement rounds.",
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

    if args.research_cutoff is None:
        args.research_cutoff = datetime.now().strftime("%Y-%m")

    if not os.path.exists(args.raw_materials_dir):
        print(
            f"Error: Raw materials directory '{args.raw_materials_dir}' does not exist."
        )
        sys.exit(1)

    if args.output_dir:
        base_dir = args.output_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = f"./book_output_{timestamp}"

    os.makedirs(base_dir, exist_ok=True)

    target_raw_materials = os.path.join(base_dir, "raw_materials")
    if os.path.abspath(target_raw_materials) != os.path.abspath(args.raw_materials_dir):
        if os.path.exists(target_raw_materials):
            shutil.rmtree(target_raw_materials)
        shutil.copytree(args.raw_materials_dir, target_raw_materials)

    import json
    from utils.gemini_utils import (
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
            "raw_materials_dir": args.raw_materials_dir,
            "latex_template_dir": args.latex_template_dir,
            "output_dir": base_dir,
            "proposal_filename": args.proposal_filename,
            "technical_notes_filename": args.technical_notes_filename,
            "target_audience": args.target_audience,
            "book_style": args.book_style,
            "use_plotting": args.use_plotting,
            "max_reflections": args.max_reflections,
            "research_cutoff": args.research_cutoff,
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
        figures_dir = os.path.join(args.raw_materials_dir, "figures")
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

    if args.use_plotting:
        from methods.book_writer_with_plotting import write_single_book

        print(f"🚀 Starting Technical Book Generation (WITH DIAGRAMS/PLOTTING) in {base_dir}")
        success = write_single_book(
            base_dir=base_dir,
            latex_template_dir=args.latex_template_dir,
            idea_filename=args.proposal_filename,
            experimental_log_filename=args.technical_notes_filename,
            writer_model_name=args.writer_model_name,
            reflection_model_name=args.reflection_model_name,
            plotting_model_name=args.plotting_model_name,
            image_model_name=args.image_model_name,
            research_cutoff=args.research_cutoff,
            plotting_max_critic_rounds=args.plotting_max_critic_rounds,
            target_audience=args.target_audience,
            book_style=args.book_style,
            max_reflections=args.max_reflections,
        )
    else:
        from methods.book_writer import write_single_book

        print(f"🚀 Starting Technical Book Generation (WITHOUT PLOTTING) in {base_dir}")
        success = write_single_book(
            base_dir=base_dir,
            latex_template_dir=args.latex_template_dir,
            idea_filename=args.proposal_filename,
            experimental_log_filename=args.technical_notes_filename,
            writer_model_name=args.writer_model_name,
            reflection_model_name=args.reflection_model_name,
            research_cutoff=args.research_cutoff,
            target_audience=args.target_audience,
            book_style=args.book_style,
            max_reflections=args.max_reflections,
        )

    final_pdf_path = os.path.join(base_dir, "final_book.pdf")
    if not success or not os.path.exists(final_pdf_path):
        print(
            f"❌ Book generation failed (final_book.pdf missing or pipeline returned False). Output dir: {base_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
