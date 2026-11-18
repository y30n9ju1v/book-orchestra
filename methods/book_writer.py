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
import os.path as osp
import shutil
import traceback
import json
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    import tqdm
except ImportError:

    class _TqdmFallback:
        @staticmethod
        def tqdm(iterable, **kwargs):
            return iterable

    tqdm = _TqdmFallback()  # type: ignore

from methods.agents.outline_agent import OutlineAgent
from methods.agents.literature_review_agent import HybridLiteratureAgent
from methods.agents.section_writing_agent import SectionWritingAgent
from methods.agents.content_refinement_agent import ContentRefinementAgent
from utils.common_utils import create_log_folder
from utils.pdf_utils import latex_to_markdown


def _resolve_input_file(materials_dir: str, primary: str, fallbacks: list[str]) -> str:
    candidates = [primary] + [f for f in fallbacks if f != primary]
    for name in candidates:
        if not name:
            continue
        path = osp.join(materials_dir, name)
        if osp.exists(path):
            return path
    return osp.join(materials_dir, primary)


def _finalize_book_artifacts(
    base_dir: str,
    latex_writeup_dir: str,
    refinement_workdir: str,
    refined_pdf_path: str,
    section_writing_agent: SectionWritingAgent,
    strict_latex: bool = False,
):
    """Saves delivered PDF (`final_book.pdf` or `preview_book.pdf`), `build_manifest.json`, self-contained `final_book.tex`, `final_book.md`, modular chapters, and updated continuity ledger."""
    from utils.pdf_utils import get_last_compile_mode

    render_mode = get_last_compile_mode()
    is_publication_grade = render_mode in ("pdflatex", "tectonic")
    final_book_pdf = osp.join(base_dir, "final_book.pdf")
    preview_book_pdf = osp.join(base_dir, "preview_book.pdf")

    if is_publication_grade:
        shutil.copy(refined_pdf_path, final_book_pdf)
        delivered_pdf = final_book_pdf
    else:
        if os.path.exists(final_book_pdf):
            os.remove(final_book_pdf)
        shutil.copy(refined_pdf_path, preview_book_pdf)
        delivered_pdf = preview_book_pdf

    # Sync references.bib and figures/ to base_dir so final_book.tex is self-contained
    src_bib = osp.join(latex_writeup_dir, "references.bib")
    if osp.exists(src_bib):
        shutil.copy(src_bib, osp.join(base_dir, "references.bib"))
    src_figs = osp.join(latex_writeup_dir, "figures")
    dst_figs = osp.join(base_dir, "figures")
    if osp.exists(src_figs) and osp.abspath(src_figs) != osp.abspath(dst_figs):
        shutil.copytree(src_figs, dst_figs, dirs_exist_ok=True)

    refined_tex_candidates = [
        osp.join(refinement_workdir, "final_refined_book.tex"),
        osp.join(latex_writeup_dir, "raw_draft_book.tex"),
    ]
    refined_tex_path = next((p for p in refined_tex_candidates if osp.exists(p)), None)
    if refined_tex_path:
        with open(refined_tex_path, "r", encoding="utf-8") as f:
            final_tex = f.read()

        final_book_tex = osp.join(base_dir, "final_book.tex")
        with open(final_book_tex, "w", encoding="utf-8") as f:
            f.write(final_tex)

        shutil.copy(final_book_tex, osp.join(latex_writeup_dir, "main.tex"))

        final_book_md = osp.join(base_dir, "final_book.md")
        md_content = latex_to_markdown(final_tex)
        with open(final_book_md, "w", encoding="utf-8") as f:
            f.write(md_content)

        exported_paths = section_writing_agent._export_modular_chapters(
            final_tex, latex_writeup_dir
        )
        section_writing_agent._update_continuity_ledger_after_write(
            final_tex, exported_paths
        )
        ledger_path = osp.join(base_dir, "book_continuity_ledger.json")
        with open(ledger_path, "w", encoding="utf-8") as f:
            json.dump(
                section_writing_agent.continuity_ledger,
                f,
                indent=4,
                ensure_ascii=False,
            )

    manifest_path = osp.join(base_dir, "build_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "render_mode": render_mode,
                "is_publication_grade": is_publication_grade,
                "delivered_pdf": osp.basename(delivered_pdf),
                "strict_latex": strict_latex,
            },
            f,
            indent=4,
            ensure_ascii=False,
        )


def write_single_book(
    base_dir,
    latex_template_dir,
    idea_filename="book_proposal.md",
    experimental_log_filename="technical_notes.md",
    writer_model_name: str = "inherit",
    reflection_model_name: str = "inherit",
    research_cutoff: str = None,
    target_audience: str = "Senior Software & Systems Engineers",
    book_style: str = "Practical Architecture & Production Engineering",
    max_reflections: int = 2,
):
    from datetime import datetime

    if not research_cutoff or str(research_cutoff).strip().lower() in ("current", "auto", "latest", "none", "null"):
        research_cutoff = datetime.now().strftime("%Y-%m")
    latex_writeup_dir = osp.join(base_dir, "latex_writeup")
    pdf_file = osp.join(base_dir, "final_book.pdf")

    if osp.exists(latex_writeup_dir):
        shutil.rmtree(latex_writeup_dir)
    if osp.exists(pdf_file):
        os.remove(pdf_file)

    materials_dir = osp.join(base_dir, "raw_materials")

    old_figure_dir = osp.join(materials_dir, "figures")
    new_figure_dir = osp.join(latex_writeup_dir, "figures")
    if osp.exists(old_figure_dir):
        shutil.copytree(old_figure_dir, new_figure_dir, dirs_exist_ok=True)
    figures_dir = new_figure_dir

    max_n_tries = 3
    cur_try = 0
    succeed = False

    while (not succeed) and (cur_try < max_n_tries):
        try:
            assert osp.exists(
                materials_dir
            ), f"Please make sure '{materials_dir}' exists!"

            shutil.copytree(latex_template_dir, latex_writeup_dir, dirs_exist_ok=True)

            guidelines_path = osp.join(latex_writeup_dir, "guidelines.md")
            latex_template_path = osp.join(latex_writeup_dir, "template.tex")

            idea_file_path = _resolve_input_file(
                materials_dir,
                idea_filename,
                ["book_proposal.md", "idea_sparse.md", "idea_dense.md", "idea.md"],
            )
            experimental_log_file_path = _resolve_input_file(
                materials_dir,
                experimental_log_filename,
                ["technical_notes.md", "experimental_log.md"],
            )

            assert osp.exists(
                guidelines_path
            ), f"No guidelines file detected at '{guidelines_path}'!"
            assert osp.exists(
                latex_template_path
            ), f"No template file detected at '{latex_template_path}'!"
            assert osp.exists(
                idea_file_path
            ), f"No book proposal / idea file detected at '{idea_file_path}'!"
            assert osp.exists(
                experimental_log_file_path
            ), f"No technical notes / experimental log file detected at '{experimental_log_file_path}'!"

            # Step 1: Book Blueprint Agent
            outline_agent = OutlineAgent(
                model_name=writer_model_name,
                cutoff_date=research_cutoff,
                target_audience=target_audience,
                book_style=book_style,
            )
            outline_file_path = osp.join(base_dir, "outline.json")
            outline_agent.run(
                idea_file=idea_file_path,
                experimental_log_file=experimental_log_file_path,
                latex_template_file=latex_template_path,
                conference_guidelines_file=guidelines_path,
                output_filepath=outline_file_path,
                target_audience=target_audience,
                book_style=book_style,
            )

            # Step 2: Technical Ecosystem & Reference Research Agent
            literature_agent_output_dir = osp.join(base_dir, "literature_agent_output")
            literature_agent = HybridLiteratureAgent(
                idea_path=idea_file_path,
                experimental_log_path=experimental_log_file_path,
                latex_template_path=latex_template_path,
                conference_guidelines_path=guidelines_path,
                output_dir=literature_agent_output_dir,
                model_name=writer_model_name,
                max_workers=3,
            )
            literature_agent.run(
                outline_path=outline_file_path,
                cutoff_date=research_cutoff,
            )
            shutil.copy(
                osp.join(literature_agent_output_dir, "outline_v1.json"),
                outline_file_path,
            )
            shutil.copy(
                osp.join(literature_agent_output_dir, "updated_template.tex"),
                osp.join(latex_writeup_dir, "template.tex"),
            )
            shutil.copy(
                osp.join(literature_agent_output_dir, "references.bib"),
                osp.join(latex_writeup_dir, "references.bib"),
            )

            # Step 3: Chapter & Section Writing Agent
            citation_map_path = osp.join(
                literature_agent_output_dir, "citation_map.json"
            )
            section_writing_agent = SectionWritingAgent(
                outline_path=outline_file_path,
                template_path=osp.join(latex_writeup_dir, "template.tex"),
                idea_path=idea_file_path,
                experimental_log_path=experimental_log_file_path,
                citation_map_path=citation_map_path,
                figures_info_path=osp.join(figures_dir, "info.json"),
                guidelines_path=guidelines_path,
                model_name=writer_model_name,
            )
            raw_draft_tex_path = osp.join(latex_writeup_dir, "raw_draft_book.tex")
            section_writing_agent.run(output_path=raw_draft_tex_path)

            # Step 4: Editorial Review Board & Refinement
            content_refinement_workdir = os.path.join(
                base_dir, "content_refinement_workdir"
            )
            if os.path.exists(content_refinement_workdir):
                shutil.rmtree(content_refinement_workdir)
            os.makedirs(content_refinement_workdir, exist_ok=True)

            shutil.copytree(
                latex_writeup_dir,
                content_refinement_workdir,
                dirs_exist_ok=True,
            )

            content_refinement_agent = ContentRefinementAgent(
                experimental_log_path=experimental_log_file_path,
                citation_map_path=citation_map_path,
                guidelines_path=guidelines_path,
                model_name=reflection_model_name,
                max_reflections=max_reflections,
                work_dir=content_refinement_workdir,
            )
            refined_pdf_path = content_refinement_agent.run(
                texfile_path=raw_draft_tex_path
            )
            assert osp.exists(
                refined_pdf_path
            ), f"Editorial Refinement Agent failed to produce final PDF at {refined_pdf_path}!"

            _finalize_book_artifacts(
                base_dir=base_dir,
                latex_writeup_dir=latex_writeup_dir,
                refinement_workdir=content_refinement_workdir,
                refined_pdf_path=refined_pdf_path,
                section_writing_agent=section_writing_agent,
            )
            print(" >> Final Book PDF saved to:\n", osp.join(base_dir, "final_book.pdf"))

            succeed = True
            return True

        except Exception:
            print(f"[Try {cur_try+1}/{max_n_tries}] EXCEPTION in write_single_book:")
            print(traceback.format_exc())
            cur_try += 1
            if cur_try < max_n_tries:
                print("Try again now...")

    print(f"Book writeup failed after {max_n_tries} tries! Stop here.")
    return False


def process_book_task(
    book,
    output_dir,
    latex_template_dir,
    idea_filename,
    experimental_log_filename,
    writer_model_name,
    research_cutoff,
    target_audience="Senior Software & Systems Engineers",
    book_style="Practical Architecture & Production Engineering",
):
    book_id = book.get("book_id") or book.get("paper_id")
    try:
        print(f"Starting processing for book '{book_id}'...")
        book_dest_dir = osp.join(output_dir, book_id)
        shutil.rmtree(book_dest_dir, ignore_errors=True)
        os.makedirs(book_dest_dir, exist_ok=True)
        final_book_path = osp.join(book_dest_dir, "final_book.pdf")

        content_dest_dir = osp.join(book_dest_dir, "raw_materials")
        os.makedirs(content_dest_dir, exist_ok=True)
        shutil.copytree(
            src=book["content_folder"], dst=content_dest_dir, dirs_exist_ok=True
        )
        if book.get("pdf_path") and osp.exists(book["pdf_path"]):
            shutil.copy(
                src=book["pdf_path"],
                dst=osp.join(content_dest_dir, "original_reference.pdf"),
            )

        success = write_single_book(
            base_dir=book_dest_dir,
            latex_template_dir=latex_template_dir,
            idea_filename=idea_filename,
            experimental_log_filename=experimental_log_filename,
            writer_model_name=writer_model_name,
            research_cutoff=research_cutoff,
            target_audience=target_audience,
            book_style=book_style,
        )

        if success:
            return {
                "book_id": book_id,
                "paper_id": book_id,
                "status": "success",
                "book_path": final_book_path,
                "paper_path": final_book_path,
            }
        return {
            "book_id": book_id,
            "paper_id": book_id,
            "status": "failed",
            "book_path": "",
            "paper_path": "",
        }
    except Exception as e:
        print(f"CRITICAL ERROR in worker for book {book_id}: {e}")
        print(traceback.format_exc())
        return {
            "book_id": book_id,
            "paper_id": book_id,
            "status": "error",
            "error": str(e),
        }


def run_batch_book_writeup(
    latex_template_dir: str,
    process_root_folder: bool = True,
    root_input_folder: str = "",
    book_info_path: str = "",
    output_dir: str = "",
    output_dir_prefix: str = "book_batch",
    date_str: str = "",
    idea_filename: str = "book_proposal.md",
    experimental_log_filename: str = "technical_notes.md",
    writer_model_name: str = "inherit",
    research_cutoff: str = None,
    target_audience: str = "Senior Software & Systems Engineers",
    book_style: str = "Practical Architecture & Production Engineering",
    n_books: int = -1,
    max_workers: int = 4,
):
    from datetime import datetime

    if not research_cutoff or str(research_cutoff).strip().lower() in ("current", "auto", "latest", "none", "null"):
        research_cutoff = datetime.now().strftime("%Y-%m")
    if output_dir == "":
        output_dir = create_log_folder(
            prefix=output_dir_prefix, date_str=date_str, log_dir="./logs/book_writing"
        )
    os.makedirs(output_dir, exist_ok=True)

    candidates = []
    if process_root_folder:
        if not root_input_folder:
            raise ValueError("Please provide root_input_folder.")
        for item_id in os.listdir(root_input_folder):
            item_dir = osp.join(root_input_folder, item_id)
            if not osp.isdir(item_dir):
                continue
            content_folder = osp.join(item_dir, "raw_materials")
            if not osp.exists(content_folder):
                continue
            candidates.append(
                {
                    "book_id": item_id,
                    "content_folder": content_folder,
                    "pdf_path": osp.join(item_dir, f"{item_id}.pdf"),
                }
            )
    else:
        with open(book_info_path, "r", encoding="utf-8") as f:
            candidates = json.load(f)

    if n_books > 0:
        candidates = candidates[:n_books]

    log_path = osp.join(output_dir, "book_writing_log.json")
    write_log = {}
    if osp.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            write_log = json.load(f)

    to_process = [
        b
        for b in candidates
        if not (
            (b.get("book_id") or b.get("paper_id")) in write_log
            and write_log[b.get("book_id") or b.get("paper_id")].get("status") == "success"
        )
    ]

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_book_task,
                book,
                output_dir,
                latex_template_dir,
                idea_filename,
                experimental_log_filename,
                writer_model_name,
                research_cutoff,
                target_audience,
                book_style,
            ): (book.get("book_id") or book.get("paper_id"))
            for book in to_process
        }
        for future in tqdm.tqdm(
            as_completed(futures), total=len(futures), desc="Writing Books"
        ):
            bid = futures[future]
            try:
                res = future.result()
                write_log[bid] = res
                with open(log_path, "w", encoding="utf-8") as f:
                    json.dump(write_log, f, indent=4)
            except Exception as e:
                print(f"Exception handling result for book {bid}: {e}")

    return log_path
