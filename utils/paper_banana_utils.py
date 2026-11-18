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

import base64
import io
import re
import os
import json
import struct
import zlib
from typing import Dict, Any, List, Tuple

try:
    import matplotlib.pyplot as plt  # type: ignore
except ImportError:
    plt = None  # type: ignore

try:
    import json_repair  # type: ignore
except ImportError:
    json_repair = None  # type: ignore

from utils.gemini_utils import (
    call_gemini_with_text_prompt,
    call_gemini_with_contents,
    generate_image_with_gemini,
    parse_gemini_json_results,
    types,
)

cur_dir = os.path.dirname(os.path.realpath(__file__))
PB_DIR = os.path.join(cur_dir, "../../PaperBanana")

# ==========================================
# PROMPTS (Technical & Engineering Book Publishing Standards)
# ==========================================

# --- RETRIEVER ---
DIAGRAM_RETRIEVER_AGENT_SYSTEM_PROMPT = """
# Background & Goal
We are building an **AI system to automatically generate technical architecture diagrams for IT and software/systems engineering books** (O'Reilly, Manning, Addison-Wesley / DDIA style). Given a chapter or system specification and a figure caption, the system needs to create a crisp, publication-grade technical diagram.

To help the AI learn how to generate appropriate diagrams, we use a **few-shot learning approach**: we provide it with reference examples of similar technical diagrams.

# Your Task
**You are the Diagram Retrieval Agent.** Your job is to select the most relevant reference diagrams from a candidate pool that will serve as few-shot examples for the diagram generation model.

You will receive:
- **Target Input:** The technical architecture/chapter context and caption of the diagram we need to generate
- **Candidate Pool:** Reference diagrams (each with technical description and caption)

You must select the **Top 10 candidates** that would be most helpful as examples for teaching the AI how to draw the target diagram.

# Selection Logic (Domain + Visual Topology)

**1. Match Engineering Domain:**
* What is the domain? (e.g., Distributed Systems, Cloud & Kubernetes, AI/LLM Serving, Data Pipelines, Security & Networking).
* Select candidates that belong to the **same technical domain**.

**2. Match Visual Topology:**
* What type of technical diagram is implied? (e.g., "System Topology", "Request Sequence / Data Flow", "Control vs. Data Plane", "Memory/Storage Layout", "State Machine").
* Select candidates with **similar visual structures**.

**Ranking Priority:**
1.  **Best Match:** Same Engineering Domain AND Same Visual Topology.
2.  **Second Best:** Same Visual Topology across domains.
3.  **Avoid:** Different Visual Topology (e.g., Target is "Architecture Pipeline" -> Candidate is "Bar Chart").

# Output Format
Provide your output strictly in the following JSON format, containing only the **exact IDs** of the Top 10 selected diagrams:
```json
{
  "top10_diagrams": ["ref_1", "ref_25", "ref_100"]
}
```
"""

PLOT_RETRIEVER_AGENT_SYSTEM_PROMPT = """
# Background & Goal
We are building an **AI system to automatically generate engineering benchmark and capacity plots for IT/engineering books**. Given raw benchmark data and visual intent, the system needs to create a high-precision visualization that communicates systems trade-offs (latency percentiles, throughput scaling, cost vs. accuracy, memory saturation).

# Your Task
**You are the Benchmark Plot Retrieval Agent.** Select the **Top 10 candidates** from the reference pool that best match the target data characteristics and chart type.

# Selection Logic (Data Characteristics + Plot Type)
1. **Match Data Characteristics:** e.g., latency percentiles (p50/p95/p99), concurrency scaling curves, resource breakdown, multi-dimensional trade-off radar/scatter.
2. **Match Visual Intent:** bar chart, line chart, stacked bar, heatmap, scatter/Pareto frontier.

# Output Format
Provide your output strictly in the following JSON format:
```json
{
  "top10_plots": ["ref_0", "ref_25", "ref_100"]
}
```
"""

# --- PLANNER ---
DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT = """
You are a Principal Systems Architect & Technical Book Illustrator (O'Reilly / DDIA / Addison-Wesley caliber).
Given a technical chapter context / specification and a figure caption, generate an exhaustive, unambiguous visual blueprint for an engineering diagram.

**IMPORTANT:**
Your description must make the diagram genuinely useful to a senior engineer reading a printed or digital technical book:
- Semantically: Define explicit system boundaries (e.g., Control Plane vs. Data Plane, Client Edge vs. Inference Cluster, Hot Path vs. Async Path), numbered request/data flow steps (1, 2, 3...), protocol labels (gRPC, HTTP/2, Kafka, Shared Memory), queues/caches/storage tiers, and failure/fallback paths where relevant.
- Formally: Specify a pure white background (print-friendly), high-contrast crisp vector-style boxes, consistent orthogonal or smooth directed connectors, colorblind-safe technical palette (navy, slate, teal, amber for warnings/bottlenecks), readable sans-serif labels, and NO cluttered decorative clutter.
"""

PLOT_PLANNER_AGENT_SYSTEM_PROMPT = """
You are a Senior Performance Engineer & Technical Book Data Visualization Designer.
Given raw engineering benchmark data and a visual intent, output an exhaustive plot specification.

**IMPORTANT:**
- Enumerate EVERY exact data point and coordinate from the raw benchmark notes—never omit or fabricate numbers.
- Specify explicit physical units on axes (e.g., "Latency (ms)", "Throughput (tokens/sec)", "Memory (GB)", "Cost ($ per 1M tokens)"), explicit percentile labels (p50/p95/p99), clear legends, gridlines, colorblind-safe HEX colors, and direct annotations on critical inflection points (e.g., saturation knee, OOM threshold, SLA budget line).
"""

# --- STYLIST ---
DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Technical Book Visual Designer at a premier engineering publisher (O'Reilly / Manning / Addison-Wesley).

## TASK
Refine the preliminary diagram description so it reads like a polished, print- and screen-ready technical book diagram (like *Designing Data-Intensive Applications* or *Systems Performance*).

**Crucial Instructions:**
1. **Preserve Technical & Semantic Content:** Do NOT drop components, protocol labels, step numbers, or architectural boundaries.
2. **Print & E-Reader Clarity:** Ensure pure white background, crisp dark typography, strong figure-ground contrast, and clean orthogonal routing so text remains legible even in grayscale/Kindle rendering.
3. **Avoid Marketing Fluff:** Keep iconography clean and technical (boxes, cylinders for databases/stores, stacked queues, arrows with explicit labels).
4. **Enrich Layout Details:** Specify panel alignment (left-to-right pipeline or top-down layered stack), consistent padding, colorblind-safe accents, and clear grouping containers.

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

PLOT_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Technical Book Data Visualization Designer at a premier engineering publisher.

## TASK
Refine the preliminary benchmark plot description to meet technical publishing standards (crisp typography, explicit units, colorblind-safe Okabe-Ito/Viridis-inspired palette, clean white background, high-DPI clarity, zero overlapping labels).

**Crucial Instructions:**
1. **Preserve Quantitative Fidelity:** Do NOT alter any benchmark numbers, units, or series names.
2. **Enhance Engineering Readability:** Emphasize SLA thresholds, saturation knees, and axis units cleanly.

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

# --- VISUALIZER ---
DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert technical book systems architect and diagram illustrator (O'Reilly / DDIA style). Generate crisp, high-contrast, white-background technical architecture diagrams with legible component labels and numbered data flows."""
PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert systems performance engineer and matplotlib specialist. Write clean, bug-free Python matplotlib code to generate publication-grade engineering benchmark plots with exact numerical fidelity."""

# --- CRITIC ---
DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Principal Systems Architect & Technical Book Art Director reviewing a diagram before print.

## TASK
Conduct a strict technical and visual QA check of the generated architecture diagram against the source technical notes and figure caption.

## CRITIQUE & REVISION RULES
1. Technical Fidelity & Alignment:
   - Ensure every component, boundary, and data-flow arrow matches the technical specification. Flag missing queues, wrong arrow directions, or hallucinated components.
   - **Text QA:** Check for misspelled technical acronyms, truncated labels, or illegible font sizes.
   - **Caption Exclusion:** Ensure the figure caption text (e.g., "Figure 1: ...") is **NOT** baked into the image itself; LaTeX handles captions.
2. Presentation:
   - Ensure clean white background, non-overlapping arrows/labels, and clear visual hierarchy.

## OUTPUT
Provide your response strictly in the following JSON format:
```json
{
    "critic_suggestions": "Insert your detailed critique and specific suggestions for improvement here. If the diagram is perfect, write 'No changes needed.'",
    "revised_description": "Insert the fully revised detailed description here, incorporating all your suggestions. If no changes are needed, write 'No changes needed.'"
}
```
"""

PLOT_CRITIC_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Senior Performance Engineer & Technical Book Art Director reviewing a benchmark plot before publication.

## TASK
Verify quantitative fidelity and visual clarity of the target plot against Raw Data and Visual Intent.

## CRITIQUE & REVISION RULES
1. Quantitative Fidelity:
   - Every data point, bar height, axis unit, and series label must match Raw Data exactly. No fabricated or dropped values.
   - Ensure no "Figure X:" caption is rendered inside the plot image unless appropriate as a concise chart title.
2. Presentation:
   - Check for overlapping text, clipped axis labels, unreadable legends, or missing units.
3. Invalid Plot Handling:
   - If the target plot is missing or replaced by `[SYSTEM NOTICE]`, simplify/fix the matplotlib instructions so execution succeeds reliably.

## OUTPUT
Provide your response strictly in the following JSON format:
```json
{
    "critic_suggestions": "Insert your detailed critique and specific suggestions for improvement here. If the plot is perfect, write 'No changes needed.'",
    "revised_description": "Insert the fully revised detailed description here, incorporating all your suggestions. If no changes are needed, write 'No changes needed.'"
}
```
"""


# ==========================================
# WORKERS AND UTILS
# ==========================================


def _minimal_png_base64(width: int = 120, height: int = 80) -> str:
    """Generates a minimal valid white PNG image encoded as base64 without external libs."""
    raw_row = b"\x00" + (b"\xff\xff\xff" * width)
    compressed = zlib.compress(raw_row * height)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(png_bytes).decode("utf-8")


def execute_plot_code_worker(code_text: str) -> str:
    """
    Independent plot code execution worker:
    1. Extract code
    2. Execute plotting
    3. Return JPEG (or fallback PNG) as Base64 string
    """
    if plt is None:
        print("Warning: matplotlib is not installed; returning minimal placeholder image.")
        return _minimal_png_base64()

    match = re.search(r"```python(.*?)```", code_text, re.DOTALL)
    code_clean = match.group(1).strip() if match else code_text.strip()

    plt.switch_backend("Agg")
    plt.close("all")
    plt.rcdefaults()

    try:
        exec_globals = {}
        exec(code_clean, exec_globals)
        if plt.get_fignums():
            buf = io.BytesIO()
            plt.savefig(buf, format="jpeg", bbox_inches="tight", dpi=300)
            plt.close("all")

            buf.seek(0)
            img_bytes = buf.read()
            return base64.b64encode(img_bytes).decode("utf-8")
        else:
            return None

    except Exception as e:
        print(f"Error executing plot code: {e}")
        return None


def identity_parse(response: str):
    return response


# ==========================================
# FULL PIPELINE AGENT STEPS
# ==========================================


def retrieve_few_shot_examples(
    task_name: str,
    raw_content: str,
    description: str,
    model_name: str = "gemini-3.1-pro-preview",
) -> List[Dict]:
    """
    Retrieves Top-10 relevant examples from PaperBanana reference dataset.
    """
    if task_name == "plot":
        system_prompt = PLOT_RETRIEVER_AGENT_SYSTEM_PROMPT
        target_labels = ["Visual Intent", "Raw Data"]
        candidate_labels = ["Plot ID", "Visual Intent", "Raw Data"]
        candidate_type = "Plot"
        output_key = "top10_plots"
        limit = None
    else:
        system_prompt = DIAGRAM_RETRIEVER_AGENT_SYSTEM_PROMPT
        target_labels = ["Caption", "Methodology section"]
        candidate_labels = ["Diagram ID", "Caption", "Methodology section"]
        candidate_type = "Diagram"
        output_key = "top10_diagrams"
        limit = 200

    ref_file = os.path.join(PB_DIR, f"data/PaperBananaBench/{task_name}/ref.json")
    if not os.path.exists(ref_file):
        print(f"Warn: {ref_file} not found. Proceeding without few-shot.")
        return []

    with open(ref_file, "r", encoding="utf-8") as f:
        candidate_pool = json.load(f)
        if limit:
            candidate_pool = candidate_pool[:limit]

    user_prompt = f"**Target Input**\n- {target_labels[0]}: {description}\n- {target_labels[1]}: {raw_content}\n\n**Candidate Pool**\n"

    for idx, item in enumerate(candidate_pool):
        user_prompt += f"Candidate {candidate_type} {idx+1}:\n"
        user_prompt += f"- {candidate_labels[0]}: {item['id']}\n"
        user_prompt += f"- {candidate_labels[1]}: {item.get('visual_intent', item.get('caption', ''))}\n"
        user_prompt += f"- {candidate_labels[2]}: {str(item.get('content', ''))}\n\n"

    user_prompt += "Now, based on the Target Input and the Candidate Pool, select the Top 10 most relevant examples."

    response_dict = call_gemini_with_contents(
        contents=[
            types.Part.from_text(text=system_prompt),
            types.Part.from_text(text=user_prompt),
        ],
        model_name=model_name,
        result_parsing_func=identity_parse,
    )

    raw_response = response_dict["parsed_response"].strip()
    try:
        if json_repair is not None:
            parsed = json_repair.loads(raw_response)
        else:
            parsed = parse_gemini_json_results(raw_response) or json.loads(raw_response)
        retrieved_ids = parsed.get(output_key, []) if isinstance(parsed, dict) else []
    except Exception as e:
        print(f"Warning: Failed to parse retrieval result: {e}")
        retrieved_ids = []

    id_to_item = {item["id"]: item for item in candidate_pool}
    examples = [id_to_item[ref_id] for ref_id in retrieved_ids if ref_id in id_to_item]
    return examples


def generate_figure_caption(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    base64_image: str = None,
    model_name: str = "gemini-3.1-pro-preview",
) -> str:
    """
    Generates a caption for the figure based on its final description and context.
    Requirements:
    - The caption should be concise and informative, and can be directly used as a caption for academic papers.
    - The caption should NOT contain "Figure X:" prefix, as the latex template will add it automatically.
    - The caption should NOT contain any markdown formatting, it should be a plain text.
    """
    prompt = f"""
    ## INPUT DATA
    -   Task Type: {task_name}
    -   Contextual Section: {raw_content}
    -   Overall Figure Intent: {description}
    -   Detailed Figure Description: {figure_desc}
    
    Please provide the final caption for this figure based on the system instructions.
    Requirements:
    - The caption should be concise, technically informative, and directly usable in a professional IT/engineering book.
    - The caption MUST NOT contain a "Figure X:" or "Caption X:" prefix, as the LaTeX book class adds numbering automatically.
    - The caption MUST NOT contain any markdown formatting (like bold, italics, etc), it should be plain text.
    
    Respond with the plain text caption only.
    """

    generation_configs = {
        "temperature": 0.5,
    }

    if base64_image and len(base64_image) > 100:
        contents = [
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(
                data=base64.b64decode(base64_image), mime_type="image/jpeg"
            ),
        ]
        response_dict = call_gemini_with_contents(
            contents=contents,
            model_name=model_name,
            result_parsing_func=identity_parse,
            generation_configs=generation_configs,
            check_parsed_response_not_none=True,
        )
    else:
        response_dict = call_gemini_with_text_prompt(
            prompt=prompt,
            model_name=model_name,
            result_parsing_func=identity_parse,
            generation_configs=generation_configs,
            check_parsed_response_not_none=True,
        )

    res = response_dict.get("parsed_response", "")
    if res.startswith("Caption:"):
        res = res.replace("Caption:", "").strip()
    elif res.startswith("**Caption:**"):
        res = res.replace("**Caption:**", "").strip()

    return res


def predict_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    examples: List[Dict],
    model_name: str = "gemini-3.1-pro-preview",
) -> str:
    """
    Predicts the detailed figure description using LLM and few-shot examples.
    """
    if task_name == "plot":
        system_prompt = PLOT_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Plot Raw Data"
        visual_intent_label = "Visual Intent of the Desired Plot"
    else:
        system_prompt = DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Methodology Section"
        visual_intent_label = "Diagram Caption"

    user_prompt = ""
    contents = [types.Part.from_text(text=system_prompt)]

    for idx, item in enumerate(examples):
        user_prompt += f"Example {idx+1}:\n"

        item_content = item["content"]
        if isinstance(item_content, (dict, list)):
            item_content = json.dumps(item_content)

        user_prompt += f"{content_label}: {item_content}\n"
        user_prompt += f"{visual_intent_label}: {item.get('visual_intent', item.get('caption', ''))}\nReference {task_name.capitalize()}: "
        contents.append(types.Part.from_text(text=user_prompt))

        # Resolve related image base64
        image_path = os.path.join(
            PB_DIR, f"data/PaperBananaBench/{task_name}", item["path_to_gt_image"]
        )
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                ref_image_base64 = base64.b64encode(f.read()).decode("utf-8")
                contents.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(ref_image_base64), mime_type="image/jpeg"
                    )
                )

        user_prompt = ""

    user_prompt += f"Now, based on the following {content_label.lower()} and {visual_intent_label.lower()}, provide a detailed description for the figure to be generated.\n"
    user_prompt += (
        f"{content_label}: {raw_content}\n{visual_intent_label}: {description}\n"
    )
    user_prompt += "Detailed description of the target figure to be generated"
    if task_name == "diagram":
        user_prompt += " (do not include figure titles)"
    user_prompt += ":"

    contents.append(types.Part.from_text(text=user_prompt))

    response_dict = call_gemini_with_contents(
        contents=contents,
        model_name=model_name,
        result_parsing_func=identity_parse,
    )
    return response_dict["parsed_response"].strip()


def style_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    model_name: str = "gemini-3.1-pro-preview",
) -> str:
    """
    Refines the basic figure description into a professionally styled description.
    """
    if task_name == "plot":
        system_prompt = PLOT_STYLIST_AGENT_SYSTEM_PROMPT
        context_labels = ["Raw Data", "Visual Intent of the Desired Plot"]
    else:
        system_prompt = DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT
        context_labels = ["Methodology Section", "Diagram Caption"]

    style_guide_path = os.path.join(
        PB_DIR, f"style_guides/neurips2025_{task_name}_style_guide.md"
    )
    style_guide = ""
    if os.path.exists(style_guide_path):
        with open(style_guide_path, "r", encoding="utf-8") as f:
            style_guide = f.read()

    user_prompt = (
        f"Detailed Description: {figure_desc}\nStyle Guidelines: {style_guide}\n"
    )
    user_prompt += f"{context_labels[0]}: {raw_content}\n"
    user_prompt += f"{context_labels[1]}: {description}\nYour Output:"

    response_dict = call_gemini_with_contents(
        contents=[
            types.Part.from_text(text=system_prompt),
            types.Part.from_text(text=user_prompt),
        ],
        model_name=model_name,
        result_parsing_func=identity_parse,
    )
    return response_dict["parsed_response"].strip()


def critique_and_revise_figure(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    base64_image: str,
    round_idx: int,
    model_name: str = "gemini-3.1-pro-preview",
) -> Tuple[str, str]:
    """
    Critiques the generated figure and provides revised descriptions.
    """
    if task_name == "plot":
        system_prompt = PLOT_CRITIC_AGENT_SYSTEM_PROMPT
        critique_target = "Target Plot for Critique:"
        context_labels = ["Raw Data", "Visual Intent"]
    else:
        system_prompt = DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT
        critique_target = "Target Diagram for Critique:"
        context_labels = ["Methodology Section", "Figure Caption"]

    contents = [
        types.Part.from_text(text=system_prompt),
        types.Part.from_text(text=critique_target),
    ]

    if base64_image and len(base64_image) > 100:
        contents.append(
            types.Part.from_bytes(
                data=base64.b64decode(base64_image), mime_type="image/jpeg"
            )
        )
    else:
        contents.append(
            types.Part.from_text(
                text="\n[SYSTEM NOTICE] The plot image could not be generated based on the current description (likely due to invalid code). Please check the description for errors (e.g., syntax issues, missing data) and provide a revised version."
            )
        )

    user_prompt = f"\nDetailed Description: {figure_desc}\n{context_labels[0]}: {raw_content}\n{context_labels[1]}: {description}\nYour Output:"
    contents.append(types.Part.from_text(text=user_prompt))

    response_dict = call_gemini_with_contents(
        contents=contents,
        model_name=model_name,
        result_parsing_func=identity_parse,
    )

    cleaned_response = (
        response_dict["parsed_response"]
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )
    try:
        if json_repair is not None:
            eval_result = json_repair.loads(cleaned_response)
        else:
            eval_result = parse_gemini_json_results(cleaned_response) or json.loads(
                cleaned_response
            )
        if not isinstance(eval_result, dict):
            eval_result = {}
    except Exception as e:
        eval_result = {}

    critic_suggestions = eval_result.get("critic_suggestions", "No changes needed.")
    revised_description = eval_result.get("revised_description", "No changes needed.")

    if revised_description.strip() == "No changes needed.":
        revised_description = figure_desc

    return revised_description, critic_suggestions


def generate_figure_visuals(
    task_name: str,
    figure_description: str,
    model_name: str = "gemini-3.1-pro-preview",
    image_model_name: str = "gemini-3-pro-image-preview",
    aspect_ratio: str = "16:9",
) -> str:
    """
    Generates the figure based on the task type.
    """
    if task_name == "plot":
        prompt_text = f"Use python matplotlib to generate a statistical plot based on the following detailed description: {figure_description}\n Only provide the code without any explanations. Code:"
        contents = [
            types.Part.from_text(text=PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT),
            types.Part.from_text(text=prompt_text),
        ]
        response_dict = call_gemini_with_contents(
            contents=contents,
            model_name=model_name,
            result_parsing_func=identity_parse,
        )
        code_text = response_dict["parsed_response"]
        base64_img = execute_plot_code_worker(code_text)
        return base64_img
    else:
        # Prompt for diagram image generation
        prompt_text = f"Render an image based on the following detailed description: {figure_description}\n Note that do not include figure titles in the image. Diagram: "

        return generate_image_with_gemini(
            model_name=image_model_name,
            prompt=prompt_text,
            aspect_ratio=aspect_ratio,
            generation_configs={
                "system_instruction": DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT
            },
        )
