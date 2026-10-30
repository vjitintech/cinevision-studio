#!/usr/bin/env python3
"""Regenerate frontend/src/local/engine/data.ts from the Python concept engine so both editions share one source of truth.
    python scripts/gen_engine_data.py"""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from cinevision.concepts import builder, geometry as G  # noqa: E402
from cinevision.concepts.archetypes import ARCHETYPES, BUDGET_ORDER  # noqa: E402
from cinevision.concepts.lighting import FIXTURES  # noqa: E402


def conv(o):
    if isinstance(o, tuple):
        return list(o)
    raise TypeError


out = "// GENERATED from backend/cinevision/concepts/{archetypes,lighting,builder,geometry}.py by scripts/gen_engine_data.py — do not edit.\n"
out += "export const ARCHETYPES: Record<string, any> = " + json.dumps(ARCHETYPES, indent=1, ensure_ascii=False, default=conv) + ";\n"
out += "export const BUDGET_ORDER = " + json.dumps(BUDGET_ORDER) + ";\n"
out += "export const FIXTURES: Record<string, Record<string, string>> = " + json.dumps(FIXTURES, indent=1, ensure_ascii=False) + ";\n"
out += "export const RIGS: Record<string, Record<string, string>> = " + json.dumps(builder.RIGS, indent=1, ensure_ascii=False) + ";\n"
out += "export const SENSORS: Record<string, any> = " + json.dumps(G.SENSORS) + ";\n"
out += "export const SHOT_SIZES: string[] = " + json.dumps(G.SHOT_SIZES) + ";\n"
out += "export const FRAMING_HEIGHT_M: Record<string, number> = " + json.dumps(G.FRAMING_HEIGHT_M) + ";\n"
out += "export const SUBJECT_REF_HEIGHT: Record<string, number> = " + json.dumps(G.SUBJECT_REF_HEIGHT) + ";\n"
out += "export const ANGLES: Record<string, [number, number]> = " + json.dumps({k: list(v) for k, v in G.ANGLES.items()}) + ";\n"
(root / "frontend/src/local/engine/data.ts").write_text(out)
print("wrote frontend/src/local/engine/data.ts")
