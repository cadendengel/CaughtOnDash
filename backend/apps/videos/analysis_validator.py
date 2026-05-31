from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "analysis_schema.json"


def load_schema() -> dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_analysis(payload: Any) -> None:
    schema = load_schema()
    jsonschema.validate(instance=payload, schema=schema)
