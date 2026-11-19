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

# Example launch script for BookOrchestra (IT & Engineering Book Publishing)
BOOK_DIR="${BOOK_DIR:-examples/distributed_llm_systems_book}"
TEMPLATE_DIR="${TEMPLATE_DIR:-templates/oreilly_style_book}"
OUTPUT_DIR="${OUTPUT_DIR:-./book_output_distributed_llm_systems}"

PYTHONUNBUFFERED=1 python3 book_writing_cli.py \
  --raw_materials_dir "${BOOK_DIR}/raw_materials" \
  --latex_template_dir "${TEMPLATE_DIR}" \
  --output_dir "${OUTPUT_DIR}" \
  --target_audience "Senior Backend, Platform, and ML Systems Engineers" \
  --book_style "Practical Architecture & Production Engineering" \
  --use_plotting true \
  --visual_density high
