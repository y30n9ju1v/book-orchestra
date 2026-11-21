import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
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
            self.assertIn(r"\usepackage{setspace}", tex)
            self.assertIn(r"\setstretch{1.12}", tex)
            self.assertIn(r"\setlength{\parskip}{0.2\baselineskip}", tex)
            for env in required_envs:
                self.assertIn(env, tex, f"{env} missing in {tpl}")

    def test_body_reading_rhythm_is_restored_deterministically(self):
        from methods.agy_workflow_runner import _ensure_body_reading_rhythm

        compact_tex = r"\documentclass{book}\begin{document}Body\end{document}"
        spaced_tex = _ensure_body_reading_rhythm(compact_tex)
        self.assertIn(r"\usepackage{setspace}", spaced_tex)
        self.assertIn(r"\setstretch{1.12}", spaced_tex)
        self.assertIn(r"\setlength{\parskip}{0.2\baselineskip}", spaced_tex)
        self.assertIn(r"\setlength{\parindent}{1em}", spaced_tex)
        self.assertEqual(
            _ensure_body_reading_rhythm(spaced_tex),
            spaced_tex,
            "Typography restoration must be idempotent.",
        )

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

    def test_semantic_preview_preserves_korean_and_hides_latex_markup(self):
        from utils.pdf_utils import compile_latex, load_book

        sample_tex = r"""
\documentclass{book}
\title{한국어 미리보기}
\author{테스트 저자}
\begin{document}
\chapter{첫 장}
\begin{objectivesbox}
독자가 자연스럽게 읽을 수 있어야 한다.
\end{objectivesbox}
\begin{lstlisting}[language=Python]
async def fetch():
    return "ok"
\end{lstlisting}
\end{document}
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "korean.tex")
            pdf_path = os.path.join(tmpdir, "korean.pdf")
            with open(tex_path, "w", encoding="utf-8") as stream:
                stream.write(sample_tex)

            self.assertTrue(compile_latex(tmpdir, pdf_path, "korean"))
            extracted = load_book(pdf_path, min_size=10)
            self.assertIn("한국어 미리보기", extracted)
            self.assertIn("첫 장", extracted)
            self.assertNotIn(r"\begin{objectivesbox}", extracted)
            self.assertNotIn("```", extracted)

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

        slide_args = parser.parse_args(["--slides_file", "lecture.pptx"])
        self.assertEqual(slide_args.slides_file, "lecture.pptx")
        self.assertIsNone(slide_args.raw_materials_dir)

        codebase_args = parser.parse_args(["--codebase_dir", "./project"])
        self.assertEqual(codebase_args.codebase_dir, "./project")
        self.assertIsNone(codebase_args.raw_materials_dir)
        self.assertIsNone(codebase_args.slides_file)

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--slides_file",
                    "lecture.pptx",
                    "--raw_materials_dir",
                    "raw_materials",
                ]
            )

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--codebase_dir",
                    "./project",
                    "--raw_materials_dir",
                    "raw_materials",
                ]
            )

    def test_codebase_ingestion_builds_redacted_grounded_materials(self):
        from utils.codebase_ingestion import prepare_codebase_materials
        from utils.path_security import validate_codebase_output_workspace

        with tempfile.TemporaryDirectory() as tmpdir:
            root = os.path.join(tmpdir, "sample_project")
            template = os.path.join(tmpdir, "template")
            output = os.path.join(root, "book_output_codebase")
            os.makedirs(os.path.join(root, "src"))
            os.makedirs(os.path.join(root, "tests"))
            os.makedirs(os.path.join(root, "node_modules"))
            os.makedirs(template)
            with open(os.path.join(template, "template.tex"), "w", encoding="utf-8") as stream:
                stream.write(r"\documentclass{book}\begin{document}\end{document}")
            with open(os.path.join(template, "guidelines.md"), "w", encoding="utf-8") as stream:
                stream.write("# Guidelines")
            with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as stream:
                stream.write("# Queue Service\nProcesses jobs with bounded retries.\n")
            with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as stream:
                stream.write('[project]\nname = "queue-service"\n')
            with open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as stream:
                stream.write('{"client_secret": "json-secret-must-not-leak"}\n')
            source_path = os.path.join(root, "src", "main.py")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    'API_KEY = "sk-this-value-must-never-reach-the-book"\n\n'
                    "class Worker:\n"
                    "    def run(self, job):\n"
                    "        return job.execute()\n"
                )
            with open(os.path.join(root, "tests", "test_main.py"), "w", encoding="utf-8") as stream:
                stream.write("def test_worker_contract():\n    assert True\n")
            with open(os.path.join(root, ".env"), "w", encoding="utf-8") as stream:
                stream.write("PASSWORD=do-not-read\n")
            with open(os.path.join(root, ".env.staging"), "w", encoding="utf-8") as stream:
                stream.write("SECRET=staging-do-not-read\n")
            with open(os.path.join(root, "secrets.yaml"), "w", encoding="utf-8") as stream:
                stream.write("token: yaml-secret-do-not-read\n")
            with open(os.path.join(root, "src", "token.py"), "w", encoding="utf-8") as stream:
                stream.write(
                    'DB_URL = "postgres://admin:supersecretpw@db.internal:5432/app"\n'
                    'GOOGLE_KEY = "AIzaSyA12345678901234567890123456789012"\n'
                    "def verify_token(raw: str) -> bool:\n"
                    "    return bool(raw)\n"
                )
            with open(
                os.path.join(root, "node_modules", "dependency.js"),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write("module.exports = 'ignored';\n")

            validated_source, _, validated_output = validate_codebase_output_workspace(
                root, template, output
            )
            self.assertEqual(str(validated_source), os.path.realpath(root))
            self.assertEqual(str(validated_output), os.path.realpath(output))

            result = prepare_codebase_materials(
                root,
                os.path.join(output, "raw_materials"),
                target_audience="Developers new to this service",
            )
            self.assertGreaterEqual(result["included_file_count"], 4)
            self.assertGreaterEqual(result["redaction_count"], 1)
            with open(result["manifest_path"], "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            included_paths = {item["path"] for item in manifest["files"]}
            self.assertIn("src/main.py", included_paths)
            self.assertIn("src/token.py", included_paths)
            self.assertNotIn(".env", included_paths)
            self.assertNotIn(".env.staging", included_paths)
            self.assertNotIn("secrets.yaml", included_paths)
            self.assertNotIn("node_modules/dependency.js", included_paths)
            self.assertTrue(manifest["snapshot_is_secret_redacted"])

            with open(result["technical_notes_path"], "r", encoding="utf-8") as stream:
                notes = stream.read()
            self.assertIn("class Worker (line 3)", notes)
            self.assertIn("<REDACTED>", notes)
            self.assertNotIn("sk-this-value-must-never-reach-the-book", notes)
            self.assertNotIn("json-secret-must-not-leak", notes)
            self.assertNotIn("supersecretpw", notes)
            self.assertNotIn("AIzaSyA12345678901234567890123456789012", notes)

            # Verify sibling materials_dir (whose parent is tmpdir, ancestor of root) does not prune root/src
            sibling_result = prepare_codebase_materials(
                root, os.path.join(tmpdir, "sibling_raw_materials")
            )
            self.assertGreaterEqual(sibling_result["included_file_count"], 5)
            sanitized_source = os.path.join(
                output, "raw_materials", "source_code", "src", "main.py"
            )
            with open(sanitized_source, "r", encoding="utf-8") as stream:
                self.assertNotIn("sk-this-value-must-never-reach-the-book", stream.read())
            with open(source_path, "r", encoding="utf-8") as stream:
                self.assertIn("sk-this-value-must-never-reach-the-book", stream.read())

            with self.assertRaisesRegex(ValueError, "VCS metadata"):
                validate_codebase_output_workspace(
                    root, template, os.path.join(root, ".git", "book_output")
                )

    def test_pptx_slide_ingestion_preserves_grounding_notes_and_visuals(self):
        import base64

        from utils.diagram_plot_utils import _minimal_png_base64
        from utils.slide_ingestion import prepare_slide_materials

        slide_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld><p:spTree>
    <p:sp><p:nvSpPr><p:cNvPr id="1" name="Heading"/><p:cNvSpPr/><p:nvPr><p:ph/></p:nvPr></p:nvSpPr>
      <p:txBody><a:p><a:r><a:t>Async Foundations</a:t></a:r></a:p></p:txBody></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
      <p:txBody><a:p><a:r><a:t>Tasks are scheduled cooperatively</a:t></a:r><a:br/><a:r><a:t>with backpressure</a:t></a:r></a:p></p:txBody></p:sp>
    <p:pic><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic>
  </p:spTree></p:cSld>
</p:sld>"""
        rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""
        notes_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
         xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp>
    <p:nvSpPr><p:cNvPr id="1" name="Notes"/><p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
    <p:txBody><a:p><a:r><a:t>Compare a task switch with an OS thread switch.</a:t></a:r></a:p></p:txBody>
  </p:sp></p:spTree></p:cSld>
</p:notes>"""
        orientation_slide_xml = slide_xml.replace(
            "Async Foundations", "Course Orientation"
        ).replace(
            "Tasks are scheduled cooperatively", "How to use this course"
        ).replace(
            '<p:pic><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic>',
            "",
        ).replace("<p:sld ", '<p:sld show="0" ', 1)
        layout_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:nvSpPr><p:cNvPr id="1" name="Layout heading"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr></p:sp>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Footer"/><p:cNvSpPr/><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr></p:sp>
  </p:spTree></p:cSld>
</p:sldLayout>"""
        chart_xml = """<?xml version="1.0" encoding="UTF-8"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>Throughput</a:t></a:r></a:p></c:rich></c:tx></c:title>
    <c:plotArea><c:barChart><c:ser>
      <c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>workers</c:v></c:pt></c:strCache></c:strRef></c:tx>
      <c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v></c:v></c:pt><c:pt idx="1"><c:v>2</c:v></c:pt></c:strCache></c:strRef></c:cat>
      <c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>19</c:v></c:pt></c:numCache></c:numRef></c:val>
    </c:ser></c:barChart></c:plotArea>
  </c:chart>
</c:chartSpace>"""
        presentation_xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst>
</p:presentation>"""
        presentation_rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            deck_path = os.path.join(tmpdir, "lecture.pptx")
            materials_dir = os.path.join(tmpdir, "generated", "raw_materials")
            with zipfile.ZipFile(deck_path, "w", zipfile.ZIP_DEFLATED) as package:
                package.writestr("ppt/slides/slide1.xml", slide_xml)
                package.writestr("ppt/slides/slide2.xml", orientation_slide_xml)
                package.writestr("ppt/slides/_rels/slide1.xml.rels", rels_xml)
                package.writestr("ppt/presentation.xml", presentation_xml)
                package.writestr(
                    "ppt/_rels/presentation.xml.rels", presentation_rels_xml
                )
                package.writestr("ppt/notesSlides/notesSlide1.xml", notes_xml)
                package.writestr("ppt/slideLayouts/slideLayout1.xml", layout_xml)
                package.writestr("ppt/charts/chart1.xml", chart_xml)
                package.writestr(
                    "ppt/media/image1.png",
                    base64.b64decode(_minimal_png_base64()),
                )

            result = prepare_slide_materials(
                deck_path,
                materials_dir,
                target_audience="Developers new to async programming",
                author_voice="Patient and conversational",
            )

            self.assertEqual(result["slide_count"], 2)
            self.assertEqual(result["included_slide_count"], 1)
            self.assertEqual(result["hidden_slide_count"], 1)
            self.assertEqual(result["extracted_figure_count"], 1)
            with open(result["manifest_path"], "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            self.assertEqual(manifest["slides"][0]["title"], "Course Orientation")
            self.assertTrue(manifest["slides"][0]["hidden"])
            self.assertFalse(manifest["slides"][0]["included_in_book"])
            self.assertEqual(manifest["slides"][1]["title"], "Async Foundations")
            self.assertEqual(
                manifest["slides"][1]["visible_text"][1],
                "Tasks are scheduled cooperatively with backpressure",
            )
            self.assertEqual(
                manifest["slides"][1]["chart_data"],
                ["Throughput — workers: (blank) = 10; 2 = 19"],
            )
            self.assertEqual(
                manifest["slides"][1]["speaker_notes"],
                "Compare a task switch with an OS thread switch.",
            )
            self.assertEqual(len(manifest["source_sha256"]), 64)
            self.assertTrue(
                os.path.exists(
                    os.path.join(materials_dir, manifest["preserved_source"])
                )
            )
            with open(result["proposal_path"], "r", encoding="utf-8") as stream:
                proposal = stream.read()
            self.assertIn("do not write a slide-by-slide transcript", proposal)
            self.assertIn("concrete analogies", proposal)
            self.assertNotIn("Course Orientation", proposal)
            with open(result["technical_notes_path"], "r", encoding="utf-8") as stream:
                notes = stream.read()
            self.assertIn("Tasks are scheduled cooperatively with backpressure", notes)
            self.assertIn("Compare a task switch", notes)
            self.assertIn("Hidden source slides excluded", notes)
            self.assertNotIn("How to use this course", notes)
            self.assertIn("figures/slide_002_visual_01.png", notes)

            with open(
                os.path.join(materials_dir, "figures", "info.json"),
                "r",
                encoding="utf-8",
            ) as stream:
                figure_info = json.load(stream)
            self.assertEqual(figure_info[0]["source_slide"], 2)
            self.assertEqual(figure_info[0]["generated_by"], "slide_ingestion")

    @unittest.skipUnless(
        importlib.util.find_spec("fitz"), "PyMuPDF is not installed"
    )
    def test_pdf_slide_ingestion_extracts_page_text_and_large_visual(self):
        import base64
        import fitz

        from utils.diagram_plot_utils import _minimal_png_base64
        from utils.slide_ingestion import prepare_slide_materials

        with tempfile.TemporaryDirectory() as tmpdir:
            deck_path = os.path.join(tmpdir, "lecture.pdf")
            document = fitz.open()
            page = document.new_page(width=800, height=450)
            page.insert_text((60, 60), "Event Loop Mental Model", fontsize=24)
            page.insert_text((60, 95), "One thread can coordinate many waiting tasks.")
            page.insert_image(
                fitz.Rect(60, 130, 500, 390),
                stream=base64.b64decode(_minimal_png_base64()),
            )
            vector_page = document.new_page(width=800, height=450)
            vector_page.insert_text((60, 60), "Vector-only request flow", fontsize=24)
            vector_page.draw_rect(fitz.Rect(60, 120, 220, 230))
            vector_page.draw_rect(fitz.Rect(320, 120, 480, 230))
            vector_page.draw_rect(fitz.Rect(580, 120, 740, 230))
            for offset in range(8):
                vector_page.draw_line(
                    fitz.Point(220 + offset, 175), fitz.Point(320 + offset, 175)
                )
            document.save(deck_path)
            document.close()

            result = prepare_slide_materials(
                deck_path, os.path.join(tmpdir, "generated", "raw_materials")
            )
            self.assertEqual(result["slide_count"], 2)
            self.assertEqual(result["extracted_figure_count"], 2)
            with open(result["manifest_path"], "r", encoding="utf-8") as stream:
                manifest = json.load(stream)
            self.assertEqual(manifest["source_type"], "pdf")
            self.assertIn(
                "Event Loop Mental Model", manifest["slides"][0]["visible_text"]
            )
            self.assertTrue(manifest["slides"][0]["figures"])
            self.assertTrue(manifest["slides"][1]["figures"])
            with open(
                os.path.join(tmpdir, "generated", "raw_materials", "figures", "info.json"),
                "r",
                encoding="utf-8",
            ) as stream:
                figure_info = json.load(stream)
            self.assertEqual(figure_info[1]["visual_kind"], "rendered_vector_slide")

    def test_pdf_slide_ingestion_reports_missing_pymupdf(self):
        from utils.slide_ingestion import _extract_pdf

        with patch.dict("sys.modules", {"pymupdf": None, "fitz": None}):
            with self.assertRaisesRegex(RuntimeError, "PyMuPDF is required"):
                _extract_pdf("missing.pdf", "unused-figures")

    def test_pdf_image_extraction_fast_path_and_cmyk_mask_order(self):
        from utils.slide_ingestion import _extract_pdf_image_bytes

        png_bytes = b"\x89PNG\r\n\x1a\nfixture"

        class FakeDocument:
            def __init__(self, colorspace):
                self.colorspace = colorspace

            def extract_image(self, _xref):
                return {
                    "image": png_bytes,
                    "colorspace": self.colorspace,
                    "cs-name": "DeviceRGB" if self.colorspace == 3 else "DeviceCMYK",
                }

        class ColorSpace:
            def __init__(self, components):
                self.n = components

        class FakePixmap:
            def __init__(self, components, payload):
                self.colorspace = ColorSpace(components)
                self.payload = payload

            def tobytes(self, _format):
                return self.payload

        class FastPathFitz:
            csRGB = object()

            @staticmethod
            def Pixmap(*_args):
                raise AssertionError("RGB fast path must not allocate a Pixmap")

        self.assertEqual(
            _extract_pdf_image_bytes(
                FakeDocument(3), FastPathFitz, (7, 0, 100, 100)
            ),
            png_bytes,
        )

        calls = []
        cmyk = FakePixmap(4, b"cmyk")
        rgb = FakePixmap(3, b"rgb")
        mask = FakePixmap(1, b"mask")
        combined = FakePixmap(3, b"combined")

        class MaskedFitz:
            csRGB = object()

            @staticmethod
            def Pixmap(*args):
                calls.append(args)
                if args == (document, 7):
                    return cmyk
                if args == (MaskedFitz.csRGB, cmyk):
                    return rgb
                if args == (document, 8):
                    return mask
                if args == (rgb, mask):
                    return combined
                raise AssertionError(f"Unexpected Pixmap call: {args}")

        document = FakeDocument(4)
        self.assertEqual(
            _extract_pdf_image_bytes(document, MaskedFitz, (7, 8, 100, 100)),
            b"combined",
        )
        self.assertEqual(calls[0], (document, 7))
        self.assertEqual(calls[1], (MaskedFitz.csRGB, cmyk))
        self.assertEqual(calls[2], (document, 8))
        self.assertEqual(calls[3], (rgb, mask))

    def test_pdf_slide_structure_code_tables_links_and_vector_filter(self):
        from types import SimpleNamespace
        from utils.slide_ingestion import (
            _build_notes,
            _detect_repeated_margin_texts,
            _extract_pdf_annotations,
            _extract_pdf_lines,
            _extract_pdf_links,
            _extract_pdf_tables,
            _has_meaningful_vector_diagram,
            _parse_pdf_page_text,
        )

        class FakePage:
            def __init__(self, page_num):
                self.page_num = page_num

            def get_text(self, mode="text"):
                if mode == "dict":
                    return {
                        "blocks": [
                            {
                                "type": 0,
                                "lines": [
                                    {
                                        "bbox": (40, 15, 300, 30),
                                        "spans": [
                                            {"text": "CS101 Async Systems", "size": 10.0, "font": "Helvetica", "flags": 0}
                                        ],
                                    },
                                    {
                                        "bbox": (40, 60, 500, 92),
                                        "spans": [
                                            {"text": f"Topic {self.page_num}: Structured Concurrency", "size": 24.0, "font": "Helvetica-Bold", "flags": 16}
                                        ],
                                    },
                                    {
                                        "bbox": (40, 130, 500, 146),
                                        "spans": [
                                            {"text": "TaskGroup bounds child lifetimes.", "size": 14.0, "font": "Helvetica", "flags": 0}
                                        ],
                                    },
                                    {
                                        "bbox": (60, 180, 320, 194),
                                        "spans": [
                                            {"text": "async with asyncio.TaskGroup() as tg:", "size": 12.0, "font": "Menlo", "flags": 8}
                                        ],
                                    },
                                    {
                                        "bbox": (88.8, 198, 320, 212),
                                        "spans": [
                                            {"text": "tg.create_task(fetch())", "size": 12.0, "font": "Menlo", "flags": 8}
                                        ],
                                    },
                                    {
                                        "bbox": (700, 415, 770, 430),
                                        "spans": [
                                            {"text": f"{self.page_num} / 3", "size": 9.0, "font": "Helvetica", "flags": 0}
                                        ],
                                    },
                                ],
                            }
                        ]
                    }
                return ""

            def find_tables(self):
                class FakeTable:
                    def extract(self):
                        return [["Tool", "Behavior"], ["gather", "Collects results"], ["TaskGroup", "Cancels siblings"]]

                return SimpleNamespace(tables=[FakeTable()])

            def annots(self):
                return [SimpleNamespace(info={"content": "Remind students about PEP 654."})]

            def get_links(self):
                return [
                    {"uri": "https://peps.python.org/pep-0654/"},
                    {"uri": "https://peps.python.org/pep-0654/"},
                    {"uri": "mailto:ignored@example.com"},
                ]

        pages = [FakePage(1), FakePage(2), FakePage(3)]
        pages_lines = [_extract_pdf_lines(p) for p in pages]
        repeated_margins = _detect_repeated_margin_texts(pages_lines, [450.0, 450.0, 450.0])
        self.assertIn("CS101 Async Systems", repeated_margins)

        title, visible_text, code_blocks = _parse_pdf_page_text(
            pages_lines[0], 450.0, 1, repeated_margins
        )
        self.assertEqual(title, "Topic 1: Structured Concurrency")
        self.assertNotIn("CS101 Async Systems", visible_text)
        self.assertNotIn("1 / 3", visible_text)
        self.assertEqual(len(code_blocks), 1)
        self.assertIn("async with asyncio.TaskGroup() as tg:\n    tg.create_task(fetch())", code_blocks[0])

        tables = _extract_pdf_tables(pages[0])
        self.assertEqual(len(tables), 1)
        self.assertIn("| Tool | Behavior |", tables[0])
        self.assertEqual(_extract_pdf_annotations(pages[0]), "Remind students about PEP 654.")
        self.assertEqual(_extract_pdf_links(pages[0]), ["https://peps.python.org/pep-0654/"])

        notes_md = _build_notes(
            [
                {
                    "slide_number": 1,
                    "source_locator": "page 1",
                    "title": title,
                    "included_in_book": True,
                    "visible_text": visible_text,
                    "code_blocks": code_blocks,
                    "tables": tables,
                    "links": ["https://peps.python.org/pep-0654/"],
                    "speaker_notes": "Remind students about PEP 654.",
                    "figures": [],
                }
            ]
        )
        self.assertIn("### Code snippets", notes_md)
        self.assertIn("    tg.create_task(fetch())", notes_md)
        self.assertIn("### Source links", notes_md)
        self.assertIn("https://peps.python.org/pep-0654/", notes_md)

        multiline_table = SimpleNamespace(
            extract=lambda: [["Column", "Meaning"], ["first\nline", "a | b"]]
        )
        pages[0].find_tables = lambda: SimpleNamespace(tables=[multiline_table])
        escaped_table = _extract_pdf_tables(pages[0])[0]
        self.assertIn("first<br>line", escaped_table)
        self.assertIn(r"a \| b", escaped_table)

        fenced_notes = _build_notes(
            [
                {
                    "slide_number": 1,
                    "source_locator": "page 1",
                    "title": "Fence safety",
                    "included_in_book": True,
                    "visible_text": [],
                    "code_blocks": ["print('```')"],
                    "figures": [],
                }
            ]
        )
        self.assertIn("````\nprint('```')\n````", fenced_notes)

        def make_rect(x0, y0, x1, y1):
            return SimpleNamespace(x0=x0, y0=y0, x1=x1, y1=y1)

        # Decorative theme chrome only -> should NOT trigger vector fallback
        theme_only_drawings = [
            {"rect": make_rect(0, 0, 800, 450), "items": [("re",)]},
            {"rect": make_rect(0, 0, 800, 40), "items": [("re",)] * 5},
            {"rect": make_rect(0, 410, 800, 450), "items": [("re",)] * 5},
            {"rect": make_rect(40, 100, 760, 102), "items": [("l",)]},
        ]
        self.assertFalse(
            _has_meaningful_vector_diagram(theme_only_drawings, 800.0, 450.0, visible_text)
        )

        # Real content-area architecture boxes & connectors -> SHOULD trigger vector fallback
        diagram_drawings = [
            {"rect": make_rect(80, 140, 220, 220), "items": [("re",), ("l",), ("l",)]},
            {"rect": make_rect(260, 140, 400, 220), "items": [("re",), ("l",), ("l",)]},
            {"rect": make_rect(440, 140, 580, 220), "items": [("re",), ("l",), ("l",)]},
            {"rect": make_rect(220, 175, 260, 185), "items": [("l",), ("l",)]},
        ]
        self.assertTrue(
            _has_meaningful_vector_diagram(diagram_drawings, 800.0, 450.0, visible_text)
        )

    def test_slide_ingestion_bounded_zip_reads_and_preflight_validation(self):
        from utils.path_security import (
            sanitize_slide_source_filename,
            validate_slide_output_workspace,
        )
        from utils.slide_ingestion import (
            _bounded_render_scale,
            _read_zip_member_bounded,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "bounded.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("large.bin", b"0123456789")
                archive.writestr("second.bin", b"abcdefghij")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "safety limit"):
                    _read_zip_member_bounded(archive, "large.bin", 5)
            with zipfile.ZipFile(archive_path) as archive, patch(
                "utils.slide_ingestion._MAX_ZIP_UNCOMPRESSED_BYTES", 15
            ):
                self.assertEqual(
                    _read_zip_member_bounded(archive, "large.bin", 10),
                    b"0123456789",
                )
                with self.assertRaisesRegex(ValueError, "cumulative safety limit"):
                    _read_zip_member_bounded(archive, "second.bin", 10)

            scale = _bounded_render_scale(100_000, 100_000)
            self.assertLessEqual(100_000 * scale, 4096)
            self.assertLessEqual((100_000 * scale) ** 2, 16 * 1024 * 1024)
            with self.assertRaisesRegex(ValueError, "too large"):
                _bounded_render_scale(100_000_000_000, 100_000_000_000)

            slide_path = os.path.join(tmpdir, "lecture.pptx")
            with open(slide_path, "wb") as stream:
                stream.write(b"PK\x03\x04")
            output_path = os.path.join(tmpdir, "output")
            with self.assertRaisesRegex(ValueError, "LaTeX template directory"):
                validate_slide_output_workspace(
                    slide_path, os.path.join(tmpdir, "missing-template"), output_path
                )
            self.assertFalse(os.path.exists(output_path))
            self.assertEqual(
                sanitize_slide_source_filename("../week03 async.pptx"),
                "week03_async.pptx",
            )


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

        def fake_agent_with_contents(
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
                            "doi": "10.1145/3600006.3613165",
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

        def fake_agent_text_prompt(prompt, model_name, **kwargs):
            if "Here is the technical book manuscript you are asked to review" in prompt:
                idx = review_call_counter["count"]
                # First 3 calls belong to v0 ensemble, next calls to v1
                rev = mock_review_v0 if idx < 3 else mock_review_v1
                review_call_counter["count"] += 1
                return {"raw_response": json.dumps(rev), "parsed_response": dict(rev)}
            # Meta-review
            rev = mock_review_v0 if review_call_counter["count"] <= 3 else mock_review_v1
            return {"raw_response": json.dumps(rev), "parsed_response": dict(rev)}

        def fake_agent_with_images(prompt, images, model_name, **kwargs):
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
                    "methods.agents.outline_agent.call_agent_with_contents",
                    side_effect=fake_agent_with_contents,
                ),
                patch(
                    "methods.agents.literature_review_agent.call_agent_with_contents",
                    side_effect=fake_agent_with_contents,
                ),
                patch(
                    "utils.llm_backend_utils.call_agent_with_images",
                    side_effect=fake_agent_with_images,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_agent_with_contents",
                    side_effect=fake_agent_with_contents,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_agent_with_images",
                    side_effect=fake_agent_with_images,
                ),
                patch(
                    "autoraters.agent_review.call_agent_with_text_prompt",
                    side_effect=fake_agent_text_prompt,
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
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "build_manifest.json")))
            with open(os.path.join(tmpdir, "build_manifest.json"), "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
            delivered = os.path.join(tmpdir, manifest["delivered_pdf"])
            self.assertTrue(os.path.exists(delivered))
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

        def fake_agent_contents(contents, model_name, result_parsing_func=None, **kwargs):
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
                    "methods.agents.outline_agent.call_agent_with_contents",
                    side_effect=fake_agent_contents,
                ),
                patch(
                    "methods.agents.literature_review_agent.call_agent_with_contents",
                    side_effect=fake_agent_contents,
                ),
                patch(
                    "utils.diagram_plot_utils.call_agent_with_contents",
                    side_effect=fake_agent_contents,
                ),
                patch(
                    "utils.diagram_plot_utils.generate_image_with_agent",
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
                    "utils.llm_backend_utils.call_agent_with_images",
                    return_value={"raw_response": sample_tex, "parsed_response": sample_tex},
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_agent_with_contents",
                    side_effect=fake_agent_contents,
                ),
                patch(
                    "methods.agents.content_refinement_agent.call_agent_with_images",
                    return_value={
                        "raw_response": "{}",
                        "parsed_response": {"figure_and_tables": {}, "other_issues": []},
                    },
                ),
                patch(
                    "autoraters.agent_review.call_agent_with_text_prompt",
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
            with open(os.path.join(tmpdir, "build_manifest.json"), "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
            self.assertTrue(os.path.exists(os.path.join(tmpdir, manifest["delivered_pdf"])))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "final_book.md")))

    def test_antigravity_run_workflow_engine_end_to_end(self):
        import ast
        import asyncio
        from methods.agy_workflow_runner import run_book_orchestra_workflow
        from utils.agent_runtime_utils import (
            set_antigravity_agent_runner,
            call_agent_with_text_prompt,
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
            bridged_res = call_agent_with_text_prompt("ping")
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
                            "doi": "10.1145/3600006.3613165",
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

            self.assertIn(res["status"], ("success", "preview"))
            self.assertEqual(len(phases_visited), 4)
            self.assertTrue(os.path.exists(res["delivered_pdf"]))
            self.assertTrue(os.path.exists(res["build_manifest"]))
            self.assertTrue(os.path.exists(res["final_book_tex"]))
            self.assertTrue(os.path.exists(res["final_book_md"]))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "references.bib")))
            self.assertTrue(os.path.isdir(os.path.join(out_dir, "figures")))
            self.assertGreaterEqual(len(res["chapters"]), 2)
            with open(
                os.path.join(out_dir, "plotting_results.json"),
                "r",
                encoding="utf-8",
            ) as results_file:
                visual_results = json.load(results_file)
            self.assertGreaterEqual(len(visual_results), 1)
            for visual_result in visual_results:
                self.assertIn(visual_result["status"], ("ready", "failed"))
                if visual_result["status"] == "failed":
                    self.assertIsNone(visual_result["name"])
                    self.assertEqual(visual_result["size_bytes"], 0)


class TestReviewFixesAndSecurityHardening(unittest.TestCase):
    def test_finding1_cli_exits_nonzero_on_failure_and_passes_all_args(self):
        import book_writing_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = os.path.join(
                REPO_ROOT, "examples", "distributed_llm_systems_book", "raw_materials"
            )
            out_dir = os.path.join(tmpdir, "out_emit")

            # 1. --emit_workflow_only exits 0 and forwards all CLI options
            rc = book_writing_cli.main(
                [
                    "--raw_materials_dir",
                    raw_dir,
                    "--output_dir",
                    out_dir,
                    "--reflection_model_name",
                    "pro",
                    "--plotting_model_name",
                    "flash",
                    "--image_model_name",
                    "image-generator",
                    "--plotting_max_critic_rounds",
                    "4",
                    "--max_reflections",
                    "5",
                    "--emit_workflow_only",
                ]
            )
            self.assertEqual(rc, 0)
            payload_path = os.path.join(out_dir, "agy_run_workflow_payload.json")
            self.assertTrue(os.path.exists(payload_path))
            with open(payload_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            wf_args = payload["Args"]
            self.assertEqual(wf_args["reflection_model_name"], "pro")
            self.assertEqual(wf_args["plotting_model_name"], "flash")
            self.assertEqual(wf_args["image_model_name"], "image-generator")
            self.assertEqual(wf_args["plotting_max_critic_rounds"], 4)
            self.assertEqual(wf_args["max_reflections"], 5)
            self.assertEqual(wf_args["visual_density"], "high")
            self.assertEqual(payload["ScriptPath"], "workflows/book_orchestra_workflow.py")

            # 2. When no runner is available and --emit_workflow_only is NOT set, CLI exits 1
            out_fail = os.path.join(tmpdir, "out_fail")
            with (
                patch("utils.agent_runtime_utils.get_antigravity_agent_runner", return_value=None),
                patch("utils.agent_runtime_utils.enable_agy_cli_runner", return_value=False),
            ):
                with self.assertRaises(SystemExit) as cm:
                    book_writing_cli.main(
                        [
                            "--raw_materials_dir",
                            raw_dir,
                            "--output_dir",
                            out_fail,
                        ]
                    )
                self.assertEqual(cm.exception.code, 1)

            # 3. When unified engine fails, CLI exits 1
            out_fail2 = os.path.join(tmpdir, "out_fail2")
            with (
                patch("utils.agent_runtime_utils.get_antigravity_agent_runner", return_value=lambda *a, **k: "{}"),
                patch("methods.agy_workflow_runner.run_book_orchestra_sync", side_effect=RuntimeError("boom")),
            ):
                with self.assertRaises(SystemExit) as cm2:
                    book_writing_cli.main(
                        [
                            "--raw_materials_dir",
                            raw_dir,
                            "--output_dir",
                            out_fail2,
                        ]
                    )
                self.assertEqual(cm2.exception.code, 1)

    def test_finding2_plot_code_ast_and_subprocess_sandbox(self):
        from utils.diagram_plot_utils import (
            UnsafePlotCodeError,
            validate_plot_code_ast,
            execute_plot_code_worker,
        )

        # Malicious snippets (including NumPy & Matplotlib file I/O bypasses) must be rejected
        malicious_snippets = [
            "import os\nos.system('id')",
            "import subprocess\nsubprocess.Popen(['ls'])",
            "open('/etc/passwd').read()",
            "__import__('os').listdir('.')",
            "().__class__.__base__.__subclasses__()",
            "import matplotlib.pyplot as plt\nplt.savefig('/tmp/evil.png')",
            "import numpy as np\nnp.fromfile('/etc/passwd', dtype=np.uint8)",
            "import numpy as np\nnp.load('/tmp/evil.npy')",
            "import numpy as np\nnp.save('/tmp/evil.npy', np.zeros(4))",
            "import numpy as np\nnp.savetxt('/tmp/evil.txt', np.zeros(4))",
            "import numpy as np\nnp.zeros(4).tofile('/tmp/evil.bin')",
        ]
        for snippet in malicious_snippets:
            with self.assertRaises(UnsafePlotCodeError, msg=f"Snippet should fail AST check: {snippet}"):
                validate_plot_code_ast(snippet)
            res = execute_plot_code_worker(snippet)
            self.assertIsNone(res)

        # Valid matplotlib snippet should execute cleanly inside isolated subprocess
        valid_snippet = """
import matplotlib.pyplot as plt
import numpy as np

x = np.array([1, 2, 3, 4])
y = np.array([10, 20, 25, 30])
fig, ax = plt.subplots(figsize=(4, 3))
ax.plot(x, y, label='Throughput')
ax.legend()
"""
        tree = validate_plot_code_ast(valid_snippet)
        self.assertIsNotNone(tree)
        res = execute_plot_code_worker(valid_snippet)
        self.assertIsNotNone(res)
        self.assertTrue(len(res) > 100)

    def test_finding3_reference_verifier_cutoff_identifiers_and_bibtex_types(self):
        from utils.reference_verifier import (
            is_reference_before_cutoff,
            has_verifiable_identifier_or_grounding,
            infer_bibtex_entry_type,
            format_bibtex_entry,
            audit_and_clean_citations,
        )

        # 1. Valid conference paper with DOI before cutoff
        c_paper = {
            "citation_key": "Kwon2023PagedAttention",
            "title": "Efficient Memory Management for Large Language Model Serving with PagedAttention",
            "authors": ["Woosuk Kwon", "Zhuohan Li"],
            "year": 2023,
            "venue": "ACM SOSP Conference",
            "doi": "10.1145/3600006.3613165",
            "entry_type": "inproceedings",
        }
        self.assertTrue(is_reference_before_cutoff(c_paper, "2026-11"))
        ok, src = has_verifiable_identifier_or_grounding(c_paper)
        self.assertTrue(ok)
        self.assertEqual(src, "doi:10.1145/3600006.3613165")
        self.assertEqual(infer_bibtex_entry_type(c_paper), "inproceedings")
        bib_paper = format_bibtex_entry(c_paper)
        self.assertIn("@inproceedings{Kwon2023PagedAttention,", bib_paper)
        self.assertIn("doi={10.1145/3600006.3613165}", bib_paper)

        # 2. Valid RFC standard -> @techreport
        c_rfc = {
            "citation_key": "Bishop2022HTTP3",
            "title": "RFC 9114: HTTP/3",
            "authors": ["Mike Bishop"],
            "year": 2022,
            "venue": "IETF",
            "rfc_number": "RFC 9114",
            "url": "https://www.rfc-editor.org/rfc/rfc9114",
        }
        self.assertTrue(is_reference_before_cutoff(c_rfc, "2026-11"))
        self.assertTrue(has_verifiable_identifier_or_grounding(c_rfc)[0])
        self.assertEqual(infer_bibtex_entry_type(c_rfc), "techreport")
        self.assertIn("@techreport{Bishop2022HTTP3,", format_bibtex_entry(c_rfc))

        # 3. Valid engineering book with ISBN -> @book
        c_book = {
            "citation_key": "Kleppmann2017DDIA",
            "title": "Designing Data-Intensive Applications",
            "authors": ["Martin Kleppmann"],
            "year": 2017,
            "venue": "O'Reilly Media",
            "isbn": "978-1449373320",
        }
        self.assertTrue(is_reference_before_cutoff(c_book, "2026-11"))
        self.assertTrue(has_verifiable_identifier_or_grounding(c_book)[0])
        self.assertEqual(infer_bibtex_entry_type(c_book), "book")
        bib_book = format_bibtex_entry(c_book)
        self.assertIn("@book{Kleppmann2017DDIA,", bib_book)
        self.assertIn("isbn={978-1449373320}", bib_book)

        # 4. Future reference after cutoff (2028 > 2026-11) -> MUST be rejected
        c_future = {
            "title": "Future Quantum GPU Interconnects",
            "authors": ["Jane Doe"],
            "year": 2028,
            "doi": "10.1145/9999999.9999999",
        }
        self.assertFalse(is_reference_before_cutoff(c_future, "2026-11"))

        # 5. Unverified hallucinated reference (no DOI/arXiv/RFC/ISBN/URL and not in source notes) -> MUST be rejected
        c_hallucinated = {
            "title": "Totally Hallucinated Paper With No Identifier",
            "authors": ["Imaginary Author"],
            "year": 2024,
            "venue": "FakeConf",
        }
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                c_hallucinated, grounding_text="AtlasGW builds on PagedAttention."
            )[0]
        )

        # 5b. Fake/reserved DOIs, fake/invalid URLs, invalid ISBN checksums, and author-surname-only matches MUST be rejected
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                {"title": "Fake DOI Record", "year": 2024, "doi": "10.9999/not-a-real-record"}
            )[0]
        )
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                {"title": "Fake URL Record", "year": 2024, "url": "https://fake.invalid.example/item"}
            )[0]
        )
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                {"title": "Bad ISBN Checksum Book", "year": 2020, "isbn": "978-1449373329"}
            )[0]
        )
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                {
                    "title": "Completely Unrelated Fake Title",
                    "year": 2023,
                    "doi": "10.1145/3600006.3613165",
                }
            )[0]
        )
        # Author surname ("Kleppmann") appearing in notes without the book's actual title must NOT pass
        self.assertFalse(
            has_verifiable_identifier_or_grounding(
                {
                    "title": "Invented Nonexistent Book By Kleppmann",
                    "authors": ["Martin Kleppmann"],
                    "year": 2024,
                },
                grounding_text="As Kleppmann discussed in distributed systems.",
            )[0]
        )

        # 6. Verify citation auditing strips hallucinated \cite keys (including when allowed set is empty!)
        allowed = {"Kwon2023PagedAttention", "Kleppmann2017DDIA"}
        tex_with_hallucination = (
            r"As shown in \cite{Kwon2023PagedAttention,Hallucinated2099Fake} and \cite{NonExistentKey}."
        )
        cleaned_tex, removed = audit_and_clean_citations(tex_with_hallucination, allowed)
        self.assertIn("Hallucinated2099Fake", removed)
        self.assertIn("NonExistentKey", removed)
        self.assertIn(r"\cite{Kwon2023PagedAttention}", cleaned_tex)
        self.assertNotIn("Hallucinated2099Fake", cleaned_tex)
        self.assertNotIn("NonExistentKey", cleaned_tex)

        cleaned_empty, removed_empty = audit_and_clean_citations(tex_with_hallucination, set())
        self.assertNotIn(r"\cite", cleaned_empty)
        self.assertEqual(len(removed_empty), 3)

    def test_finding4_latex_syntax_validation_and_layout_diagnostics(self):
        from utils.pdf_utils import (
            compile_latex,
            validate_latex_syntax,
            inspect_book_layout_issues,
        )

        # 1. Broken LaTeX with unclosed environment must fail compile_latex and NOT create a fake PDF
        broken_tex = r"""\documentclass{book}
\begin{document}
\chapter{Broken Chapter}
\begin{objectivesbox}
Unclosed environment here!
\end{document}
"""
        is_valid, errors = validate_latex_syntax(broken_tex)
        self.assertFalse(is_valid)
        self.assertTrue(len(errors) > 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tex_path = os.path.join(tmpdir, "broken.tex")
            pdf_path = os.path.join(tmpdir, "broken.pdf")
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(broken_tex)

            ok = compile_latex(tmpdir, pdf_path, "broken")
            self.assertFalse(ok)
            self.assertFalse(os.path.exists(pdf_path))

        # 2. Layout inspector detects >88 char lstlisting lines and wide unwrapped tables
        wide_line = "x = " + ("a" * 100)
        layout_tex = rf"""\documentclass{{book}}
\begin{{document}}
\chapter{{Layout Test}}
\begin{{lstlisting}}[language=Python]
{wide_line}
\end{{lstlisting}}
\begin{{table}}[htbp]
\begin{{tabular}}{{l|l|l|l|l|l}}
A & B & C & D & E & F \\
\end{{tabular}}
\end{{table}}
\end{{document}}
"""
        diag = inspect_book_layout_issues(layout_tex)
        total_issues = len(diag.get("figure_and_tables", {})) + len(diag.get("other_issues", []))
        self.assertGreaterEqual(total_issues, 2)

    def test_finding5_and_6_chapter_cleanup_and_no_hardcoded_user_paths(self):
        from methods.agents.section_writing_agent import SectionWritingAgent

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = SectionWritingAgent.__new__(SectionWritingAgent)
            chapters_dir = os.path.join(tmpdir, "chapters")
            os.makedirs(chapters_dir, exist_ok=True)
            stale_path = os.path.join(chapters_dir, "ch99_stale_old_chapter.tex")
            with open(stale_path, "w", encoding="utf-8") as f:
                f.write("stale")

            agent._export_modular_chapters(
                r"\documentclass{book}\begin{document}\chapter{Fresh Chapter}\label{ch:fresh}Body\end{document}",
                tmpdir,
            )
            files = os.listdir(chapters_dir)
            self.assertNotIn("ch99_stale_old_chapter.tex", files)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith("ch01_fresh_chapter"))

        # Check committed repository sources, not ignored build outputs or local input drafts.
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for rel_p in tracked:
            if not rel_p.endswith((".py", ".md", ".json", ".tex")):
                continue
            if rel_p == "tests/test_book_orchestra.py":
                continue
            full_p = os.path.join(REPO_ROOT, rel_p)
            with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.assertNotIn(
                "/Users/yeongjun",
                content,
                f"Hardcoded user path found in {rel_p}",
            )

    def test_re_review_agy_model_alias_and_multimodal_bridge_and_bibtex_check(self):
        from utils.agent_runtime_utils import (
            resolve_agy_cli_model_name,
            enable_agy_cli_runner,
            get_antigravity_agent_runner,
            get_antigravity_image_runner,
            set_antigravity_agent_runner,
            _extract_prompt_and_media,
            types,
        )
        from utils.pdf_utils import compile_latex, validate_latex_syntax

        # 1. Model alias resolution ('flash', 'pro', 'inherit')
        with patch(
            "utils.agent_runtime_utils.list_agy_cli_models",
            return_value=["gemini-3.8-flash-high", "gemini-3.8-pro-high"],
        ):
            self.assertIsNone(resolve_agy_cli_model_name("inherit"))
            self.assertEqual(
                resolve_agy_cli_model_name("flash"), "gemini-3.8-flash-high"
            )
            self.assertEqual(
                resolve_agy_cli_model_name("pro"), "gemini-3.8-pro-high"
            )

        # When model discovery returns empty (e.g. offline sandbox), short aliases return None
        # instead of passing invalid '--model flash' to agy CLI
        with patch("utils.agent_runtime_utils.list_agy_cli_models", return_value=[]):
            self.assertIsNone(resolve_agy_cli_model_name("flash"))
            self.assertIsNone(resolve_agy_cli_model_name("pro"))

        # 2. enable_agy_cli_runner registers both text and image runners
        set_antigravity_agent_runner(None)
        try:
            with patch(
                "utils.agent_runtime_utils.find_agy_cli_binary",
                return_value="/usr/bin/true",
            ):
                ok = enable_agy_cli_runner()
                self.assertTrue(ok)
                self.assertIsNotNone(get_antigravity_agent_runner())
                self.assertIsNotNone(get_antigravity_image_runner())
        finally:
            set_antigravity_agent_runner(None)

        # 3. PDF binary parts are extracted into readable text instead of '[Attached binary part]'
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_tex = r"\documentclass{book}\begin{document}\chapter{UniquePdfTokenXYZ}\end{document}"
            tex_p = os.path.join(tmpdir, "t.tex")
            pdf_p = os.path.join(tmpdir, "t.pdf")
            with open(tex_p, "w", encoding="utf-8") as f:
                f.write(sample_tex)
            compile_latex(tmpdir, pdf_p, "t")
            with open(pdf_p, "rb") as f:
                pdf_bytes = f.read()

            prompt_text, _, tmp_files = _extract_prompt_and_media(
                [
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    types.Part.from_text(text="Review this book."),
                ]
            )
            for tf in tmp_files:
                if os.path.exists(tf):
                    os.remove(tf)
            self.assertIn("UniquePdfTokenXYZ", prompt_text)

        # 4. Undefined citation key missing from references.bib fails validate_latex_syntax & compile_latex
        with tempfile.TemporaryDirectory() as tmpdir:
            bib_path = os.path.join(tmpdir, "references.bib")
            with open(bib_path, "w", encoding="utf-8") as f:
                f.write(
                    "@book{Kleppmann2017DDIA,\n  title={DDIA},\n  author={Kleppmann},\n  year={2017}\n}\n"
                )
            bad_cite_tex = r"""\documentclass{book}
\begin{document}
\chapter{Test}
Citing an undefined key \cite{MissingKey2099}.
\bibliography{references}
\end{document}
"""
            valid, errs = validate_latex_syntax(bad_cite_tex, cwd=tmpdir)
            self.assertFalse(valid)
            self.assertTrue(any("MissingKey2099" in e for e in errs))


class TestProductionHardeningReview(unittest.TestCase):
    def test_1_latex_command_blocking_and_isolated_sandbox_flags(self):
        from utils.pdf_utils import compile_latex, validate_latex_syntax

        dangerous_snippets = [
            r"\documentclass{book}\begin{document}\input{/etc/hosts}\end{document}",
            r"\documentclass{book}\begin{document}\include{secret}\end{document}",
            r"\documentclass{book}\begin{document}\write18{id}\end{document}",
            r"\documentclass{book}\begin{document}\openin1=/etc/passwd\end{document}",
            r"\documentclass{book}\begin{document}\catcode`\@=11\end{document}",
        ]
        for tex in dangerous_snippets:
            valid, errs = validate_latex_syntax(tex)
            self.assertFalse(valid, f"Dangerous LaTeX should be blocked: {tex}")
            self.assertTrue(any("Forbidden LaTeX command" in e for e in errs))

        # Verify pdflatex is invoked inside isolated tempdir with -no-shell-escape & openin_any=p
        safe_tex = r"\documentclass{book}\begin{document}\chapter{Safe}\end{document}"
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_p = os.path.join(tmpdir, "safe.tex")
            secret_p = os.path.join(tmpdir, "unrelated_host_secret.txt")
            pdf_p = os.path.join(tmpdir, "safe.pdf")
            with open(tex_p, "w", encoding="utf-8") as f:
                f.write(safe_tex)
            with open(secret_p, "w", encoding="utf-8") as f:
                f.write("SECRET")

            recorded_calls = []

            def fake_subproc_run(cmd, cwd=None, env=None, **kw):
                recorded_calls.append({"cmd": cmd, "cwd": cwd, "env": env})
                # Confirm cwd is isolated sandbox (NOT tmpdir) and secret file was NOT copied
                self.assertNotEqual(os.path.abspath(cwd), os.path.abspath(tmpdir))
                self.assertFalse(os.path.exists(os.path.join(cwd, "unrelated_host_secret.txt")))
                with open(os.path.join(cwd, "safe.pdf"), "wb") as pf:
                    pf.write(b"%PDF-1.4\n%%EOF\n")

                class _Proc:
                    returncode = 0
                    stdout = "Output written on safe.pdf"
                    stderr = ""

                return _Proc()

            with (
                patch("utils.pdf_utils.detect_latex_engine", return_value="pdflatex"),
                patch("utils.pdf_utils.subprocess.run", side_effect=fake_subproc_run),
            ):
                ok = compile_latex(tmpdir, pdf_p, "safe", strict_latex=True)
                self.assertTrue(ok)
                self.assertTrue(os.path.exists(pdf_p))
                self.assertGreaterEqual(len(recorded_calls), 1)
                self.assertIn("-no-shell-escape", recorded_calls[0]["cmd"])
                self.assertEqual(recorded_calls[0]["env"].get("openin_any"), "p")
                self.assertEqual(recorded_calls[0]["env"].get("openout_any"), "p")

    def test_2_workspace_overlap_and_marker_protection(self):
        from utils.path_security import prepare_safe_output_workspace

        tpl_dir = os.path.join(REPO_ROOT, "templates", "oreilly_style_book")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Case A: input raw_materials lives inside output/raw_materials/book1 while output_dir=output
            out_dir = os.path.join(tmpdir, "output")
            nested_raw = os.path.join(out_dir, "raw_materials", "book1")
            os.makedirs(nested_raw, exist_ok=True)
            marker_file = os.path.join(nested_raw, "keep_me.md")
            with open(marker_file, "w", encoding="utf-8") as f:
                f.write("important proposal")

            with self.assertRaises(ValueError):
                prepare_safe_output_workspace(nested_raw, tpl_dir, out_dir)
            # Source file MUST remain untouched!
            self.assertTrue(os.path.exists(marker_file))

            # Case B: unmanaged pre-existing output/raw_materials without BookOrchestra marker must NOT be deleted
            separate_raw = os.path.join(tmpdir, "raw_src")
            os.makedirs(separate_raw, exist_ok=True)
            with open(os.path.join(separate_raw, "book_proposal.md"), "w", encoding="utf-8") as f:
                f.write("new proposal")
            unmanaged_target = os.path.join(out_dir, "raw_materials")
            os.makedirs(unmanaged_target, exist_ok=True)
            with open(os.path.join(unmanaged_target, "user_owned.txt"), "w", encoding="utf-8") as f:
                f.write("do not wipe me")

            with self.assertRaises(ValueError):
                prepare_safe_output_workspace(separate_raw, tpl_dir, out_dir)
            self.assertTrue(os.path.exists(os.path.join(unmanaged_target, "user_owned.txt")))

    def test_3_path_traversal_upload_and_image_agent_hardening(self):
        import base64
        from utils.path_security import (
            detect_media_format,
            resolve_safe_child_path,
            sanitize_slug,
            sanitize_upload_filename,
        )

        # Slug sanitization blocks ../../outside
        slug = sanitize_slug("../../etc/passwd")
        self.assertRegex(slug, r"^[a-z0-9_-]+$")
        self.assertNotIn("..", slug)
        self.assertNotIn("/", slug)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                resolve_safe_child_path(tmpdir, "../../escape.jpg")

            safe_name, ext = sanitize_upload_filename("../../../evil/../diagram.png")
            self.assertTrue(safe_name.endswith(".png"))
            self.assertNotIn("..", safe_name)
            self.assertNotIn("/", safe_name)

        # Image magic bytes detection rejects fake HTML/text disguised as PNG
        self.assertIsNone(detect_media_format(b"not really a png file"))
        from utils.agent_runtime_utils import _fallback_diagram_png_base64

        valid_png = base64.b64decode(_fallback_diagram_png_base64(width=16, height=16))
        self.assertEqual(detect_media_format(valid_png), ".png")

    def test_4_ssrf_blocking_private_metadata_and_redirects(self):
        from utils.reference_verifier import (
            _SSRFGuardedRedirectHandler,
            is_safe_authoritative_url,
        )
        import urllib.error
        import urllib.request

        ssrf_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/admin",
            "http://192.168.1.10/internal",
            "http://127.0.0.1:8080/",
            "http://[::1]/",
            "http://localhost/secret",
        ]
        for bad_url in ssrf_targets:
            self.assertFalse(
                is_safe_authoritative_url(bad_url),
                f"SSRF target must be rejected: {bad_url}",
            )

        # Redirect handler blocks redirect to link-local / private IP
        handler = _SSRFGuardedRedirectHandler()
        req = urllib.request.Request("https://arxiv.org/abs/2309.06180")
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/"
            )

    def test_5_preview_vs_strict_publish_and_tectonic_support(self):
        from utils.pdf_utils import compile_latex, get_last_compile_mode

        sample_tex = r"\documentclass{book}\begin{document}\chapter{Hello}\end{document}"
        with tempfile.TemporaryDirectory() as tmpdir:
            tex_p = os.path.join(tmpdir, "book.tex")
            final_p = os.path.join(tmpdir, "final_book.pdf")
            preview_p = os.path.join(tmpdir, "preview_book.pdf")
            with open(tex_p, "w", encoding="utf-8") as f:
                f.write(sample_tex)

            with patch("utils.pdf_utils.detect_latex_engine", return_value=None):
                # Publish mode MUST fail when no LaTeX engine exists
                ok_strict = compile_latex(tmpdir, final_p, "book", strict_latex=True)
                self.assertFalse(ok_strict)
                self.assertEqual(get_last_compile_mode(), "failed")
                self.assertFalse(os.path.exists(final_p))

                # Preview mode writes preview_book.pdf and leaves final_book.pdf absent
                ok_preview = compile_latex(
                    tmpdir, final_p, "book", strict_latex=False, preview_pdf_file=preview_p
                )
                self.assertTrue(ok_preview)
                self.assertEqual(get_last_compile_mode(), "preview")
                from utils.pdf_utils import get_last_preview_backend, _find_preview_font
                self.assertIn(
                    get_last_preview_backend(),
                    ("pandoc+pymupdf-story", "markdown+pymupdf-story", "minimal-pdf"),
                )
                self.assertTrue(os.path.exists(preview_p))
                self.assertFalse(os.path.exists(final_p))

                # Regression check: never pick ancient Gothic script font (NotoSansGothic) for CJK
                chosen_font = _find_preview_font()
                if chosen_font is not None:
                    self.assertNotIn("NotoSansGothic", chosen_font)
                for tpl_name in (
                    "oreilly_style_book",
                    "manning_in_action_book",
                    "systems_handbook",
                ):
                    tpl_path = os.path.join(REPO_ROOT, "templates", tpl_name, "template.tex")
                    with open(tpl_path, "r", encoding="utf-8") as tf:
                        self.assertNotIn("Noto Sans Gothic", tf.read())

    def test_visual_density_coverage_and_user_figure_catalog(self):
        import base64
        from methods.agy_workflow_runner import (
            _catalog_user_figures,
            _ensure_visual_coverage,
        )
        from utils.diagram_plot_utils import _minimal_png_base64

        outline = {
            "plotting_plan": [],
            "section_plan": [
                {
                    "section_title": "Concurrency Mental Models",
                    "learning_objectives": ["Distinguish task and stream lifecycles"],
                    "subsections": [
                        {"subsection_title": "Task creation"},
                        {"subsection_title": "Cancellation and cleanup"},
                    ],
                },
                {"section_title": "Appendix: Commands"},
            ],
        }
        enriched = _ensure_visual_coverage(outline, "high")
        self.assertEqual(len(enriched["plotting_plan"]), 2)
        self.assertTrue(
            all(
                item["chapter_title"] == "Concurrency Mental Models"
                and item["plot_type"] == "diagram"
                and item["pedagogical_purpose"]
                for item in enriched["plotting_plan"]
            )
        )

        with tempfile.TemporaryDirectory() as figures_dir:
            figure_name = "task-lifecycle.png"
            with open(os.path.join(figures_dir, figure_name), "wb") as image_file:
                image_file.write(base64.b64decode(_minimal_png_base64()))
            with open(
                os.path.join(figures_dir, "info.json"), "w", encoding="utf-8"
            ) as metadata_file:
                json.dump(
                    [
                        {
                            "name": figure_name,
                            "caption": "Task lifecycle and cancellation boundaries",
                            "chapter_title": "Concurrency Mental Models",
                        }
                    ],
                    metadata_file,
                )

            catalog = _catalog_user_figures(figures_dir)
            self.assertEqual(len(catalog), 1)
            self.assertEqual(catalog[0]["generated_by"], "user")
            self.assertEqual(catalog[0]["status"], "ready")
            self.assertEqual(
                catalog[0]["caption"], "Task lifecycle and cancellation boundaries"
            )

    def test_6_7_8_9_unified_cli_defaults_scoring_retries_and_dynamic_cutoff(self):
        from book_writing_cli import build_arg_parser
        from methods.agents.content_refinement_agent import ContentRefinementAgent
        from utils.agent_runtime_utils import call_agent_with_text_prompt, set_antigravity_agent_runner

        # Finding 6 & 9: Unified defaults + dynamic null cutoff in workflow.json
        parser = build_arg_parser()
        default_args = parser.parse_args(["--raw_materials_dir", "examples/distributed_llm_systems_book/raw_materials"])
        self.assertTrue(default_args.use_plotting)
        self.assertEqual(default_args.visual_density, "high")
        self.assertEqual(default_args.max_reflections, 2)

        with open(os.path.join(REPO_ROOT, "workflows", "workflow.json"), "r", encoding="utf-8") as wf_f:
            wf_json = json.load(wf_f)
        self.assertIsNone(wf_json["args"]["properties"]["research_cutoff"]["default"])
        self.assertIn("strict_latex", wf_json["args"]["properties"])
        self.assertEqual(
            wf_json["args"]["properties"]["visual_density"]["default"], "high"
        )

        # Finding 7: _extract_scores must NOT double/triple-count legacy alias axes
        cra = ContentRefinementAgent.__new__(ContentRefinementAgent)
        extracted = cra._extract_scores(
            {
                "Technical_Depth": 3,
                "Practical_Utility": 4,
                "Significance": 4,  # legacy alias of Practical_Utility
                "Contribution": 4,  # legacy alias of Practical_Utility
                "Overall": 8,
            }
        )
        self.assertNotIn("Significance", extracted)
        self.assertNotIn("Contribution", extracted)
        self.assertEqual(extracted["Practical_Utility"], 4.0)

        # Finding 8: Retry logic actually retries transient failures up to max_retries
        calls = {"n": 0}

        def flaky_runner(**kw):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient glitch")
            return '```json\n{"ok": true}\n```'

        set_antigravity_agent_runner(flaky_runner)
        try:
            res = call_agent_with_text_prompt("hi", max_retries=3, base_interval_sec=0)
            self.assertEqual(res["parsed_response"], {"ok": True})
            self.assertEqual(calls["n"], 2)
        finally:
            set_antigravity_agent_runner(None)

        # Finding 8: Packaging files use pinned compatible bounds
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, "pyproject.toml")))
        with open(os.path.join(REPO_ROOT, "requirements.txt"), "r", encoding="utf-8") as req_f:
            reqs = req_f.read()
        self.assertNotIn("joblib", reqs)
        self.assertNotIn("pyOpenSSL", reqs)
        self.assertIn(">=", reqs)

    def test_7_image_generator_contract_security_and_plot_fallback(self):
        from utils.diagram_plot_utils import (
            UnsafePlotCodeError,
            validate_plot_code_ast,
        )
        from utils.path_security import extract_safe_generated_image_bytes

        # 1. `from __future__ import annotations` is allowed; other __future__ or os imports blocked
        self.assertIsNotNone(
            validate_plot_code_ast(
                "from __future__ import annotations\nimport matplotlib.pyplot as plt\nplt.plot([1,2],[3,4])\n"
            )
        )
        with self.assertRaises(UnsafePlotCodeError):
            validate_plot_code_ast("from __future__ import braces\n")

        # 2. extract_safe_generated_image_bytes accepts trusted <brain_dir>/<uuid>/<file>.jpg
        #    and rejects arbitrary host paths, nested .system_generated, and escaping symlinks.
        fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + (b"\x00" * 64)
        with tempfile.TemporaryDirectory() as tmpdir:
            figures_dir = os.path.join(tmpdir, "figures")
            brain_dir = os.path.join(tmpdir, "brain")
            conv_uuid = "3203d54d-f661-488c-8a84-269c35f139f9"
            conv_dir = os.path.join(brain_dir, conv_uuid)
            nested_dir = os.path.join(conv_dir, ".system_generated")
            outside_dir = os.path.join(tmpdir, "outside")
            for d in (figures_dir, conv_dir, nested_dir, outside_dir):
                os.makedirs(d, exist_ok=True)

            valid_brain_img = os.path.join(conv_dir, "diagram_12345.jpg")
            with open(valid_brain_img, "wb") as f:
                f.write(fake_jpeg)

            nested_img = os.path.join(nested_dir, "internal.jpg")
            with open(nested_img, "wb") as f:
                f.write(fake_jpeg)

            outside_img = os.path.join(outside_dir, "secret.jpg")
            with open(outside_img, "wb") as f:
                f.write(fake_jpeg)

            symlink_escape = os.path.join(conv_dir, "link.jpg")
            os.symlink(outside_img, symlink_escape)

            # Accepted: dict {"image_path": ...}
            data, fmt = extract_safe_generated_image_bytes(
                {"image_path": valid_brain_img, "error": ""},
                figures_dir=figures_dir,
                brain_dir=brain_dir,
            )
            self.assertEqual(data, fake_jpeg)
            self.assertEqual(fmt, ".jpg")

            # Accepted: prose + backticks (real image-generator output style)
            data2, fmt2 = extract_safe_generated_image_bytes(
                f"Generated diagram saved at `{valid_brain_img}` — crisp white background.",
                figures_dir=figures_dir,
                brain_dir=brain_dir,
            )
            self.assertEqual(data2, fake_jpeg)
            self.assertEqual(fmt2, ".jpg")

            # Rejected: outside host path, nested .system_generated, symlink escape, /etc/hosts
            for untrusted in (outside_img, nested_img, symlink_escape, "/etc/hosts"):
                bad_data, bad_fmt = extract_safe_generated_image_bytes(
                    {"image_path": untrusted},
                    figures_dir=figures_dir,
                    brain_dir=brain_dir,
                )
                self.assertIsNone(bad_data, f"Untrusted path must be rejected: {untrusted}")
                self.assertIsNone(bad_fmt)


if __name__ == "__main__":
    unittest.main()
