import json
import os
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestBookOrchestraImportsAndPrompts(unittest.TestCase):
    def test_agent_and_writer_imports_and_aliases(self):
        from methods.agents.outline_agent import OutlineAgent, BookBlueprintAgent
        from methods.agents.literature_review_agent import (
            HybridLiteratureAgent,
            TechnicalResearchAgent,
        )
        from methods.agents.section_writing_agent import (
            SectionWritingAgent,
            ChapterWritingAgent,
        )
        from methods.agents.content_refinement_agent import (
            ContentRefinementAgent,
            EditorialRefinementAgent,
        )
        from methods.agents.plotting_agent import (
            PlottingAgent,
            TechnicalIllustrationAgent,
        )
        from methods.book_writer import write_single_book, run_batch_book_writeup
        from methods.book_writer_with_plotting import (
            write_single_book as write_single_book_plot,
        )
        from autoraters.agent_review import (
            perform_review_agentreview,
            perform_book_editorial_review,
        )
        from autoraters import sxs_book_quality
        from utils.pdf_utils import load_book, latex_to_markdown

        self.assertIs(OutlineAgent, BookBlueprintAgent)
        self.assertIs(HybridLiteratureAgent, TechnicalResearchAgent)
        self.assertIs(SectionWritingAgent, ChapterWritingAgent)
        self.assertIs(ContentRefinementAgent, EditorialRefinementAgent)
        self.assertIs(PlottingAgent, TechnicalIllustrationAgent)
        self.assertIs(perform_review_agentreview, perform_book_editorial_review)
        self.assertTrue(callable(load_book))
        self.assertTrue(callable(write_single_book))
        self.assertTrue(callable(write_single_book_plot))
        self.assertTrue(callable(run_batch_book_writeup))
        self.assertTrue(callable(latex_to_markdown))
        self.assertTrue(hasattr(sxs_book_quality, "rate_sxs_book_quality"))

    def test_prompt_templates_and_integrity(self):
        from utils.prompt_utils import (
            TECHNICAL_BOOK_INTEGRITY_PROMPT,
            UNIVERSAL_NO_LEAKAGE_PROMPT,
        )
        from methods.prompts.outline_agent import outline_agent_system_prompt
        from methods.prompts.literature_review_agent import (
            literature_review_agent_writter_prompt,
        )
        from methods.prompts.section_writing_agent import section_writing_agent_prompt
        from methods.prompts.content_refinement_agent import (
            content_refinement_agent_system_prompt,
        )
        from methods.prompts.format_agent import format_agent_system_prompt
        from autoraters.prompts.sxs_quality_prompts import (
            sxs_book_quality_system_prompt,
            sxs_ecosystem_quality_system_prompt,
        )

        self.assertEqual(TECHNICAL_BOOK_INTEGRITY_PROMPT, UNIVERSAL_NO_LEAKAGE_PROMPT)
        self.assertNotIn("double-blind review", TECHNICAL_BOOK_INTEGRITY_PROMPT.lower())
        self.assertIn("Principal / Staff", TECHNICAL_BOOK_INTEGRITY_PROMPT)

        formatted_outline = outline_agent_system_prompt.format(cutoff_date="2026-11")
        self.assertIn("2026-11", formatted_outline)
        self.assertIn("book_metadata", formatted_outline)

        formatted_lit = literature_review_agent_writter_prompt.format(
            paper_count=10, min_cite_paper_count=8, cutoff_date="2026-11"
        )
        self.assertIn("10", formatted_lit)
        self.assertIn("8", formatted_lit)

        self.assertIn("book_1", sxs_book_quality_system_prompt)
        self.assertIn("book_1", sxs_ecosystem_quality_system_prompt)

        self.assertIn("objectivesbox", section_writing_agent_prompt)
        self.assertIn("lstlisting", section_writing_agent_prompt)
        self.assertIn("Editorial Review Board", content_refinement_agent_system_prompt)
        self.assertIn("lstlisting", format_agent_system_prompt)


class TestBookTemplatesAndExamples(unittest.TestCase):
    def test_book_templates_exist_and_have_publisher_environments(self):
        required_envs = [
            "objectivesbox",
            "tipbox",
            "warningbox",
            "pitfallbox",
            "notebox",
            "architecturebox",
            "exercisesbox",
            "listings",
            "lstset",
        ]
        for tpl in [
            "oreilly_style_book",
            "manning_in_action_book",
            "systems_handbook",
        ]:
            tpl_dir = os.path.join(REPO_ROOT, "templates", tpl)
            tex_path = os.path.join(tpl_dir, "template.tex")
            guide_path = os.path.join(tpl_dir, "guidelines.md")
            bib_path = os.path.join(tpl_dir, "references.bib")

            self.assertTrue(os.path.exists(tex_path), f"Missing {tex_path}")
            self.assertTrue(os.path.exists(guide_path), f"Missing {guide_path}")
            self.assertTrue(os.path.exists(bib_path), f"Missing {bib_path}")

            with open(tex_path, "r", encoding="utf-8") as f:
                tex = f.read()
            self.assertIn(r"\documentclass", tex)
            self.assertIn(r"\chapter", tex)
            for env in required_envs:
                self.assertIn(env, tex, f"{env} missing in {tpl}")

    def test_example_materials_and_frontend_example(self):
        raw_dir = os.path.join(
            REPO_ROOT, "examples", "distributed_llm_systems_book", "raw_materials"
        )
        for fname in [
            "book_proposal.md",
            "technical_notes.md",
        ]:
            self.assertTrue(os.path.exists(os.path.join(raw_dir, fname)))

        # Verify legacy paper templates and CVPR assets are removed
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "templates", "cvpr2025")))
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "templates", "iclr2025")))
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "paper_writing_cli.py")))
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, "assets", "overview.png")))

        fe_example = os.path.join(
            REPO_ROOT, "frontend", "examples", "it_book_example.json"
        )
        self.assertTrue(os.path.exists(fe_example))
        with open(fe_example, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("idea", data)
        self.assertIn("log", data)
        self.assertIn("AtlasGW", data["idea"])


class TestPdfUtilsAndCli(unittest.TestCase):
    def test_latex_to_markdown_and_pdf_fallback(self):
        from utils.pdf_utils import (
            latex_to_markdown,
            compile_latex,
            load_book,
            pdf_to_grid_images,
        )

        sample_tex = r"""
\documentclass{book}
\title{Distributed Systems Engineering}
\author{Staff Engineer}
\begin{document}
\chapter{Foundations}
\begin{objectivesbox}
Understand KV-cache sizing and backpressure.
\end{objectivesbox}
\begin{lstlisting}[language=Python,caption={Bounded Queue},label={lst:queue}]
async def enqueue(q, item):
    await q.put(item)
\end{lstlisting}
\begin{warningbox}
Never deploy unbounded queues in production gateways.
\end{warningbox}
\end{document}
"""
        md = latex_to_markdown(sample_tex)
        self.assertIn("# Distributed Systems Engineering", md)
        self.assertIn("# Foundations", md)
        self.assertIn("```python", md)
        self.assertIn("Chapter Learning Objectives", md)
        self.assertIn("Production Warning", md)

        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "sample.tex")
            pdf_path = os.path.join(tmpdir, "sample.pdf")
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(sample_tex)

            compile_latex(tmpdir, pdf_path, "sample")
            self.assertTrue(os.path.exists(pdf_path))
            extracted = load_book(pdf_path, min_size=10)
            self.assertIn("Foundations", extracted)

            grids = pdf_to_grid_images(pdf_path, os.path.join(tmpdir, "grids"))
            self.assertTrue(len(grids) >= 1)
            self.assertTrue(os.path.exists(grids[0]))

    def test_cli_arg_parsers(self):
        from book_writing_cli import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            [
                "--raw_materials_dir",
                "examples/distributed_llm_systems_book/raw_materials",
                "--target_audience",
                "Staff Engineers",
            ]
        )
        self.assertEqual(args.latex_template_dir, "templates/oreilly_style_book")
        self.assertEqual(args.proposal_filename, "book_proposal.md")
        self.assertEqual(args.technical_notes_filename, "technical_notes.md")
        self.assertEqual(args.target_audience, "Staff Engineers")


class TestEndToEndMockedBookPipeline(unittest.TestCase):
    def test_write_single_book_end_to_end_mocked(self):
        from methods.book_writer import write_single_book

        sample_outline = {
            "book_metadata": {
                "title": "Engineering Production LLM Systems",
                "subtitle": "Memory, Latency, and Reliability at Scale",
                "target_audience": "Senior Software & Systems Engineers",
                "running_case_study": "AtlasGW Gateway",
                "terminology_glossary": {"TTFT": "Time to First Token"},
            },
            "plotting_plan": [],
            "intro_related_work_plan": {
                "introduction_strategy": {
                    "hook_hypothesis": "Production GPU OOMs",
                    "problem_gap_hypothesis": "Missing backpressure",
                    "search_directions": ["PagedAttention vLLM memory"],
                },
                "related_work_strategy": {
                    "overview": "Serving architectures",
                    "subsections": [],
                },
            },
            "section_plan": [
                {
                    "section_title": "System Design, Data Models, and Core Engine",
                    "learning_objectives": ["Model KV cache"],
                    "subsections": [
                        {
                            "subsection_title": "KV-Cache Capacity",
                            "content_bullets": ["320 KiB/token FP16"],
                            "citation_hints": ["Kwon2023EfficientMemory"],
                        },
                        {
                            "subsection_title": "Admission Control",
                            "content_bullets": ["Bounded asyncio queue"],
                            "citation_hints": [],
                        },
                    ],
                }
            ],
        }

        updated_template_tex = r"""\documentclass[11pt,oneside]{book}
\begin{document}
\frontmatter
\title{Engineering Production LLM Systems}
\maketitle
\chapter*{Preface}
Preface content grounded in AtlasGW case study.
\mainmatter
\chapter{Problem Landscape, Core Concepts, and Ecosystem Architecture}
\label{ch:foundations}
Foundations referencing \cite{Kwon2023EfficientMemory}.
\chapter{System Design, Data Models, and Core Engine}
\label{ch:core_architecture}
Core architecture placeholder.
\backmatter
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""

        completed_book_tex = r"""\documentclass[11pt,oneside]{book}
\begin{document}
\frontmatter
\title{Engineering Production LLM Systems}
\maketitle
\chapter*{Preface}
Preface content grounded in AtlasGW case study.
\mainmatter
\chapter{Problem Landscape, Core Concepts, and Ecosystem Architecture}
\label{ch:foundations}
Foundations referencing \cite{Kwon2023EfficientMemory}.
\chapter{System Design, Data Models, and Core Engine}
\label{ch:core_architecture}
\begin{objectivesbox}
\begin{itemize}
\item Size KV-cache VRAM budgets accurately.
\end{itemize}
\end{objectivesbox}
At 8,192 tokens, FP16 KV cache requires 2.5 GiB per concurrent sequence.
\begin{lstlisting}[language=Python,caption={Bounded Token Queue},label={lst:queue}]
async def acquire(tokens: int) -> bool:
    return tokens > 0
\end{lstlisting}
\begin{warningbox}
Avoid unbounded asyncio queues in front of GPU workers.
\end{warningbox}
\begin{exercisesbox}
Compute concurrency limits for FP8 KV cache on 8x80GB H100 nodes.
\end{exercisesbox}
\backmatter
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""

        mock_review_v0 = {
            "Summary": "Solid draft",
            "Strengths": ["Clear KV-cache math"],
            "Weaknesses": ["Add failure mode table"],
            "Technical_Depth": 3,
            "Practical_Utility": 3,
            "Code_and_Architecture_Quality": 3,
            "Pedagogical_Clarity": 3,
            "Structural_Coherence": 3,
            "Editorial_Polish": 3,
            "Questions": ["What happens on retry storms?"],
            "Limitations": ["Single cluster scope"],
            "Ethical Concerns": False,
            "Overall": 7,
            "Confidence": 4,
            "Decision": "Accept",
        }

        mock_review_v1 = dict(mock_review_v0)
        mock_review_v1["Overall"] = 8
        mock_review_v1["Technical_Depth"] = 4

        review_call_counter = {"count": 0}

        def fake_gemini_with_contents(
            contents,
            model_name,
            max_retries=5,
            base_interval_sec=5,
            result_parsing_func=None,
            generation_configs=None,
            check_parsed_response_not_none=True,
        ):
            # Distinguish agents by contents / result_parsing_func
            prompt_blob = " ".join(str(getattr(c, "text", c)) for c in contents)
            if "outline.json" in prompt_blob and "book_metadata" in prompt_blob and result_parsing_func is None:
                return {"raw_response": json.dumps(sample_outline), "parsed_response": sample_outline}
            if "candidates" in prompt_blob and "DiscoveryResult" in prompt_blob:
                cand_resp = {
                    "section_name": "Foundations",
                    "candidates": [
                        {
                            "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
                            "year": 2023,
                            "reason": "Canonical PagedAttention paper",
                            "authors": ["Woosuk Kwon", "Zhuohan Li"],
                            "venue": "SOSP",
                        }
                    ],
                }
                return {"raw_response": json.dumps(cand_resp), "parsed_response": cand_resp}
            if "Preface/Introduction and Foundations/Ecosystem Chapter" in prompt_blob:
                return {"raw_response": updated_template_tex, "parsed_response": updated_template_tex}
            if "book_continuity_ledger.json" in prompt_blob:
                return {"raw_response": completed_book_tex, "parsed_response": completed_book_tex}
            if "EDITORIAL REVISION ITERATION" in prompt_blob:
                payload = json.dumps(
                    {
                        "json": {"actions_taken": ["Added retry-storm failure note"]},
                        "latex": completed_book_tex.replace(
                            "Avoid unbounded asyncio queues",
                            "Avoid unbounded asyncio queues and unjittered retry storms",
                        ),
                    }
                )
                return {"raw_response": payload, "parsed_response": payload}
            if "Typesetting" in prompt_blob or "formatting_feedback" in prompt_blob or "CURRENT FORMATTING FEEDBACK" in prompt_blob:
                return {"raw_response": completed_book_tex, "parsed_response": completed_book_tex}
            return {"raw_response": "{}", "parsed_response": {}}

        def fake_gemini_text_prompt(prompt, model_name, **kwargs):
            if "Here is the technical book manuscript you are asked to review" in prompt:
                idx = review_call_counter["count"]
                # First 3 calls belong to v0 ensemble, next calls to v1
                rev = mock_review_v0 if idx < 3 else mock_review_v1
                review_call_counter["count"] += 1
                return {"raw_response": json.dumps(rev), "parsed_response": dict(rev)}
            # Meta-review
            rev = mock_review_v0 if review_call_counter["count"] <= 3 else mock_review_v1
            return {"raw_response": json.dumps(rev), "parsed_response": dict(rev)}

        def fake_gemini_with_images(prompt, images, model_name, **kwargs):
            if "book_continuity_ledger.json" in prompt:
                return {"raw_response": completed_book_tex, "parsed_response": completed_book_tex}
            fmt_resp = {
                "figure_and_tables": {"Table 1": {"detected_issue": "None", "suggested_fix": "None"}},
                "other_issues": [],
            }
            return {"raw_response": json.dumps(fmt_resp), "parsed_response": fmt_resp}

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_src = os.path.join(
                REPO_ROOT, "examples", "distributed_llm_systems_book", "raw_materials"
            )
            raw_dst = os.path.join(tmpdir, "raw_materials")
            os.makedirs(raw_dst, exist_ok=True)
            for fname in ("book_proposal.md", "technical_notes.md"):
                with open(os.path.join(raw_src, fname), "r", encoding="utf-8") as sf:
                    with open(os.path.join(raw_dst, fname), "w", encoding="utf-8") as df:
                        df.write(sf.read())

            template_dir = os.path.join(REPO_ROOT, "templates", "oreilly_style_book")

            with (
                patch(
                    "methods.agents.outline_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_with_contents,
                ),
                patch(
                    "methods.agents.literature_review_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_with_contents,
                ),
                patch(
                    "utils.llm_backend_utils.call_gemini_with_images",
                    side_effect=fake_gemini_with_images,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_with_contents,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_gemini_with_images",
                    side_effect=fake_gemini_with_images,
                ),
                patch(
                    "autoraters.agent_review.call_gemini_with_text_prompt",
                    side_effect=fake_gemini_text_prompt,
                ),
            ):
                success = write_single_book(
                    base_dir=tmpdir,
                    latex_template_dir=template_dir,
                    idea_filename="book_proposal.md",
                    experimental_log_filename="technical_notes.md",
                    research_cutoff="2026-11",
                )

            self.assertTrue(success)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.pdf")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.tex")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.md")))
            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, "book_continuity_ledger.json"))
            )
            self.assertTrue(
                os.path.exists(
                    os.path.join(tmpdir, "literature_agent_output", "citation_map.json")
                )
            )
            chapters_dir = os.path.join(tmpdir, "latex_writeup", "chapters")
            self.assertTrue(os.path.isdir(chapters_dir))
            chapter_files = os.listdir(chapters_dir)
            self.assertGreaterEqual(len(chapter_files), 2)

            with open(os.path.join(tmpdir, "final_book.md"), "r", encoding="utf-8") as f:
                md_out = f.read()
            self.assertIn("Engineering Production LLM Systems", md_out)
            self.assertIn("Bounded Token Queue", md_out)

    def test_write_single_book_with_plotting_mocked(self):
        import base64
        from methods.book_writer_with_plotting import write_single_book
        from utils.diagram_plot_utils import _minimal_png_base64

        sample_outline = {
            "book_metadata": {
                "title": "Engineering Production LLM Systems",
                "target_audience": "Senior Engineers",
            },
            "plotting_plan": [
                {
                    "figure_id": "fig_gateway_topology",
                    "title": "AtlasGW Topology",
                    "plot_type": "diagram",
                    "data_source": "both",
                    "objective": "Show control plane and data plane separation.",
                    "aspect_ratio": "16:9",
                }
            ],
            "intro_related_work_plan": {
                "introduction_strategy": {
                    "hook_hypothesis": "Tail latency spikes",
                    "problem_gap_hypothesis": "Unbounded queues",
                    "search_directions": [],
                },
                "related_work_strategy": {"overview": "Systems", "subsections": []},
            },
            "section_plan": [],
        }

        sample_tex = r"""\documentclass[11pt,oneside]{book}
\begin{document}
\chapter{Foundations}
\includegraphics[width=0.9\linewidth]{figures/fig_gateway_topology.jpg}
\end{document}
"""
        sample_b64 = _minimal_png_base64()

        def fake_gemini_contents(contents, model_name, result_parsing_func=None, **kwargs):
            blob = " ".join(str(getattr(c, "text", c)) for c in contents)
            if "outline.json" in blob and "book_metadata" in blob and result_parsing_func is None:
                return {"raw_response": json.dumps(sample_outline), "parsed_response": sample_outline}
            if "Preface/Introduction and Foundations/Ecosystem Chapter" in blob:
                return {"raw_response": sample_tex, "parsed_response": sample_tex}
            if "EDITORIAL REVISION ITERATION" in blob:
                payload = json.dumps({"json": {"actions_taken": []}, "latex": sample_tex})
                return {"raw_response": payload, "parsed_response": payload}
            if "Detailed Description" in blob or "figure to be generated" in blob:
                return {"raw_response": "White background diagram.", "parsed_response": "White background diagram."}
            return {"raw_response": sample_tex, "parsed_response": sample_tex}

        fake_review = {
            "Summary": "Good",
            "Strengths": ["Clear"],
            "Weaknesses": [],
            "Technical_Depth": 4,
            "Practical_Utility": 4,
            "Code_and_Architecture_Quality": 4,
            "Pedagogical_Clarity": 4,
            "Structural_Coherence": 4,
            "Editorial_Polish": 4,
            "Questions": [],
            "Limitations": [],
            "Ethical Concerns": False,
            "Overall": 9,
            "Confidence": 5,
            "Decision": "Accept",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_src = os.path.join(
                REPO_ROOT, "examples", "distributed_llm_systems_book", "raw_materials"
            )
            raw_dst = os.path.join(tmpdir, "raw_materials")
            os.makedirs(raw_dst, exist_ok=True)
            for fname in ("book_proposal.md", "technical_notes.md"):
                with open(os.path.join(raw_src, fname), "r", encoding="utf-8") as sf:
                    with open(os.path.join(raw_dst, fname), "w", encoding="utf-8") as df:
                        df.write(sf.read())

            template_dir = os.path.join(REPO_ROOT, "templates", "systems_handbook")

            with (
                patch(
                    "methods.agents.outline_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_contents,
                ),
                patch(
                    "methods.agents.literature_review_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_contents,
                ),
                patch(
                    "utils.diagram_plot_utils.call_gemini_with_contents",
                    side_effect=fake_gemini_contents,
                ),
                patch(
                    "utils.diagram_plot_utils.generate_image_with_gemini",
                    return_value=sample_b64,
                ),
                patch(
                    "utils.diagram_plot_utils.critique_and_revise_figure",
                    return_value=("White background diagram.", "No changes needed."),
                ),
                patch(
                    "utils.diagram_plot_utils.generate_figure_caption",
                    return_value="Control and data plane architecture of AtlasGW.",
                ),
                patch(
                    "utils.llm_backend_utils.call_gemini_with_images",
                    return_value={"raw_response": sample_tex, "parsed_response": sample_tex},
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_gemini_with_contents",
                    side_effect=fake_gemini_contents,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_gemini_with_images",
                    return_value={
                        "raw_response": "{}",
                        "parsed_response": {"figure_and_tables": {}, "other_issues": []},
                    },
                ),
                patch(
                    "autoraters.agent_review.call_gemini_with_text_prompt",
                    return_value={"raw_response": json.dumps(fake_review), "parsed_response": dict(fake_review)},
                ),
            ):
                success = write_single_book(
                    base_dir=tmpdir,
                    latex_template_dir=template_dir,
                    idea_filename="book_proposal.md",
                    experimental_log_filename="technical_notes.md",
                    research_cutoff="2026-11",
                    plotting_max_critic_rounds=1,
                )

            self.assertTrue(success)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "plotting_results.json")))
            self.assertTrue(
                os.path.exists(os.path.join(tmpdir, "latex_writeup", "figures", "info.json"))
            )
            self.assertTrue(
                os.path.exists(
                    os.path.join(tmpdir, "latex_writeup", "figures", "fig_gateway_topology.jpg")
                )
            )
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.pdf")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.md")))

    def test_antigravity_run_workflow_engine_end_to_end(self):
        import ast
        import asyncio
        from methods.agy_workflow_runner import run_book_orchestra_workflow
        from utils.gemini_utils import (
            set_antigravity_agent_runner,
            call_gemini_with_text_prompt,
        )

        # 1. Verify workflow entrypoint compiles with top-level await & skill/schema exist
        wf_script_path = os.path.join(
            REPO_ROOT, "workflows", "book_orchestra_workflow.py"
        )
        wf_json_path = os.path.join(REPO_ROOT, "workflows", "workflow.json")
        skill_path = os.path.join(
            REPO_ROOT, ".agents", "skills", "book-orchestra", "SKILL.md"
        )
        self.assertTrue(os.path.exists(wf_script_path))
        self.assertTrue(os.path.exists(wf_json_path))
        self.assertTrue(os.path.exists(skill_path))

        with open(wf_script_path, "r", encoding="utf-8") as f:
            wf_src = f.read()
        compile(
            wf_src,
            wf_script_path,
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )

        # 2. Verify Antigravity in-process bridge works without external API keys
        set_antigravity_agent_runner(
            lambda prompt, **kw: '```json\n{"bridged": true}\n```'
        )
        try:
            bridged_res = call_gemini_with_text_prompt("ping")
            self.assertEqual(bridged_res["parsed_response"], {"bridged": True})
        finally:
            set_antigravity_agent_runner(None)

        # 3. Run full 4-phase Antigravity `run_workflow` orchestration with mocked primitives
        phases_visited = []
        logs_emitted = []

        def fake_phase(name: str):
            phases_visited.append(name)

        def fake_log(*items):
            logs_emitted.append(" ".join(str(x) for x in items))

        sample_outline = {
            "book_metadata": {
                "title": "Engineering Production LLM Systems",
                "running_case_study": "AtlasGW Gateway",
                "terminology_glossary": {"TTFT": "Time to First Token"},
            },
            "plotting_plan": [
                {
                    "figure_id": "fig_gateway_topology",
                    "title": "AtlasGW Control and Data Plane",
                    "plot_type": "diagram",
                    "data_source": "both",
                    "objective": "Show gateway topology.",
                    "aspect_ratio": "16:9",
                }
            ],
            "intro_related_work_plan": {
                "introduction_strategy": {
                    "hook_hypothesis": "Production OOMs",
                    "problem_gap_hypothesis": "Unbounded queues",
                    "search_directions": ["PagedAttention vLLM"],
                },
                "related_work_strategy": {"overview": "Serving", "subsections": []},
            },
            "section_plan": [
                {
                    "section_title": "System Design, Data Models, and Core Engine",
                    "learning_objectives": ["Model KV cache"],
                    "subsections": [
                        {
                            "subsection_title": "KV-Cache Sizing",
                            "content_bullets": ["320 KiB/token FP16"],
                            "citation_hints": ["Kwon2023PagedAttention"],
                        }
                    ],
                }
            ],
        }

        sample_tex = r"""\documentclass[11pt,oneside]{book}
\begin{document}
\chapter*{Preface}
Preface for AtlasGW.
\chapter{Problem Landscape, Core Concepts, and Ecosystem Architecture}
Foundations citing \cite{Kwon2023EfficientMemory}.
\chapter{System Design, Data Models, and Core Engine}
\begin{objectivesbox}
Size KV-cache memory accurately.
\end{objectivesbox}
\begin{lstlisting}[language=Python,caption={Bounded Queue},label={lst:q}]
async def acquire(tokens: int) -> bool:
    return tokens > 0
\end{lstlisting}
\end{document}
"""

        review_round = {"calls": 0}

        async def fake_agent(
            prompt=None,
            schema=None,
            role=None,
            type_name="self",
            workspace="inherit",
            model=None,
        ):
            role_str = role or ""
            if "Blueprint Architect" in role_str:
                return sample_outline
            if role_str.startswith("Research:"):
                return {
                    "section_name": "Foundations",
                    "candidates": [
                        {
                            "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
                            "year": 2023,
                            "authors": ["Woosuk Kwon"],
                            "venue": "SOSP",
                            "reason": "PagedAttention",
                        }
                    ],
                }
            if "Preface & Foundations Author" in role_str:
                return sample_tex
            if "Principal Technical Book Author" in role_str:
                return sample_tex
            if "Review Board" in role_str or "Editor-in-Chief" in role_str:
                review_round["calls"] += 1
                score = 7 if review_round["calls"] <= 4 else 9
                return {
                    "Summary": "Strong systems book",
                    "Strengths": ["KV-cache math"],
                    "Weaknesses": ["Add retry jitter note"],
                    "Technical_Depth": 4,
                    "Practical_Utility": 4,
                    "Code_and_Architecture_Quality": 4,
                    "Pedagogical_Clarity": 4,
                    "Structural_Coherence": 4,
                    "Editorial_Polish": 4,
                    "Questions": [],
                    "Limitations": [],
                    "Overall": score,
                    "Confidence": 4,
                    "Decision": "Accept",
                }
            if "Editorial Revision" in role_str:
                return {
                    "worklog": {
                        "addressed_weaknesses": ["Added retry jitter note"],
                        "integrated_answers": [],
                        "actions_taken": ["Updated Chapter 2"],
                    },
                    "latex": sample_tex,
                }
            return sample_tex

        async def fake_parallel(items, fn):
            results = []
            for item in items:
                res = fn(item)
                if asyncio.iscoroutine(res):
                    res = await res
                results.append(res)
            return results

        async def fake_pipeline(items, fn):
            return await fake_parallel(items, fn)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = os.path.join(tmpdir, "book_out")
            res = asyncio.run(
                run_book_orchestra_workflow(
                    workflow_args={
                        "raw_materials_dir": os.path.join(
                            REPO_ROOT,
                            "examples",
                            "distributed_llm_systems_book",
                            "raw_materials",
                        ),
                        "latex_template_dir": os.path.join(
                            REPO_ROOT, "templates", "oreilly_style_book"
                        ),
                        "output_dir": out_dir,
                        "use_plotting": True,
                        "max_reflections": 1,
                    },
                    phase_fn=fake_phase,
                    log_fn=fake_log,
                    agent_fn=fake_agent,
                    parallel_fn=fake_parallel,
                    pipeline_fn=fake_pipeline,
                )
            )

            self.assertEqual(res["status"], "success")
            self.assertEqual(len(phases_visited), 4)
            self.assertTrue(os.path.exists(res["final_book_pdf"]))
            self.assertTrue(os.path.exists(res["final_book_tex"]))
            self.assertTrue(os.path.exists(res["final_book_md"]))
            self.assertGreaterEqual(len(res["chapters"]), 2)


if __name__ == "__main__":
    unittest.main()
