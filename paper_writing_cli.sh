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

# Backward-compatible wrapper calling BookOrchestra CLI
PYTHONUNBUFFERED=1 python3 paper_writing_cli.py \
  --raw_materials_dir examples/distributed_llm_systems_book/raw_materials \
  --latex_template_dir templates/oreilly_style_book \
  --output_dir ./book_output_example \
  --use_plotting true
