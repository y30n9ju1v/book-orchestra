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
Technical Book Architecture Diagram & Benchmark Plot Utilities for BookOrchestra.
"""

import base64
import io
import re
import json
import struct
import zlib
from typing import Dict, List, Tuple

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

# ==========================================
# PROMPTS (Technical & Engineering Book Publishing Standards)
# ==========================================

DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT = """
You are a Principal Systems Architect & Technical Book Illustrator (O'Reilly / DDIA / Addison-Wesley caliber).
Given a technical chapter context / specification and a figure caption, generate an exhaustive, unambiguous visual blueprint for an engineering diagram.

**IMPORTANT:**
Your description must make the diagram genuinely useful to a senior engineer reading a printed or digital technical book:
- Semantically: Define explicit system boundaries (e.g., Control Plane vs. Data Plane, Client Edge vs. Inference Cluster, Hot Path vs. Async Path), numbered request/data flow steps (1, 2, 3...), protocol labels (gRPC, HTTP/2, Kafka, Shared Memory), queues/caches/storage tiers, and failure/fallback paths where relevant.
- Formally: Specify a pure white background (print-friendly), high-contrast crisp vector-style boxes, consistent orthogonal or smooth directed connectors, colorblind-safe technical palette (navy, slate, teal, amber for warnings/bottlenecks), readable sans-serif labels, and zero decorative clutter.
"""

PLOT_PLANNER_AGENT_SYSTEM_PROMPT = """
You are a Senior Performance Engineer & Technical Book Data Visualization Designer.
Given raw engineering benchmark data and a visual intent, output an exhaustive plot specification.

**IMPORTANT:**
- Enumerate EVERY exact data point and coordinate from the raw benchmark notes—never omit or fabricate numbers.
- Specify explicit physical units on axes (e.g., "Latency (ms)", "Throughput (tokens/sec)", "Memory (GB)", "Cost ($ per 1M tokens)"), explicit percentile labels (p50/p95/p99), clear legends, gridlines, colorblind-safe HEX colors, and direct annotations on critical inflection points (e.g., saturation knee, OOM threshold, SLA budget line).
"""

DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Technical Book Visual Designer at a premier engineering publisher (O'Reilly / Manning / Addison-Wesley).

## TASK
Refine the preliminary diagram description so it reads like a polished, print- and screen-ready technical book diagram (like *Designing Data-Intensive Applications* or *Systems Performance*).

**Crucial Instructions:**
1. **Preserve Technical & Semantic Content:** Do NOT drop components, protocol labels, step numbers, or architectural boundaries.
2. **Print & E-Reader Clarity:** Ensure pure white background, crisp dark typography, strong figure-ground contrast, and clean orthogonal routing so text remains legible even in grayscale/e-ink rendering.
3. **Avoid Marketing Fluff:** Keep iconography clean and technical (boxes, cylinders for stores, stacked queues, arrows with explicit labels).

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

PLOT_STYLIST_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Lead Technical Book Data Visualization Designer at a premier engineering publisher.

## TASK
Refine the preliminary benchmark plot description to meet technical publishing standards (crisp typography, explicit units, colorblind-safe palette, clean white background, high-DPI clarity, zero overlapping labels).

## OUTPUT
Output ONLY the final polished Detailed Description. Do not include any conversational text or explanations.
"""

DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert technical book systems architect and diagram illustrator (O'Reilly / DDIA style). Generate crisp, high-contrast, white-background technical architecture diagrams with legible component labels and numbered data flows."""
PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT = """You are an expert systems performance engineer and matplotlib specialist. Write clean, bug-free Python matplotlib code to generate publication-grade engineering benchmark plots with exact numerical fidelity."""

DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT = """
## ROLE
You are a Principal Systems Architect & Technical Book Art Director reviewing a diagram before print.

## TASK
Conduct a strict technical and visual QA check of the generated architecture diagram against the source technical notes and figure caption.

## CRITIQUE & REVISION RULES
1. Technical Fidelity & Alignment:
   - Ensure every component, boundary, and data-flow arrow matches the technical specification. Flag missing queues, wrong arrow directions, or hallucinated components.
   - **Text QA:** Check for misspelled technical acronyms, truncated labels, or illegible font sizes.
   - **Caption Exclusion:** Ensure the figure caption text is **NOT** baked into the image itself; LaTeX handles captions.
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

## OUTPUT
Provide your response strictly in the following JSON format:
```json
{
    "critic_suggestions": "Insert your detailed critique and specific suggestions for improvement here. If the plot is perfect, write 'No changes needed.'",
    "revised_description": "Insert the fully revised detailed description here, incorporating all your suggestions. If no changes are needed, write 'No changes needed.'"
}
```
"""


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
    if plt is None:
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
            return base64.b64encode(buf.read()).decode("utf-8")
        return None
    except Exception as e:
        print(f"Error executing plot code: {e}")
        return None


def identity_parse(response: str):
    return response


def generate_figure_caption(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    base64_image: str = None,
    model_name: str = "inherit",
) -> str:
    prompt = f"""
    ## INPUT DATA
    - Task Type: {task_name}
    - Contextual Chapter Notes: {raw_content}
    - Overall Visual Intent: {description}
    - Detailed Visual Description: {figure_desc}

    Provide a concise, technically informative caption for an IT/engineering book.
    - MUST NOT contain a "Figure X:" or "Caption X:" prefix.
    - MUST NOT contain markdown formatting.
    Respond with plain text only.
    """
    generation_configs = {"temperature": 0.5}
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
    for prefix in ("Caption:", "**Caption:**"):
        if res.startswith(prefix):
            res = res.replace(prefix, "", 1).strip()
    return res


def predict_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    examples: List[Dict] = None,
    model_name: str = "inherit",
) -> str:
    if task_name == "plot":
        system_prompt = PLOT_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Benchmark Raw Data"
        visual_intent_label = "Visual Intent of the Plot"
    else:
        system_prompt = DIAGRAM_PLANNER_AGENT_SYSTEM_PROMPT
        content_label = "Architecture Specification"
        visual_intent_label = "Diagram Caption & Objective"

    user_prompt = (
        f"Based on the following {content_label.lower()} and {visual_intent_label.lower()}, "
        f"provide a detailed specification for the visual to be generated.\n"
        f"{content_label}: {raw_content}\n{visual_intent_label}: {description}\n"
        f"Detailed description:"
    )

    response_dict = call_gemini_with_contents(
        contents=[
            types.Part.from_text(text=system_prompt),
            types.Part.from_text(text=user_prompt),
        ],
        model_name=model_name,
        result_parsing_func=identity_parse,
    )
    return response_dict["parsed_response"].strip()


def style_figure_content(
    task_name: str,
    raw_content: str,
    description: str,
    figure_desc: str,
    model_name: str = "inherit",
) -> str:
    system_prompt = (
        PLOT_STYLIST_AGENT_SYSTEM_PROMPT
        if task_name == "plot"
        else DIAGRAM_STYLIST_AGENT_SYSTEM_PROMPT
    )
    user_prompt = (
        f"Detailed Description: {figure_desc}\n"
        f"Technical Context: {raw_content}\n"
        f"Visual Intent: {description}\nYour Output:"
    )
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
    model_name: str = "inherit",
) -> Tuple[str, str]:
    system_prompt = (
        PLOT_CRITIC_AGENT_SYSTEM_PROMPT
        if task_name == "plot"
        else DIAGRAM_CRITIC_AGENT_SYSTEM_PROMPT
    )
    contents = [types.Part.from_text(text=system_prompt)]
    if base64_image and len(base64_image) > 100:
        contents.append(
            types.Part.from_bytes(
                data=base64.b64decode(base64_image), mime_type="image/jpeg"
            )
        )
    user_prompt = (
        f"\nDetailed Description: {figure_desc}\n"
        f"Technical Context: {raw_content}\n"
        f"Visual Intent: {description}\nYour Output:"
    )
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
    except Exception:
        eval_result = {}

    critic_suggestions = eval_result.get("critic_suggestions", "No changes needed.")
    revised_description = eval_result.get("revised_description", "No changes needed.")
    if revised_description.strip() == "No changes needed.":
        revised_description = figure_desc
    return revised_description, critic_suggestions


def generate_figure_visuals(
    task_name: str,
    figure_description: str,
    model_name: str = "inherit",
    image_model_name: str = "image-generator",
    aspect_ratio: str = "16:9",
) -> str:
    if task_name == "plot":
        prompt_text = (
            f"Use python matplotlib to generate an engineering benchmark plot based on: "
            f"{figure_description}\nOnly provide the code without explanations. Code:"
        )
        response_dict = call_gemini_with_contents(
            contents=[
                types.Part.from_text(text=PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT),
                types.Part.from_text(text=prompt_text),
            ],
            model_name=model_name,
            result_parsing_func=identity_parse,
        )
        return execute_plot_code_worker(response_dict["parsed_response"])
    else:
        prompt_text = (
            f"Render a technical architecture diagram based on: {figure_description}\n"
            f"Do not include figure titles inside the image."
        )
        return generate_image_with_gemini(
            model_name=image_model_name,
            prompt=prompt_text,
            aspect_ratio=aspect_ratio,
            generation_configs={
                "system_instruction": DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT
            },
        )
