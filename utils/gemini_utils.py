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
Antigravity-native LLM & multimodal bridge for BookOrchestra.

External `google-genai` API calls (`GEMINI_API_KEY` / `VERTEX_AI_PROJECT`) have been
replaced with an Antigravity workflow agent bridge so BookOrchestra runs natively
inside Antigravity via `run_workflow` (`workflows/book_orchestra_workflow.py`)
without external API keys.
"""

import re
import json
import os
import shutil
import struct
import subprocess
import zlib
import base64
from typing import Dict, Any, Callable, Optional, List


class _Part:
    def __init__(
        self,
        text: Optional[str] = None,
        data: Optional[bytes] = None,
        mime_type: Optional[str] = None,
    ):
        self.text = text
        self.data = data
        self.mime_type = mime_type

    @classmethod
    def from_text(cls, text: str):
        return cls(text=text)

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str):
        return cls(data=data, mime_type=mime_type)


class _TypesNamespace:
    Part = _Part
    Content = Any

    class GoogleSearch:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Tool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ImageConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


types = _TypesNamespace()

# Optional registered Antigravity agent runner for in-process bridge execution.
# Signature: runner(prompt: str, system_instruction: Optional[str], model_name: str, images: Optional[List[str]], generation_configs: Optional[dict]) -> str
_antigravity_agent_runner: Optional[Callable[..., str]] = None
_antigravity_image_runner: Optional[Callable[..., str]] = None


def set_antigravity_agent_runner(
    runner: Optional[Callable[..., str]],
    image_runner: Optional[Callable[..., str]] = None,
):
    """Registers an Antigravity native agent runner callback."""
    global _antigravity_agent_runner, _antigravity_image_runner
    _antigravity_agent_runner = runner
    if runner is None and image_runner is None:
        _antigravity_image_runner = None
    elif image_runner is not None:
        _antigravity_image_runner = image_runner


def get_antigravity_agent_runner() -> Optional[Callable[..., str]]:
    return _antigravity_agent_runner


def get_antigravity_image_runner() -> Optional[Callable[..., str]]:
    return _antigravity_image_runner


def find_agy_cli_binary() -> Optional[str]:
    """Locates the Antigravity CLI (`agy`) binary if installed on the host."""
    candidates = [
        os.environ.get("AGY_BIN"),
        shutil.which("agy"),
        os.path.expanduser("~/.local/bin/agy"),
        "/Applications/Gemini.app/Contents/Helpers/agy",
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


_AGY_MODELS_CACHE: Optional[List[str]] = None


def list_agy_cli_models(agy_bin: Optional[str] = None) -> List[str]:
    """Queries `agy models` once and caches the list of recognized full model IDs."""
    global _AGY_MODELS_CACHE
    if _AGY_MODELS_CACHE is not None:
        return list(_AGY_MODELS_CACHE)

    resolved_bin = agy_bin or find_agy_cli_binary()
    if not resolved_bin:
        _AGY_MODELS_CACHE = []
        return []

    try:
        proc = subprocess.run(
            [resolved_bin, "models"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode == 0 and proc.stdout:
            found = re.findall(
                r"\b(?:gemini|claude|gpt|o\d)-[a-zA-Z0-9.\-_]+\b", proc.stdout
            )
            seen = []
            for m in found:
                if m not in seen:
                    seen.append(m)
            _AGY_MODELS_CACHE = seen
            return list(seen)
    except Exception:
        pass

    _AGY_MODELS_CACHE = []
    return []


def resolve_agy_cli_model_name(
    model_name: Optional[str], agy_bin: Optional[str] = None
) -> Optional[str]:
    """
    Translates workflow model tier aliases ('inherit', 'pro', 'flash', 'flash_lite')
    into full model IDs recognized by `agy --model`, or returns None so `agy` uses
    its default session model without failing on short aliases.
    """
    if not model_name:
        return None
    clean = str(model_name).strip()
    if clean.lower() in ("inherit", "default", "auto", ""):
        return None

    available = list_agy_cli_models(agy_bin=agy_bin)
    low = clean.lower()

    if low in ("pro", "flash", "flash_lite", "flash-lite"):
        if not available:
            # When `agy models` cannot be queried, omit `--model` so `agy` uses the active session default
            # instead of failing with `Error: model flash is not recognized`.
            return None
        if low in ("flash_lite", "flash-lite"):
            for m in available:
                if "flash" in m.lower() and "lite" in m.lower():
                    return m
            for m in available:
                if "flash" in m.lower():
                    return m
        elif low == "flash":
            for m in available:
                if "flash" in m.lower() and "lite" not in m.lower():
                    return m
            for m in available:
                if "flash" in m.lower():
                    return m
        elif low == "pro":
            for m in available:
                if "pro" in m.lower():
                    return m
        return None

    if available and clean not in available:
        for m in available:
            if low in m.lower():
                return m
        return None

    return clean


def _schema_to_json_str(schema_obj: Any) -> Optional[str]:
    if schema_obj is None:
        return None
    if isinstance(schema_obj, str):
        return schema_obj
    if isinstance(schema_obj, dict):
        return json.dumps(schema_obj, ensure_ascii=False)
    if hasattr(schema_obj, "model_json_schema") and callable(
        schema_obj.model_json_schema
    ):
        try:
            return json.dumps(schema_obj.model_json_schema(), ensure_ascii=False)
        except Exception:
            return None
    return None


def create_agy_cli_runner(
    agy_bin: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    timeout_sec: int = 300,
) -> Callable[..., str]:
    """
    Creates an Antigravity CLI (`agy --print`) runner so subprocesses (CLI / Streamlit)
    can execute prompts through Antigravity without external Gemini API keys.
    Automatically maps tier aliases ('pro', 'flash', 'flash_lite'), attaches image paths,
    forwards JSON schemas, and enables research grounding when tools are requested.
    """
    resolved_bin = agy_bin or find_agy_cli_binary()
    if not resolved_bin:
        raise RuntimeError(
            "Antigravity CLI ('agy') binary not found on PATH or standard install locations."
        )

    def _runner(
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: str = "inherit",
        images: Optional[List[str]] = None,
        generation_configs: Optional[dict] = None,
    ) -> str:
        cfg = generation_configs or {}
        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"

        if cfg.get("tools"):
            full_prompt += (
                "\n\n[Tool Grounding Instruction]: Use web/literature search to verify "
                "all technical references, DOIs, arXiv IDs, RFC numbers, and publication years."
            )

        schema_str = _schema_to_json_str(cfg.get("response_schema"))
        if schema_str and "```json" not in full_prompt:
            full_prompt += (
                f"\n\nReturn valid JSON conforming to this schema:\n```json\n{schema_str}\n```"
            )

        if images:
            valid_imgs = [os.path.abspath(p) for p in images if p and os.path.exists(p)]
            if valid_imgs:
                full_prompt += "\n\nAttached image files to inspect:\n" + "\n".join(
                    f"- {p}" for p in valid_imgs
                )

        base_cmd = [resolved_bin, "--disable-slash-commands", "--print", full_prompt]
        if workspace_dir and os.path.isdir(workspace_dir):
            base_cmd.extend(["--add-dir", os.path.abspath(workspace_dir)])
        if images:
            for img_p in images:
                if img_p and os.path.exists(img_p):
                    img_dir = os.path.dirname(os.path.abspath(img_p))
                    if img_dir and os.path.isdir(img_dir):
                        base_cmd.extend(["--add-dir", img_dir])

        resolved_model = resolve_agy_cli_model_name(model_name, agy_bin=resolved_bin)
        cmd = list(base_cmd)
        if resolved_model:
            cmd.extend(["--model", resolved_model])

        proc = subprocess.run(
            cmd,
            cwd=workspace_dir or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        # If the model alias was rejected by this version of `agy`, retry once without `--model`
        if proc.returncode != 0 and resolved_model:
            err_msg = (proc.stderr or proc.stdout or "").lower()
            if "model" in err_msg and (
                "not recognized" in err_msg
                or "invalid" in err_msg
                or "unknown" in err_msg
                or "not found" in err_msg
            ):
                proc = subprocess.run(
                    base_cmd,
                    cwd=workspace_dir or os.getcwd(),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                )

        if proc.returncode != 0:
            err_detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"Antigravity CLI ('{resolved_bin} --print') failed with exit code {proc.returncode}: {err_detail}"
            )
        output = (proc.stdout or "").strip()
        if not output:
            raise RuntimeError("Antigravity CLI returned empty output.")
        return output

    return _runner


def create_agy_cli_image_runner(
    text_runner: Optional[Callable[..., str]] = None,
) -> Callable[..., str]:
    """
    Creates an Antigravity image/diagram runner for local CLI & Streamlit execution.
    Uses the text runner to synthesize a clean Matplotlib technical architecture diagram
    (executed in the isolated plot sandbox) or renders a labeled schematic diagram.
    """

    def _image_runner(
        prompt: str,
        aspect_ratio: str = "16:9",
        model_name: str = "inherit",
        generation_configs: Optional[dict] = None,
    ) -> str:
        if text_runner is not None:
            try:
                from utils.diagram_plot_utils import execute_plot_code_worker

                code_prompt = (
                    "You are a Technical Architecture Diagram Generator using Python matplotlib.\n"
                    "Write self-contained Python matplotlib code (using only matplotlib, matplotlib.patches, numpy) "
                    "that draws a crisp, white-background architecture block diagram with labeled boxes and arrows "
                    "for the following specification:\n\n"
                    f"{prompt}\n\n"
                    "Wrap the code in ```python ... ``` and do NOT call plt.savefig or plt.show."
                )
                code_resp = text_runner(
                    prompt=code_prompt,
                    system_instruction=None,
                    model_name=model_name,
                    images=None,
                    generation_configs=generation_configs or {},
                )
                b64 = execute_plot_code_worker(str(code_resp))
                if b64:
                    return b64
            except Exception:
                pass
        return _fallback_diagram_png_base64(prompt=prompt)

    return _image_runner


def enable_agy_cli_runner(
    workspace_dir: Optional[str] = None, timeout_sec: int = 300
) -> bool:
    """
    If no in-process Antigravity runner is registered yet, registers both the `agy --print`
    text runner and the architecture diagram image runner if `agy` is available.
    Returns True if a runner is active.
    """
    if _antigravity_agent_runner is not None:
        if _antigravity_image_runner is None:
            set_antigravity_agent_runner(
                _antigravity_agent_runner,
                image_runner=create_agy_cli_image_runner(_antigravity_agent_runner),
            )
        return True
    agy_bin = find_agy_cli_binary()
    if not agy_bin:
        return False
    text_runner = create_agy_cli_runner(
        agy_bin=agy_bin, workspace_dir=workspace_dir, timeout_sec=timeout_sec
    )
    img_runner = create_agy_cli_image_runner(text_runner=text_runner)
    set_antigravity_agent_runner(text_runner, image_runner=img_runner)
    return True


def parse_gemini_json_results(response: str):
    if not response or not isinstance(response, str):
        return None

    json_pattern = r"```json(.*?)```"
    matches = re.findall(json_pattern, response, re.DOTALL)

    if not matches:
        json_pattern = r"(\{.*\}|\[.*\])"
        matches = re.findall(json_pattern, response, re.DOTALL)

    for json_string in matches:
        json_string = json_string.strip()
        try:
            parsed_json = json.loads(json_string)
            return parsed_json
        except json.JSONDecodeError:
            try:
                json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
                json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
                json_string_clean = re.sub(
                    r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
                )

                parsed_json = json.loads(json_string_clean)
                return parsed_json
            except json.JSONDecodeError:
                continue

    try:
        start_idx = response.find("{")
        end_idx = response.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    return None


def parse_gemini_latex_results(response: str):
    match = re.search(r"```latex\s*(.*?)\s*```", response.strip(), re.DOTALL)
    if match:
        latex_string = match.group(1)
    else:
        latex_string = response
    return latex_string


def parse_gemini_json_latex_response(response_text: str) -> str:
    """
    Extracts the JSON worklog and the LaTeX code from the agent response.
    Returns a JSON-serialized string with keys "json" and "latex".
    """
    result = {"json": None, "latex": None}

    json_match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
    if json_match:
        try:
            result["json"] = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            result["json"] = {"error": "Invalid JSON format returned"}

    latex_match = re.search(r"```latex\s*(.*?)\s*```", response_text, re.DOTALL)
    if latex_match:
        result["latex"] = latex_match.group(1)
    else:
        result["latex"] = response_text.strip()

    return json.dumps(result)


def _extract_prompt_and_media(contents: Any) -> tuple[str, List[str], List[str]]:
    """
    Extracts text, materializes image binary parts into temporary files for multimodal inspection,
    and extracts text from PDF binary parts using `load_book` so local/CLI runners receive full
    manuscript and visual context.
    Returns (prompt_text, image_paths, temp_files_to_cleanup).
    """
    import tempfile

    if isinstance(contents, str):
        return contents, [], []

    parts_text: List[str] = []
    image_paths: List[str] = []
    temp_files: List[str] = []

    items = contents if isinstance(contents, (list, tuple)) else [contents]
    for item in items:
        if isinstance(item, str):
            parts_text.append(item)
        elif hasattr(item, "text") and item.text is not None:
            parts_text.append(str(item.text))
        elif isinstance(item, dict) and "text" in item:
            parts_text.append(str(item["text"]))
        elif hasattr(item, "mime_type") and item.mime_type:
            mime = str(item.mime_type).lower()
            raw_bytes = getattr(item, "data", None)
            if mime == "application/pdf" and raw_bytes:
                try:
                    from utils.pdf_utils import load_book

                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as tmp_pdf:
                        tmp_pdf.write(raw_bytes)
                        tmp_pdf_path = tmp_pdf.name
                    temp_files.append(tmp_pdf_path)
                    extracted_pdf_text = load_book(tmp_pdf_path, min_size=1)
                    if extracted_pdf_text and not extracted_pdf_text.startswith("Error"):
                        parts_text.append(
                            f"--- Attached PDF Document Text ---\n{extracted_pdf_text}"
                        )
                    else:
                        parts_text.append(f"[Attached PDF file: {tmp_pdf_path}]")
                except Exception:
                    parts_text.append("[Attached binary part: application/pdf]")
            elif mime.startswith("image/") and raw_bytes:
                ext = ".png" if "png" in mime else ".jpg"
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=ext, delete=False
                    ) as tmp_img:
                        tmp_img.write(raw_bytes)
                        tmp_img_path = tmp_img.name
                    image_paths.append(tmp_img_path)
                    temp_files.append(tmp_img_path)
                except Exception:
                    parts_text.append(f"[Attached binary part: {mime}]")
            else:
                parts_text.append(f"[Attached binary part: {mime}]")
        else:
            parts_text.append(str(item))

    return "\n\n".join(parts_text), image_paths, temp_files


def _contents_to_prompt_text(contents: Any) -> str:
    prompt_text, _, temp_files = _extract_prompt_and_media(contents)
    for tf in temp_files:
        try:
            if os.path.exists(tf):
                os.remove(tf)
        except Exception:
            pass
    return prompt_text


def call_gemini_with_contents(
    contents: Any,
    model_name: str = "inherit",
    max_retries: int = 3,
    base_interval_sec: int = 1,
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[dict] = None,
    check_parsed_response_not_none: bool = True,
):
    """
    Routes content generation through the registered Antigravity workflow agent runner
    (no external google-genai API key required).
    """
    if generation_configs is None:
        generation_configs = {}

    if _antigravity_agent_runner is None:
        raise RuntimeError(
            "External Gemini API calls are disabled. Run BookOrchestra natively inside Antigravity "
            "using the `run_workflow` tool with `ScriptPath='workflows/book_orchestra_workflow.py'`, "
            "or register an Antigravity runner via `set_antigravity_agent_runner(...)`."
        )

    prompt_text, image_paths, temp_files = _extract_prompt_and_media(contents)
    system_instruction = generation_configs.get("system_instruction")

    try:
        raw_response = _antigravity_agent_runner(
            prompt=prompt_text,
            system_instruction=system_instruction,
            model_name=model_name,
            images=image_paths or None,
            generation_configs=generation_configs,
        )
    finally:
        for tf in temp_files:
            try:
                if os.path.exists(tf):
                    os.remove(tf)
            except Exception:
                pass
    parsed_response = (
        result_parsing_func(raw_response)
        if result_parsing_func is not None
        else raw_response
    )

    if check_parsed_response_not_none:
        assert (
            parsed_response is not None
        ), f"Parsed response is None. Raw response:\n{raw_response}"

    return {
        "raw_response": raw_response,
        "parsed_response": parsed_response,
    }


def call_gemini_with_text_prompt(
    prompt: str,
    model_name: str = "inherit",
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[dict] = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 3,
    base_interval_sec: int = 1,
):
    contents = [types.Part(text=prompt)]
    return call_gemini_with_contents(
        contents=contents,
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=generation_configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
    )


def call_gemini_with_images(
    prompt: str,
    images: list[str],
    model_name: str = "inherit",
    result_parsing_func: Callable = parse_gemini_json_results,
    generation_configs: Optional[dict] = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 3,
    base_interval_sec: int = 1,
):
    if generation_configs is None:
        generation_configs = {}

    if _antigravity_agent_runner is not None:
        system_instruction = generation_configs.get("system_instruction")
        raw_response = _antigravity_agent_runner(
            prompt=prompt,
            system_instruction=system_instruction,
            model_name=model_name,
            images=images,
            generation_configs=generation_configs,
        )
        parsed_response = (
            result_parsing_func(raw_response)
            if result_parsing_func is not None
            else raw_response
        )
        if check_parsed_response_not_none:
            assert parsed_response is not None
        return {
            "raw_response": raw_response,
            "parsed_response": parsed_response,
        }

    llm_contents = [types.Part.from_text(text=prompt)]
    for image_path in images or []:
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            llm_contents.append(
                types.Part.from_bytes(data=img_bytes, mime_type="image/png")
            )

    return call_gemini_with_contents(
        contents=llm_contents,
        model_name=model_name,
        generation_configs=generation_configs,
        result_parsing_func=result_parsing_func,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
    )


def _fallback_diagram_png_base64(
    width: int = 160, height: int = 90, prompt: str = ""
) -> str:
    """
    Renders a labeled Matplotlib schematic block diagram when matplotlib is available,
    or falls back to a minimal valid PNG base64 image.
    """
    if prompt:
        try:
            import io
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches

            fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 6)
            ax.axis("off")

            lines = [ln.strip() for ln in str(prompt).splitlines() if ln.strip()]
            title_line = lines[0][:64] if lines else "System Architecture Diagram"
            ax.text(
                5.0,
                5.4,
                title_line,
                ha="center",
                va="center",
                fontsize=12,
                fontweight="bold",
                color="#0f172a",
            )

            stages = ["Ingress / API", "Control & Routing", "Core Engine / Workers"]
            x_positions = [1.0, 4.0, 7.0]
            for idx, (stage, x0) in enumerate(zip(stages, x_positions)):
                box = mpatches.FancyBboxPatch(
                    (x0, 2.2),
                    2.2,
                    1.6,
                    boxstyle="round,pad=0.15",
                    edgecolor="#2563eb",
                    facecolor="#eff6ff",
                    linewidth=1.5,
                )
                ax.add_patch(box)
                ax.text(
                    x0 + 1.1,
                    3.0,
                    stage,
                    ha="center",
                    va="center",
                    fontsize=9,
                    fontweight="bold",
                    color="#1e3a8a",
                )
                if idx < len(stages) - 1:
                    ax.annotate(
                        "",
                        xy=(x_positions[idx + 1] - 0.1, 3.0),
                        xytext=(x0 + 2.3, 3.0),
                        arrowprops=dict(arrowstyle="->", color="#475569", lw=1.8),
                    )

            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
            plt.close(fig)
            data = buf.getvalue()
            if data:
                return base64.b64encode(data).decode("utf-8")
        except Exception:
            pass

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


def generate_image_with_gemini(
    model_name: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    generation_configs: Optional[dict] = None,
    max_retries: int = 3,
    base_interval_sec: int = 1,
    save_path: Optional[str] = None,
) -> str:
    """
    Generates an image via the Antigravity image-generator runner (or renders a
    labeled schematic diagram PNG base64 when no external image API is configured).
    """
    img_base64 = None
    if _antigravity_image_runner is not None:
        img_base64 = _antigravity_image_runner(
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            model_name=model_name,
            generation_configs=generation_configs or {},
        )
    if not img_base64:
        img_base64 = _fallback_diagram_png_base64(prompt=prompt)

    if save_path and img_base64:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, "wb") as fh:
                fh.write(base64.b64decode(img_base64))
        except Exception as e:
            print(f"Warning: Failed to save image to {save_path}: {e}")

    return img_base64
