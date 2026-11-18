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

import ast
import base64
import io
import os
import re
import json
import struct
import subprocess
import sys
import tempfile
import textwrap
import zlib
from typing import Dict, List, Tuple, Set

try:
    import matplotlib.pyplot as plt  # type: ignore
except ImportError:
    plt = None  # type: ignore

try:
    import json_repair  # type: ignore
except ImportError:
    json_repair = None  # type: ignore

from utils.agent_runtime_utils import (
    call_agent_with_text_prompt,
    call_agent_with_contents,
    generate_image_with_agent,
    parse_agent_json_response,
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


ALLOWED_PLOT_MODULES: Set[str] = {
    "matplotlib",
    "matplotlib.pyplot",
    "matplotlib.patches",
    "matplotlib.ticker",
    "matplotlib.cm",
    "matplotlib.colors",
    "matplotlib.lines",
    "matplotlib.gridspec",
    "matplotlib.patheffects",
    "numpy",
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "dataclasses",
    "typing",
    "decimal",
    "fractions",
}

FORBIDDEN_PLOT_NAMES: Set[str] = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "input",
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "urllib",
    "requests",
    "http",
    "pathlib",
    "ctypes",
    "pickle",
    "builtins",
    "importlib",
    "inspect",
    "io",
    "tempfile",
    "glob",
    "mmap",
}

FORBIDDEN_PLOT_ATTRIBUTES: Set[str] = {
    "system",
    "popen",
    "spawn",
    "fork",
    "kill",
    "remove",
    "unlink",
    "rmdir",
    "rmtree",
    "listdir",
    "scandir",
    "walk",
    "environ",
    "modules",
    "savefig",
    "imread",
    "imsave",
    "rc_file",
    "rc_file_defaults",
    "fromfile",
    "tofile",
    "load",
    "loads",
    "save",
    "savez",
    "savez_compressed",
    "savetxt",
    "loadtxt",
    "genfromtxt",
    "fromregex",
    "memmap",
    "DataSource",
    "recfromcsv",
    "recfromtxt",
    "open",
    "fdopen",
    "read",
    "write",
    "readline",
    "readlines",
    "writelines",
}


class UnsafePlotCodeError(ValueError):
    """Raised when LLM-generated plot code violates the AST security policy."""


def validate_plot_code_ast(code_clean: str) -> ast.AST:
    """
    Statically validates LLM-generated matplotlib code before execution.
    Rejects non-allowlisted imports, dangerous builtins, file/network access
    (including NumPy/Matplotlib file I/O APIs), and dunder traversal.
    """
    if not code_clean or not code_clean.strip():
        raise UnsafePlotCodeError("Empty plot code.")

    try:
        tree = ast.parse(code_clean, mode="exec")
    except SyntaxError as e:
        raise UnsafePlotCodeError(f"SyntaxError in plot code: {e}") from e

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.strip()
                if mod not in ALLOWED_PLOT_MODULES:
                    raise UnsafePlotCodeError(
                        f"Disallowed import '{mod}' in plot code."
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise UnsafePlotCodeError("Relative imports are disallowed in plot code.")
            mod = (node.module or "").strip()
            if mod not in ALLOWED_PLOT_MODULES:
                raise UnsafePlotCodeError(
                    f"Disallowed import from '{mod}' in plot code."
                )
            for alias in node.names:
                if (
                    alias.name == "*"
                    or alias.name.startswith("__")
                    or alias.name in FORBIDDEN_PLOT_ATTRIBUTES
                    or alias.name in FORBIDDEN_PLOT_NAMES
                ):
                    raise UnsafePlotCodeError(
                        f"Disallowed symbol import '{alias.name}' in plot code."
                    )
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_PLOT_NAMES or node.id.startswith("__"):
                raise UnsafePlotCodeError(
                    f"Disallowed identifier '{node.id}' in plot code."
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in FORBIDDEN_PLOT_ATTRIBUTES:
                raise UnsafePlotCodeError(
                    f"Disallowed attribute access '.{node.attr}' in plot code."
                )

    return tree


def execute_plot_code_worker(code_text: str, timeout_sec: int = 15) -> str:
    """
    Sandboxed plot code execution worker:
    1. Extracts Python code and validates its AST against strict allowlists
       (blocking os/sys/open as well as NumPy/Matplotlib file I/O methods).
    2. Runs the code in an isolated child process (`python -I`) inside a temporary directory
       with resource limits, disabled NumPy/Matplotlib file APIs, and a kernel-level
       `sys.addaudithook` that blocks all file open/read/write, process, and network events.
    3. Reads the rendered JPEG image and returns its Base64 string (or None on error/violation).
    """
    match = re.search(r"```python(.*?)```", code_text or "", re.DOTALL)
    code_clean = match.group(1).strip() if match else (code_text or "").strip()

    try:
        validate_plot_code_ast(code_clean)
    except UnsafePlotCodeError as e:
        print(f"Security/AST check rejected plot code: {e}")
        return None

    if plt is None:
        return _minimal_png_base64()

    runner_source = textwrap.dedent(
        """
        import builtins
        import io
        import os
        import resource
        import sys

        try:
            resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
            resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        except Exception:
            pass

        code_path = sys.argv[1]
        out_path = sys.argv[2]
        with open(code_path, "r", encoding="utf-8") as f:
            user_code = f.read()

        # Pre-open output file descriptor before locking the runtime audit hook
        out_fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        img_buf = io.BytesIO()

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        plt.close("all")
        plt.rcdefaults()

        def _blocked_io(*a, **kw):
            raise PermissionError("File I/O is disabled inside the plot sandbox.")

        # Strip file I/O functions from matplotlib and numpy
        _real_savefig = plt.savefig
        plt.show = lambda *a, **kw: None
        plt.savefig = _blocked_io
        plt.imread = _blocked_io
        plt.imsave = _blocked_io
        matplotlib.rc_file = _blocked_io

        for _np_fn in (
            "load", "loads", "save", "savez", "savez_compressed",
            "savetxt", "loadtxt", "genfromtxt", "fromfile", "fromregex",
            "memmap", "recfromcsv", "recfromtxt",
        ):
            if hasattr(np, _np_fn):
                setattr(np, _np_fn, _blocked_io)
        if hasattr(np, "lib") and hasattr(np.lib, "npyio"):
            for _nio_fn in ("load", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromregex", "DataSource"):
                if hasattr(np.lib.npyio, _nio_fn):
                    setattr(np.lib.npyio, _nio_fn, _blocked_io)

        ALLOWED = {
            "matplotlib",
            "matplotlib.pyplot",
            "matplotlib.patches",
            "matplotlib.ticker",
            "matplotlib.cm",
            "matplotlib.colors",
            "matplotlib.lines",
            "matplotlib.gridspec",
            "matplotlib.patheffects",
            "numpy",
            "math",
            "statistics",
            "collections",
            "itertools",
            "functools",
            "dataclasses",
            "typing",
            "decimal",
            "fractions",
        }
        _real_import = builtins.__import__

        def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level != 0 or (name not in ALLOWED and name.split(".")[0] not in {"matplotlib", "numpy"}):
                raise ImportError(f"Import of '{name}' is blocked by plot sandbox.")
            return _real_import(name, globals, locals, fromlist, level)

        safe_builtins = dict(builtins.__dict__)
        for bad in ("open", "eval", "exec", "compile", "input", "breakpoint", "getattr", "setattr", "delattr", "globals", "locals", "vars"):
            safe_builtins.pop(bad, None)
        safe_builtins["__import__"] = _safe_import

        # Warm up matplotlib font/renderer cache before locking OS file descriptors
        _warm_fig, _warm_ax = plt.subplots(figsize=(1, 1))
        _warm_ax.plot([0, 1], [0, 1])
        _warm_ax.set_title("warmup")
        _real_savefig( io.BytesIO(), format="jpeg", dpi=72)
        plt.close("all")
        plt.rcdefaults()

        compiled_code = compile(user_code, "<plot_code>", "exec")
        exec_globals = {"__builtins__": safe_builtins, "__name__": "__main__"}

        _USER_CODE_ACTIVE = [True]

        def _audit_guard(event, args):
            if not _USER_CODE_ACTIVE[0]:
                return
            if event in ("open", "os.system", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty", "subprocess.Popen", "ctypes.dlopen", "mmap.new"):
                raise PermissionError(f"Sandbox audit hook blocked '{event}' during plot execution.")
            if event.startswith(("os.", "shutil.", "socket.", "urllib.", "http.", "ftplib.", "subprocess.", "pathlib.", "glob.", "tempfile.")):
                raise PermissionError(f"Sandbox audit hook blocked '{event}' during plot execution.")

        # Kernel-level OS sandbox lock: setting RLIMIT_NOFILE to (0, 0) prevents the process
        # (including C extensions like ndarray.tofile / fopen / socket) from opening ANY new file descriptor or socket.
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (0, 0))
        except Exception:
            pass

        sys.addaudithook(_audit_guard)
        try:
            exec(compiled_code, exec_globals)
        finally:
            _USER_CODE_ACTIVE[0] = False

        if plt.get_fignums():
            _real_savefig(img_buf, format="jpeg", bbox_inches="tight", dpi=300)
            plt.close("all")
            os.write(out_fd, img_buf.getvalue())
        os.close(out_fd)
        """
    )

    try:
        with tempfile.TemporaryDirectory(prefix="book_plot_sbx_") as tmpdir:
            code_file = os.path.join(tmpdir, "plot_script.py")
            runner_file = os.path.join(tmpdir, "_runner.py")
            out_img = os.path.join(tmpdir, "output.jpg")

            with open(code_file, "w", encoding="utf-8") as f:
                f.write(code_clean)
            with open(runner_file, "w", encoding="utf-8") as f:
                f.write(runner_source)

            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": tmpdir,
                "HOME": tmpdir,
            }

            base_cmd = [sys.executable, "-I", runner_file, code_file, out_img]
            proc = subprocess.run(
                base_cmd,
                cwd=tmpdir,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                print(f"Error executing sandboxed plot code: {proc.stderr.strip()}")
                return None
            if os.path.exists(out_img) and os.path.getsize(out_img) > 0:
                with open(out_img, "rb") as img_f:
                    return base64.b64encode(img_f.read()).decode("utf-8")
            return None
    except Exception as e:
        print(f"Error executing plot code in sandbox: {e}")
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
        response_dict = call_agent_with_contents(
            contents=contents,
            model_name=model_name,
            result_parsing_func=identity_parse,
            generation_configs=generation_configs,
            check_parsed_response_not_none=True,
        )
    else:
        response_dict = call_agent_with_text_prompt(
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

    response_dict = call_agent_with_contents(
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
    response_dict = call_agent_with_contents(
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

    response_dict = call_agent_with_contents(
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
            eval_result = parse_agent_json_response(cleaned_response) or json.loads(
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
        response_dict = call_agent_with_contents(
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
        return generate_image_with_agent(
            model_name=image_model_name,
            prompt=prompt_text,
            aspect_ratio=aspect_ratio,
            generation_configs={
                "system_instruction": DIAGRAM_VISUALIZER_AGENT_SYSTEM_PROMPT
            },
        )
