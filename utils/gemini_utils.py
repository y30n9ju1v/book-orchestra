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
import struct
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
    if image_runner is not None:
        _antigravity_image_runner = image_runner


def get_antigravity_agent_runner() -> Optional[Callable[..., str]]:
    return _antigravity_agent_runner


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


def _contents_to_prompt_text(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    parts_text: List[str] = []
    if isinstance(contents, (list, tuple)):
        for item in contents:
            if isinstance(item, str):
                parts_text.append(item)
            elif hasattr(item, "text") and item.text is not None:
                parts_text.append(str(item.text))
            elif isinstance(item, dict) and "text" in item:
                parts_text.append(str(item["text"]))
            elif hasattr(item, "mime_type") and item.mime_type:
                parts_text.append(f"[Attached binary part: {item.mime_type}]")
    else:
        parts_text.append(str(contents))
    return "\n\n".join(parts_text)


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

    prompt_text = _contents_to_prompt_text(contents)
    system_instruction = generation_configs.get("system_instruction")

    raw_response = _antigravity_agent_runner(
        prompt=prompt_text,
        system_instruction=system_instruction,
        model_name=model_name,
        images=None,
        generation_configs=generation_configs,
    )
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


def _fallback_diagram_png_base64(width: int = 160, height: int = 90) -> str:
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
    Generates an image via the Antigravity image-generator runner (or returns a
    clean placeholder PNG base64 when no external image API is configured).
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
        img_base64 = _fallback_diagram_png_base64()

    if save_path and img_base64:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, "wb") as fh:
                fh.write(base64.b64decode(img_base64))
        except Exception as e:
            print(f"Warning: Failed to save image to {save_path}: {e}")

    return img_base64
