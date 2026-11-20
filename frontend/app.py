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

import streamlit as st
import os
import json
import sys
import time
import threading
import subprocess
import shutil
import datetime
import io
import zipfile
from streamlit.runtime.scriptrunner import add_script_run_ctx
import signal
import ctypes

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv():
        return False


from frontend_utils import send_notification_email

load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="BookOrchestra - AI Technical Book Publishing Studio",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


def apply_custom_css():
    st.markdown(
        """
    <style>
        .stApp { background: #f8fafc; color: #0f172a; }
        [data-testid="stSidebar"] { background-color: #f1f5f9; border-right: 1px solid #e2e8f0; }
        .card { background: white; border-radius: 12px; padding: 2rem; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06); margin-bottom: 1.5rem; }
        .timeline-container { padding: 1rem; }
        .timeline-item { border-left: 2px solid #e2e8f0; padding-left: 1.5rem; position: relative; margin-bottom: 1.5rem; }
        .timeline-item.active { border-left-color: #2563eb; }
        .timeline-item.finished { border-left-color: #10b981; }
        .timeline-icon { position: absolute; left: -11px; top: 0; width: 20px; height: 20px; border-radius: 50%; background: white; border: 2px solid #cbd5e1; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: #64748b; }
        .timeline-item.active .timeline-icon { border-color: #b45309; background: #fffbeb; color: #b45309; }
        .timeline-item.finished .timeline-icon { border-color: #10b981; background: #10b981; color: white; }
        .gallery-item { background: white; border-radius: 8px; padding: 1rem; text-align: center; border: 1px solid #e2e8f0; box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05); }
        .stButton > button { background-color: #ecfdf5 !important; color: #065f46 !important; border: 1px solid #a7f3d0 !important; padding: 0.75rem 1.5rem; border-radius: 6px; font-weight: 600; width: 100%; transition: all 0.2s; }
        .stButton > button:hover { transform: translateY(-1px); background-color: #d1fae5 !important; box-shadow: 0 4px 6px -1px rgba(5, 150, 105, 0.2); }
        .stTextInput > div > div > input, .stTextArea > div > div > textarea { background-color: white !important; color: #0f172a !important; border: 1px solid #cbd5e1 !important; }
    </style>
    """,
        unsafe_allow_html=True,
    )


apply_custom_css()


def initialize_session_state():
    default_states = {
        "locked": False,
        "running": False,
        "progress": 0,
        "hide_inputs": False,
        "logs": {
            "Book Blueprint & TOC": "",
            "Technical Ecosystem Research": "",
            "Diagram & Benchmark Plotting": "",
            "Multi-Chapter Authoring": "",
            "Editorial Review Board": "",
        },
        "current_step": 0,
        "figures": [],
        "figure_counter": 0,
        "generated_figures": [],
        "slide_source_bytes": None,
        "slide_source_name": None,
        "run_directory": None,
        "email_confirmed": False,
        "error_message": "",
    }
    for state_key, default_value in default_states.items():
        if state_key not in st.session_state:
            st.session_state[state_key] = default_value


initialize_session_state()


def _load_example_json(filename: str):
    examples_directory = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "examples"
    )
    json_path = os.path.join(examples_directory, filename)
    try:
        with open(json_path, "r", encoding="utf-8") as file_object:
            example_data = json.load(file_object)
        st.session_state.idea_text_main = example_data.get("idea", "")
        st.session_state.log_text_main = example_data.get("log", "")

        figure_path = example_data.get("figure_path")
        figure_caption = example_data.get("figure_caption", "")
        st.session_state.figures = []
        if figure_path:
            full_figure_path = os.path.join(examples_directory, figure_path)
            if os.path.exists(full_figure_path):
                st.session_state.figures = [
                    {
                        "file": full_figure_path,
                        "name": figure_path,
                        "caption": figure_caption,
                    }
                ]

        st.sidebar.success("🎉 Example loaded successfully!")
        time.sleep(0.5)
        st.rerun()
    except Exception as exception_object:
        st.sidebar.error(f"Error loading example: {exception_object}")


def render_sidebar():
    st.sidebar.title("📚 Publishing Studio")

    if st.sidebar.button(
        "Load IT/Engineering Book Example", disabled=st.session_state.locked
    ):
        _load_example_json("it_book_example.json")

    models = ["inherit", "pro", "flash"]
    selected_model = st.sidebar.selectbox(
        "Select Authoring Model Tier", models, disabled=st.session_state.locked
    )

    templates = [
        "oreilly_style_book",
        "manning_in_action_book",
        "systems_handbook",
    ]
    selected_template = st.sidebar.selectbox(
        "Select Book / Publisher Template",
        templates,
        disabled=st.session_state.locked,
    )

    audiences = [
        "Senior / Staff Engineers & Architects",
        "Mid-to-Senior Software Engineers",
        "Platform / SRE / DevOps Engineers",
        "AI / ML Systems Engineers",
    ]
    selected_audience = st.sidebar.selectbox(
        "Target Reader Persona", audiences, disabled=st.session_state.locked
    )

    styles = [
        "Architecture & Production Trade-offs (DDIA / O'Reilly)",
        "Project-Based Hands-On (Manning In Action)",
        "Deep Systems Reference & Handbook (SRE / Addison-Wesley)",
    ]
    selected_style = st.sidebar.selectbox(
        "Pedagogical Style", styles, disabled=st.session_state.locked
    )

    voice_profiles = {
        "Warm exploratory mentor": (
            "Preserve the proposal's voice. Write as a warm, exploratory engineering mentor: "
            "reader-facing, concrete, conversational, and technically precise, with varied pacing."
        ),
        "Concise hands-on guide": (
            "Use concise, approachable workshop prose with short explanations, worked examples, "
            "and minimal jargon while preserving technical rigor."
        ),
        "Deep reference, still readable": (
            "Use an authoritative reference voice, but keep paragraphs focused, transitions natural, "
            "and examples frequent; avoid cold or needlessly dense prose."
        ),
    }
    selected_voice_name = st.sidebar.selectbox(
        "Author Voice",
        list(voice_profiles),
        disabled=st.session_state.locked,
    )
    selected_author_voice = voice_profiles[selected_voice_name]

    plotting_options = ["Yes", "No"]
    enable_plotting = st.sidebar.selectbox(
        "Generate Architecture Diagrams & Benchmark Plots",
        plotting_options,
        index=0,
        disabled=st.session_state.locked,
    )
    visual_density_labels = {
        "High — 2+ teaching visuals per chapter": "high",
        "Balanced — 1+ teaching visual per chapter": "balanced",
        "Minimal — essentials only": "minimal",
    }
    selected_visual_density_label = st.sidebar.selectbox(
        "Visual Density",
        list(visual_density_labels),
        index=0,
        disabled=st.session_state.locked or enable_plotting == "No",
    )
    selected_visual_density = visual_density_labels[selected_visual_density_label]

    strict_latex_mode = st.sidebar.checkbox(
        "Strict LaTeX Publish Mode (require a TeX engine)",
        value=False,
        disabled=st.session_state.locked,
    )
    st.session_state.strict_latex_mode = strict_latex_mode

    st.sidebar.markdown("Notification Email (optional)")
    email_input = st.sidebar.text_input(
        "Email",
        key="email_input",
        label_visibility="collapsed",
        disabled=st.session_state.locked,
    )

    if st.sidebar.button("Confirm Email", disabled=st.session_state.locked):
        if st.session_state.get("email_input"):
            st.session_state.email_confirmed = True
        else:
            st.session_state.email_confirmed = False
            st.sidebar.info("Email notification skipped (optional).")

    if st.session_state.get("email_confirmed"):
        st.sidebar.success("📧 Email confirmed!")

    return (
        selected_model,
        selected_template,
        selected_audience,
        selected_style,
        selected_author_voice,
        enable_plotting,
        selected_visual_density,
        email_input,
    )


(
    selected_model,
    selected_template,
    selected_audience,
    selected_style,
    selected_author_voice,
    enable_plotting,
    selected_visual_density,
    email_input,
) = render_sidebar()


# ------------------------------------------------------------------------
# UI COMPONENT FUNCTIONS
# ------------------------------------------------------------------------


def render_gallery_tab():
    st.subheader("Uploaded Architecture Diagrams & Figures")
    if not st.session_state.figures:
        if not st.session_state.running:
            st.markdown(
                "<p style='color: gray; font-style: italic;'>No diagrams uploaded yet. Add diagrams from the input materials section or enable automatic diagram generation.</p>",
                unsafe_allow_html=True,
            )
        else:
            st.info("ℹ️ No user-supplied figures for this run.")
    else:
        for index, figure in enumerate(st.session_state.figures):
            st.markdown(
                f'<div class="gallery-item" style="margin-bottom: 1rem;"><p style="font-weight: bold;">{figure["name"]}</p></div>',
                unsafe_allow_html=True,
            )
            st.image(figure["file"], width="stretch")
            new_caption = st.text_input(
                "Edit Caption",
                value=figure["caption"],
                key=f"edit_caption_{index}",
                disabled=st.session_state.locked,
            )
            if new_caption != figure["caption"]:
                st.session_state.figures[index]["caption"] = new_caption
            if st.button(
                "🗑️ Delete Image",
                key=f"delete_figure_{index}",
                disabled=st.session_state.locked,
            ):
                st.session_state.figures.pop(index)
                st.rerun()
            st.markdown("<hr>", unsafe_allow_html=True)

    st.subheader("Generated Architecture Diagrams & Benchmark Plots")
    if st.session_state.get("run_directory"):
        results_path = os.path.join(
            st.session_state.run_directory, "plotting_results.json"
        )
        if os.path.exists(results_path):
            try:
                with open(results_path, "r", encoding="utf-8") as file_object:
                    st.session_state.generated_figures = json.load(file_object)
            except Exception:
                pass

    if st.session_state.get("generated_figures"):
        for figure in st.session_state.generated_figures:
            image_path = os.path.join(
                st.session_state.run_directory, figure.get("image_path", "")
            )
            if os.path.exists(image_path):
                st.markdown(f"**{figure.get('title', 'Diagram / Plot')}**")
                st.image(image_path, caption=figure.get("caption", ""))
                st.markdown("<hr>", unsafe_allow_html=True)


def render_json_file(file_name, title, failure_message):
    st.subheader(title)
    if st.session_state.get("run_directory"):
        file_path = os.path.join(st.session_state.run_directory, file_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as file_object:
                    st.json(json.load(file_object))
            except Exception as exception_object:
                st.error(f"Error loading {title.lower()}: {exception_object}")
        elif not st.session_state.running:
            st.info(failure_message)
    else:
        st.info(failure_message)


def render_pdf_and_snapshots(pdf_path, snapshots_directory, download_key):
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as file_object:
            st.download_button(
                label="📥 Download PDF",
                data=file_object.read(),
                file_name=os.path.basename(pdf_path),
                mime="application/pdf",
                key=download_key,
            )

        if os.path.exists(snapshots_directory):
            st.markdown("**Page Previews:**")
            image_files = sorted(
                [
                    file
                    for file in os.listdir(snapshots_directory)
                    if file.endswith(".jpg") or file.endswith(".png")
                ]
            )
            if image_files:
                grid_columns = st.columns(3)
                for index, image_file in enumerate(image_files[:3]):
                    with grid_columns[index]:
                        st.image(
                            os.path.join(snapshots_directory, image_file),
                            width="stretch",
                        )


# ------------------------------------------------------------------------
# BACKGROUND EXECUTION POLLING
# ------------------------------------------------------------------------


def handle_live_execution(
    steps,
    email_input,
    selected_template,
    selected_model,
    selected_audience,
    selected_style,
    selected_author_voice,
    enable_plotting,
    selected_visual_density,
):
    if st.session_state.running:
        if not st.session_state.get("thread_started"):
            has_slide_source = bool(st.session_state.get("saved_slide_bytes"))
            if not has_slide_source and (
                not st.session_state.get("saved_idea")
                or not st.session_state.get("saved_log")
            ):
                st.session_state.error_message = "❌ Please provide both the Book Proposal and Technical Notes."
                st.session_state.running = False
                st.session_state.locked = False
                st.session_state.hide_inputs = False
                st.rerun()
            st.session_state.thread_started = True

            base_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            parent_directory = os.path.dirname(base_directory)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            run_directory = os.path.join(
                parent_directory, "demo_logs", f"book_orchestra_run_{timestamp}"
            )
            st.session_state.run_directory = run_directory
            raw_materials_directory = os.path.join(run_directory, "raw_materials")
            figures_directory = os.path.join(raw_materials_directory, "figures")

            os.makedirs(run_directory, exist_ok=True)
            if not has_slide_source:
                os.makedirs(raw_materials_directory, exist_ok=True)
                os.makedirs(figures_directory, exist_ok=True)

            slide_source_path = None
            if has_slide_source:
                slide_extension = os.path.splitext(
                    st.session_state.get("saved_slide_name") or "lecture.pptx"
                )[1].lower()
                if slide_extension not in (".pptx", ".pdf"):
                    slide_extension = ".pptx"
                slide_source_path = os.path.join(
                    run_directory, f"uploaded_lecture{slide_extension}"
                )

            command = [
                sys.executable,
                "-u",
                os.path.join(base_directory, "book_writing_cli.py"),
                "--output_dir",
                run_directory,
                "--latex_template_dir",
                os.path.join(base_directory, "templates", selected_template),
                "--writer_model_name",
                selected_model,
                "--reflection_model_name",
                selected_model,
                "--proposal_filename",
                "book_proposal.md",
                "--technical_notes_filename",
                "technical_notes.md",
                "--target_audience",
                selected_audience,
                "--book_style",
                selected_style,
                "--author_voice",
                selected_author_voice,
                "--visual_density",
                selected_visual_density,
            ]
            if slide_source_path:
                command.extend(["--slides_file", slide_source_path])
            else:
                command.extend(["--raw_materials_dir", raw_materials_directory])
            if enable_plotting == "Yes":
                command.extend(
                    [
                        "--use_plotting",
                        "True",
                        "--plotting_model_name",
                        selected_model,
                        "--image_model_name",
                        "image-generator",
                    ]
                )
            else:
                command.extend(["--use_plotting", "False"])
            if st.session_state.get("strict_latex_mode", False):
                command.append("--strict_latex")

            def run_pipeline(
                execution_command,
                working_directory,
                steps_list,
                recipient_email,
                idea_content,
                log_content,
                figures_list,
                slide_bytes,
                slide_path,
            ):
                try:
                    preexec_fn = None
                    if sys.platform.startswith("linux"):
                        libc = ctypes.CDLL(None)
                        process_set_parent_death_signal = 1

                        def set_death_signal():
                            libc.prctl(process_set_parent_death_signal, signal.SIGKILL)

                        preexec_fn = set_death_signal

                    os.makedirs(run_directory, exist_ok=True)
                    raw_materials_dir = os.path.join(run_directory, "raw_materials")
                    figures_dir = os.path.join(raw_materials_dir, "figures")

                    if slide_bytes and slide_path:
                        with open(slide_path, "wb") as file_object:
                            file_object.write(slide_bytes)
                    else:
                        os.makedirs(raw_materials_dir, exist_ok=True)
                        os.makedirs(figures_dir, exist_ok=True)
                        with open(
                            os.path.join(raw_materials_dir, "book_proposal.md"),
                            "w",
                            encoding="utf-8",
                        ) as file_object:
                            file_object.write(idea_content)

                        with open(
                            os.path.join(raw_materials_dir, "technical_notes.md"),
                            "w",
                            encoding="utf-8",
                        ) as file_object:
                            file_object.write(log_content)

                    from utils.path_security import (
                        detect_media_format,
                        resolve_safe_child_path,
                        sanitize_upload_filename,
                    )

                    for figure in figures_list if not slide_bytes else []:
                        if isinstance(figure["file"], str):
                            with open(figure["file"], "rb") as src_f:
                                raw_fig_bytes = src_f.read()
                        else:
                            raw_fig_bytes = figure["file"].getvalue()

                        detected_ext = detect_media_format(raw_fig_bytes)
                        if detected_ext is None:
                            raise ValueError(
                                f"Uploaded file '{figure.get('name')}' failed image/PDF content validation."
                            )
                        safe_fig_name, _ = sanitize_upload_filename(
                            figure.get("name", f"figure{detected_ext}"),
                            fallback_ext=detected_ext,
                        )
                        figure_path = str(
                            resolve_safe_child_path(figures_dir, safe_fig_name)
                        )
                        with open(figure_path, "wb") as file_object:
                            file_object.write(raw_fig_bytes)

                    process = subprocess.Popen(
                        execution_command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=working_directory,
                        preexec_fn=preexec_fn,
                    )

                    current_step_name = steps_list[0]
                    st.session_state.current_step = 0

                    while True:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            if (
                                "Processing figure:" in line
                                or (
                                    line.strip().startswith("[")
                                    and not line.strip().startswith("[DEBUG]")
                                )
                                or "Figure processing complete" in line
                                or "Saved figures/" in line
                                or "[DEBUG] Starting run_plotting_agent" in line
                            ):
                                st.session_state.logs[
                                    "Diagram & Benchmark Plotting"
                                ] += line
                            else:
                                if (
                                    "Starting Technical Ecosystem & Literature Agent"
                                    in line
                                    or "Starting Fast Hybrid Literature Agent" in line
                                ):
                                    st.session_state.current_step = 1
                                    current_step_name = steps_list[1]
                                elif "Agent SectionWritingAgent starting" in line:
                                    st.session_state.current_step = 2
                                    current_step_name = steps_list[2]
                                elif (
                                    "=== Initial Baseline Review ===" in line
                                    or "Starting dedicated formatting loop" in line
                                ):
                                    st.session_state.current_step = 3
                                    current_step_name = steps_list[3]

                                st.session_state.logs[current_step_name] += line

                    run_dir = st.session_state.run_directory
                    final_pdf_path = os.path.join(run_dir, "final_book.pdf")
                    preview_pdf_path = os.path.join(run_dir, "preview_book.pdf")
                    delivered_pdf_exists = os.path.exists(final_pdf_path) or (
                        not st.session_state.get("strict_latex_mode", False)
                        and os.path.exists(preview_pdf_path)
                    )
                    if process.poll() == 0 and delivered_pdf_exists:
                        st.session_state.current_step = len(steps_list)
                        if recipient_email:
                            attachments = []
                            for fname in ("final_book.pdf", "preview_book.pdf", "final_book.md", "build_manifest.json"):
                                fpath = os.path.join(run_dir, fname)
                                if os.path.exists(fpath):
                                    attachments.append(fpath)

                            latex_writeup = os.path.join(run_dir, "latex_writeup")
                            if os.path.exists(latex_writeup):
                                try:
                                    zip_output_base = os.path.join(
                                        run_dir, "latex_source"
                                    )
                                    shutil.make_archive(
                                        zip_output_base, "zip", latex_writeup
                                    )
                                    if os.path.exists(zip_output_base + ".zip"):
                                        attachments.append(zip_output_base + ".zip")
                                except Exception as e:
                                    st.session_state.logs[
                                        "Editorial Review Board"
                                    ] += f"\n❌ Error preparing latex zip: {e}"

                            send_notification_email(
                                recipient_email,
                                "Generated Technical Book",
                                attachments=attachments,
                            )
                    else:
                        st.session_state.logs[
                            current_step_name
                        ] += f"\n❌ Book generation exited with code {process.poll()} (final_book.pdf exists: {os.path.exists(final_pdf_path)}).\n"
                except Exception as exception_object:
                    st.session_state.logs[
                        "Editorial Review Board"
                    ] += f"\n❌ Error in thread: {exception_object}"
                finally:
                    st.session_state.running = False
                    st.session_state.thread_started = False

            worker_thread = threading.Thread(
                target=run_pipeline,
                args=(
                    command,
                    base_directory,
                    steps,
                    email_input,
                    st.session_state.saved_idea,
                    st.session_state.saved_log,
                    list(st.session_state.figures),
                    st.session_state.get("saved_slide_bytes"),
                    slide_source_path,
                ),
            )
            add_script_run_ctx(worker_thread)
            worker_thread.start()
            st.rerun()

        time.sleep(2)
        st.rerun()


# ------------------------------------------------------------------------
# MAIN PAGE ROUTING
# ------------------------------------------------------------------------

st.title("📚 BookOrchestra — Technical & Engineering Book Publishing Studio")
st.markdown(
    "Multi-agent orchestration tailored for **Senior Software/Systems Engineers** and **Technical Publishers** (O'Reilly, Manning, Addison-Wesley standards)."
)

steps = [
    "Book Blueprint & TOC",
    "Technical Ecosystem Research",
    "Multi-Chapter Authoring",
    "Editorial Review Board",
]

if not st.session_state.get("hide_inputs"):
    input_container = st.container()
    with input_container:
        if st.session_state.get("error_message"):
            st.error(st.session_state.error_message)
            st.session_state.error_message = ""

        st.header("📝 Book Source Materials")
        st.subheader("🎞️ Lecture Slides (Optional Alternative Input)")
        st.caption(
            "Upload a PPTX or slide PDF to extract slide text, speaker notes, tables, "
            "and reusable visuals. When supplied, it replaces the proposal and notes inputs below."
        )
        uploaded_slides = st.file_uploader(
            "Upload lecture slides",
            type=["pptx", "pdf"],
            disabled=st.session_state.locked,
            key="slide_uploader_main",
        )
        if uploaded_slides:
            st.session_state.slide_source_bytes = uploaded_slides.getvalue()
            st.session_state.slide_source_name = uploaded_slides.name
            st.success(
                "Lecture slides selected. BookOrchestra will preserve the source and "
                "create grounded proposal/notes automatically."
            )
        else:
            st.session_state.slide_source_bytes = None
            st.session_state.slide_source_name = None

        column_1, column_2 = st.columns(2)

        with column_1:
            st.subheader("📘 Book Proposal & Architecture Vision")
            idea_input_type = st.radio(
                "Proposal Input Type",
                ["Text", "Upload"],
                disabled=st.session_state.locked,
                key="idea_input_type_main",
            )
            if idea_input_type == "Text":
                st.markdown(
                    "Enter book proposal, audience, and chapter scope <span style='color:red'>*</span>",
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "Book Proposal",
                    key="idea_text_main",
                    label_visibility="collapsed",
                    disabled=st.session_state.locked,
                    height=300,
                )
            else:
                st.markdown(
                    "Upload Proposal file (.md/.txt) <span style='color:red'>*</span>",
                    unsafe_allow_html=True,
                )
                uploaded_idea = st.file_uploader(
                    "Upload Proposal",
                    type=["md", "txt"],
                    label_visibility="collapsed",
                    disabled=st.session_state.locked,
                    key="idea_uploader_main",
                )
                if uploaded_idea:
                    st.session_state.idea_text_main = uploaded_idea.read().decode(
                        "utf-8"
                    )

        with column_2:
            st.subheader("🛠️ Technical Notes, Benchmarks & Code Specs")
            log_input_type = st.radio(
                "Technical Notes Input Type",
                ["Text", "Upload"],
                disabled=st.session_state.locked,
                key="log_input_type_main",
            )
            if log_input_type == "Text":
                st.markdown(
                    "Enter capacity math, code specs, benchmarks, and post-mortems <span style='color:red'>*</span>",
                    unsafe_allow_html=True,
                )
                st.text_area(
                    "Technical Notes",
                    key="log_text_main",
                    label_visibility="collapsed",
                    disabled=st.session_state.locked,
                    height=300,
                )
            else:
                st.markdown(
                    "Upload Technical Notes file (.md/.txt) <span style='color:red'>*</span>",
                    unsafe_allow_html=True,
                )
                uploaded_log = st.file_uploader(
                    "Upload Technical Notes",
                    type=["md", "txt"],
                    label_visibility="collapsed",
                    disabled=st.session_state.locked,
                    key="log_uploader_main",
                )
                if uploaded_log:
                    st.session_state.log_text_main = uploaded_log.read().decode("utf-8")

        st.subheader("🖼️ Upload Architecture Diagrams or Plots (Optional)")
        if st.session_state.get("slide_source_bytes"):
            st.info(
                "The slide deck's reusable visuals will be extracted automatically. "
                "Separate figure uploads are available with Proposal & Notes input."
            )
        figure_column_1, figure_column_2 = st.columns([1, 2])
        with figure_column_1:
            figure_file = st.file_uploader(
                "Upload figure",
                type=["png", "jpg", "jpeg", "pdf"],
                disabled=st.session_state.locked,
                key="figure_uploader_main",
            )
        with figure_column_2:
            figure_caption = st.text_input(
                "Diagram / Plot Caption",
                disabled=st.session_state.locked,
                key="figure_caption_main",
            )

            button_column, _ = st.columns([1, 2])
            with button_column:
                if st.button(
                    "Add Figure",
                    disabled=st.session_state.locked
                    or bool(st.session_state.get("slide_source_bytes"))
                    or not figure_file
                    or not figure_caption,
                ):
                    st.session_state.figures.append(
                        {
                            "file": figure_file,
                            "name": figure_file.name,
                            "caption": figure_caption,
                        }
                    )
                    st.session_state.figure_counter += 1
                    st.success("🎉 Figure added! Check the Gallery tab.")
                    time.sleep(1)
                    st.rerun()

        if st.button(
            "🚀 Start Book Publishing Pipeline",
            type="primary",
            disabled=st.session_state.locked,
        ):
            st.session_state.saved_idea = st.session_state.get("idea_text_main")
            st.session_state.saved_log = st.session_state.get("log_text_main")
            st.session_state.saved_slide_bytes = st.session_state.get(
                "slide_source_bytes"
            )
            st.session_state.saved_slide_name = st.session_state.get(
                "slide_source_name"
            )
            st.session_state.locked = True
            st.session_state.running = True
            st.session_state.hide_inputs = True
            st.rerun()

else:
    if st.session_state.get("running") or st.session_state.get("thread_started"):
        st.info(
            "🚀 Technical Book Publishing Pipeline running! Drafting blueprint, researching references, authoring chapters, and running the 3-Persona Editorial Review Board..."
        )
    elif (
        not st.session_state.get("running")
        and st.session_state.get("thread_started", False) is False
    ):
        if st.session_state.get("current_step") == len(steps):
            st.success("🎉 Technical Book Manuscript Complete!")
        elif st.session_state.get("current_step", 0) < len(steps) and any(
            st.session_state.logs.values()
        ):
            st.error("❌ Book generation failed or stopped unexpectedly. Check logs.")

    tabs_list = [
        "📊 Publishing Pipeline",
        "📥 Proposal & Notes",
        "🖼️ Diagrams & Plots",
        "📑 Book Blueprint & TOC",
        "📚 Reference Map",
        "📈 Editorial Review Board",
    ]
    tab_timeline, tab_inputs, tab_gallery, tab_outline, tab_citation, tab_refinement = (
        st.tabs(tabs_list)
    )

    with tab_timeline:
        st.subheader("Publishing Progress")
        for index, step in enumerate(steps):
            state_class, icon = (
                ("finished", "✓")
                if index < st.session_state.current_step
                else (
                    ("active", "⏳")
                    if index == st.session_state.current_step
                    and st.session_state.running
                    else ("", "○")
                )
            )

            if step == "Technical Ecosystem Research" and enable_plotting == "Yes":
                st.markdown(
                    f'<div class="timeline-item {state_class}"><div class="timeline-icon">{icon}</div><p style="font-weight: bold; margin-bottom: 0.25rem;">Technical Ecosystem Research</p></div>',
                    unsafe_allow_html=True,
                )
                with st.expander(
                    "View Research Logs",
                    expanded=(index == st.session_state.current_step),
                ):
                    if st.session_state.logs["Technical Ecosystem Research"]:
                        st.code(st.session_state.logs["Technical Ecosystem Research"])
                    else:
                        st.text("No logs yet.")

                st.markdown(
                    f'<div class="timeline-item {state_class}"><div class="timeline-icon">{icon}</div><p style="font-weight: bold; margin-bottom: 0.25rem;">Diagram & Benchmark Plotting</p></div>',
                    unsafe_allow_html=True,
                )
                with st.expander(
                    "View Diagram & Plotting Logs",
                    expanded=(index == st.session_state.current_step),
                ):
                    if st.session_state.logs["Diagram & Benchmark Plotting"]:
                        st.code(st.session_state.logs["Diagram & Benchmark Plotting"])
                    else:
                        st.text("No logs yet.")
            else:
                st.markdown(
                    f'<div class="timeline-item {state_class}"><div class="timeline-icon">{icon}</div><p style="font-weight: bold; margin-bottom: 0.25rem;">{step}</p></div>',
                    unsafe_allow_html=True,
                )
                with st.expander(
                    f"View Logs for {step}",
                    expanded=(index == st.session_state.current_step),
                ):
                    if st.session_state.logs[step]:
                        st.code(st.session_state.logs[step])
                    else:
                        st.text("No logs yet.")

        if not st.session_state.running and st.session_state.get("run_directory"):
            pdf_path = os.path.join(st.session_state.run_directory, "final_book.pdf")
            preview_path = os.path.join(st.session_state.run_directory, "preview_book.pdf")
            manifest_path = os.path.join(st.session_state.run_directory, "build_manifest.json")
            md_path = os.path.join(st.session_state.run_directory, "final_book.md")
            latex_directory = os.path.join(
                st.session_state.run_directory, "latex_writeup"
            )

            render_mode = "unknown"
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        render_mode = json.load(mf).get("render_mode", "unknown")
                except Exception:
                    pass

            if render_mode == "preview" or (not os.path.exists(pdf_path) and os.path.exists(preview_path)):
                st.warning(
                    "⚠️ No LaTeX engine (`pdflatex`/`tectonic`) was available; generated a **preview PDF** (`preview_book.pdf`) instead of a publication-typeset `final_book.pdf`."
                )

            active_pdf_path = pdf_path if os.path.exists(pdf_path) else preview_path
            active_pdf_name = (
                "final_book.pdf" if os.path.exists(pdf_path) else "preview_book.pdf"
            )
            active_pdf_label = (
                "📄 Download Final Book PDF"
                if os.path.exists(pdf_path)
                else "📄 Download Preview PDF (Non-LaTeX)"
            )

            if (
                os.path.exists(active_pdf_path)
                or os.path.exists(md_path)
                or os.path.exists(latex_directory)
            ):
                st.markdown("### 📥 Download Published Outputs")
                col_dl_1, col_dl_2, col_dl_3 = st.columns(3)

                if os.path.exists(active_pdf_path):
                    with open(active_pdf_path, "rb") as file_object:
                        with col_dl_1:
                            st.download_button(
                                label=active_pdf_label,
                                data=file_object.read(),
                                file_name=active_pdf_name,
                                mime="application/pdf",
                                key="download_final_pdf_tab",
                            )

                if os.path.exists(latex_directory):
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(
                        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
                    ) as zip_file:
                        for root, directories, files in os.walk(latex_directory):
                            for file in files:
                                file_path = os.path.join(root, file)
                                archive_name = os.path.relpath(
                                    file_path, latex_directory
                                )
                                zip_file.write(file_path, archive_name)

                    with col_dl_2:
                        st.download_button(
                            label="📦 Download LaTeX & Chapters (.zip)",
                            data=zip_buffer.getvalue(),
                            file_name="book_latex_source.zip",
                            mime="application/zip",
                            key="download_final_latex_tab",
                        )

                if os.path.exists(md_path):
                    with open(md_path, "r", encoding="utf-8") as file_object:
                        with col_dl_3:
                            st.download_button(
                                label="📝 Download Markdown Book (.md)",
                                data=file_object.read(),
                                file_name="final_book.md",
                                mime="text/markdown",
                                key="download_final_md_tab",
                            )

    with tab_inputs:
        st.header("📥 Book Proposal & Technical Notes")
        st.markdown(
            "<h3 style='background-color: #e0f2fe; padding: 6px; border-radius: 4px;'>Book Proposal</h3>",
            unsafe_allow_html=True,
        )
        st.markdown(
            st.session_state.get(
                "saved_idea", "*(No proposal provided or process not started)*"
            )
        )
        st.markdown(
            "<h3 style='background-color: #fef3c7; padding: 6px; border-radius: 4px;'>Technical Notes & Benchmarks</h3>",
            unsafe_allow_html=True,
        )
        st.markdown(
            st.session_state.get(
                "saved_log", "*(No technical notes provided or process not started)*"
            )
        )

    with tab_gallery:
        render_gallery_tab()

    with tab_outline:
        render_json_file(
            "outline.json",
            "Book Blueprint & Table of Contents",
            "No blueprint generated yet.",
        )

    with tab_citation:
        render_json_file(
            os.path.join("literature_agent_output", "citation_map.json"),
            "Verified Technical Bibliography",
            "No bibliography map generated yet.",
        )

    with tab_refinement:
        st.header("Editorial Review Board & Typesetting Refinement")
        if not st.session_state.get("run_directory"):
            st.info("No editorial refinement progress available yet.")
        else:
            worklog_path = os.path.join(
                st.session_state.run_directory,
                "content_refinement_workdir",
                "content_refinement_worklog.json",
            )

            st.markdown("### Editorial & Technical Review Iterations")
            columns = st.columns(2)

            initial_pdf_path = os.path.join(
                st.session_state.run_directory,
                "content_refinement_workdir",
                "initial_draft.pdf",
            )
            if os.path.exists(initial_pdf_path):
                with columns[0]:
                    with st.expander("Manuscript Draft V0 (Initial)"):
                        st.write("Initial compiled book manuscript.")
                        render_pdf_and_snapshots(
                            initial_pdf_path,
                            os.path.join(
                                st.session_state.run_directory,
                                "content_refinement_workdir",
                                "pdf_screenshots",
                                "content_refinement",
                                "v0",
                            ),
                            "download_content_0",
                        )

            if os.path.exists(worklog_path):
                try:
                    with open(worklog_path, "r", encoding="utf-8") as file_object:
                        worklog_data = json.load(file_object)

                    content_versions = list(worklog_data.keys())

                    for index, version_key in enumerate(content_versions):
                        entry = worklog_data[version_key]
                        with columns[(index + 1) % 2]:
                            with st.expander(
                                f"Editorial Revision {version_key.upper()}"
                            ):
                                st.write(f"**Outcome:** {entry.get('outcome')}")
                                st.write(f"**Total Gain:** {entry.get('total_gain')}")
                                st.write(f"**Total Drop:** {entry.get('total_drop')}")

                                if "scores_before" in entry and "scores_after" in entry:
                                    st.markdown("**Editorial Review Board Scores:**")
                                    for axis in [
                                        "Overall",
                                        "Technical_Depth",
                                        "Practical_Utility",
                                        "Code_and_Architecture_Quality",
                                        "Pedagogical_Clarity",
                                        "Structural_Coherence",
                                        "Editorial_Polish",
                                    ]:
                                        if (
                                            axis in entry["scores_before"]
                                            or axis in entry["scores_after"]
                                        ):
                                            st.write(
                                                f"- {axis.replace('_', ' ')}: {entry['scores_before'].get(axis, 0)} -> {entry['scores_after'].get(axis, 0)}"
                                            )

                                render_pdf_and_snapshots(
                                    os.path.join(
                                        st.session_state.run_directory,
                                        "content_refinement_workdir",
                                        f"refined_book_{version_key}.pdf",
                                    ),
                                    os.path.join(
                                        st.session_state.run_directory,
                                        "content_refinement_workdir",
                                        "pdf_screenshots",
                                        "content_refinement",
                                        version_key,
                                    ),
                                    f"download_content_{version_key}",
                                )

                    final_selected_version = next(
                        (
                            f"V{entry['round']}"
                            for entry in worklog_data.values()
                            if entry.get("outcome")
                            in [
                                "ACCEPTED_SCORE_INCREASE",
                                "ACCEPTED_NEUTRAL_IMPROVEMENT",
                            ]
                        ),
                        "V0 (Initial)",
                    )

                    format_worklog_path = os.path.join(
                        st.session_state.run_directory,
                        "content_refinement_workdir",
                        "format_refinement_worklog.json",
                    )
                    if os.path.exists(format_worklog_path):
                        st.info(
                            f"💡 `Editorial Revision {final_selected_version}` selected, proceeding to production typesetting QA..."
                        )

                        st.markdown("### Production Typesetting QA")
                        format_iterations = []
                        with open(
                            format_worklog_path, "r", encoding="utf-8"
                        ) as file_object:
                            for version_key, entry in json.load(file_object).items():
                                format_iterations.append(
                                    {
                                        "iteration": int(version_key[1:]),
                                        "feedback": entry.get(
                                            "formatting_feedback", {}
                                        ),
                                        "pdf_path": os.path.join(
                                            st.session_state.run_directory,
                                            "content_refinement_workdir",
                                            f"formatted_candidate_{version_key}.pdf",
                                        ),
                                        "screenshots_directory": os.path.join(
                                            st.session_state.run_directory,
                                            "content_refinement_workdir",
                                            "pdf_screenshots",
                                            "format_refinement",
                                            version_key,
                                        ),
                                    }
                                )

                        format_columns = st.columns(2)
                        for index, entry in enumerate(format_iterations):
                            with format_columns[index % 2]:
                                with st.expander(
                                    f"Typesetting QA V{entry['iteration']}"
                                ):
                                    if "figure_and_tables" in entry["feedback"]:
                                        st.markdown("**Diagram, Code & Table QA:**")
                                        st.json(entry["feedback"]["figure_and_tables"])
                                    render_pdf_and_snapshots(
                                        entry["pdf_path"],
                                        entry["screenshots_directory"],
                                        f"download_format_{entry['iteration']}",
                                    )
                except Exception as exception_object:
                    st.error(f"Error processing refinement data: {exception_object}")
            elif not st.session_state.running and not os.path.exists(initial_pdf_path):
                st.info("No editorial refinement progress available yet.")

    handle_live_execution(
        steps,
        email_input,
        selected_template,
        selected_model,
        selected_audience,
        selected_style,
        selected_author_voice,
        enable_plotting,
        selected_visual_density,
    )
