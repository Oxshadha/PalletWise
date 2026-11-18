"""Writes the API's OpenAPI document; Pydantic models are the source of truth (baseline 5.1)."""

import json
import sys
from pathlib import Path

from palletwise.api.app import app

target = Path(sys.argv[1] if len(sys.argv) > 1 else "packages/contracts/openapi.json")
target.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
