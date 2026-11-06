"""Every review language must translate every finding and keep every {placeholder} unchanged."""
import importlib
import re

import pytest

from cinevision.director import texts as en
from cinevision.director.i18n import LANGUAGES, pack

PH = re.compile(r"\{(\w+)\}")
OTHER = [c for c in LANGUAGES if c != "en"]


@pytest.mark.parametrize("lang", OTHER)
def test_language_is_complete_and_placeholders_match(lang):
    mod = importlib.import_module(f"cinevision.director.i18n.{lang}")
    for code, src in en.FINDINGS.items():
        assert code in mod.F, f"{lang}: missing {code}"
        for key, txt in zip(("obs", "sug", "why"), mod.F[code]):
            if src.get(key):
                assert txt, f"{lang}:{code}.{key} empty"
                assert set(PH.findall(txt)) == set(PH.findall(src[key])), f"{lang}:{code}.{key} placeholders"
    for group in ("DEPARTMENTS", "STATUS", "VERDICT", "PACE_ADVICE", "EDIT_STEPS"):
        src, tr = getattr(en, group), getattr(mod, group)
        assert set(tr) == set(src), f"{lang}: {group} keys"
        for k in src:
            assert set(PH.findall(tr[k])) == set(PH.findall(src[k])), f"{lang}:{group}.{k}"
    assert set(mod.EDIT_OPTIONS) == set(en.EDIT_OPTIONS)
    assert set(PH.findall(mod.SUMMARY["text"])) == {"good", "improve", "problem"}
    p = pack(lang)
    assert p["FINDINGS"]["snd.quiet"]["obs"] != en.FINDINGS["snd.quiet"]["obs"]


def test_app_labels_translated_with_placeholders():
    import json
    from pathlib import Path
    fe = Path(__file__).resolve().parents[2] / "frontend" / "src"
    en = dict(re.findall(r'(\w+): "((?:[^"\\]|\\.)*)"', (fe / "i18n-strings.ts").read_text(encoding="utf-8")))
    ui = json.loads((fe / "i18n-ui.json").read_text(encoding="utf-8"))
    assert set(ui) == set(OTHER)
    for lang, d in ui.items():
        assert set(d) == set(en), (lang, set(en) ^ set(d))
        for k, v in d.items():
            assert v.strip() and set(PH.findall(v)) == set(PH.findall(en[k])), (lang, k)


def test_vocabulary_aligned_and_applied():
    from cinevision.director.i18n import vocab
    from cinevision.director.render import _tr_value
    for lang in OTHER:
        assert len(vocab.V[lang]) == len(vocab.TERMS), lang
        assert all(x.strip() for x in vocab.V[lang]), lang
    assert _tr_value("neutral / observational", "fr") == "neutre / observateur"
    assert _tr_value("medium close-up", "es") == "primer plano medio"          # longest term wins over "medium"
    assert _tr_value("zoom in ×2, pan right", "de") == "Zoom rein ×2, Schwenk nach rechts"
    assert _tr_value("long take / unhurried, very still", "hi") == "लंबा टेक / इत्मीनान, बहुत स्थिर"



@pytest.mark.parametrize("lang", OTHER)
def test_assembly_text_complete(lang):
    mod = importlib.import_module(f"cinevision.director.i18n.{lang}")
    assert set(mod.ASSEMBLY) == set(en.ASSEMBLY), lang
    for k, v in en.ASSEMBLY.items():
        assert mod.ASSEMBLY[k].strip() and set(PH.findall(mod.ASSEMBLY[k])) == set(PH.findall(v)), (lang, k)
    assert set(mod.CUES) == set(en.CUES) and all(mod.CUES.values())


@pytest.mark.parametrize("lang", OTHER)
def test_exercises_translated(lang):
    mod = importlib.import_module(f"cinevision.director.i18n.{lang}")
    assert set(mod.EXERCISES) == set(en.EXERCISES) and all(v.strip() for v in mod.EXERCISES.values())
