from __future__ import annotations

import re

from torexlink.constants import SUPPORTED_LOCALES
from torexlink.i18n import UZ, Translator, catalogues

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def test_all_catalogues_have_identical_nonempty_keys() -> None:
    sets = {locale: set(catalogue) for locale, catalogue in catalogues().items()}
    assert set(sets) == set(SUPPORTED_LOCALES)
    for locale, keys in sets.items():
        assert keys == sets["uz"], f"{locale} key mismatch"
        for key, value in catalogues()[locale].items():
            assert isinstance(value, str) and value.strip(), f"empty translation {locale}/{key}"


def test_placeholders_are_consistent() -> None:
    for key, source in UZ.items():
        expected = set(_PLACEHOLDER.findall(source))
        for locale in ("ru", "en"):
            value = catalogues()[locale][key]
            assert set(_PLACEHOLDER.findall(value)) == expected, f"placeholder mismatch {locale}/{key}"


def test_translator_formats_and_falls_back() -> None:
    translator = Translator("uz")
    assert translator.t("home_peer_latency", ms=12) == "12 ms kechikish"
    translator.set_locale("en")
    assert translator.t("home_peer_latency", ms=12) == "12 ms latency"
    translator.set_locale("ru")
    assert "мс" in translator.t("home_peer_latency", ms=12)
    assert translator.t("missing_key") == "missing_key"


def test_unknown_locale_uses_default() -> None:
    translator = Translator("xx")
    assert translator.locale == "uz"
    assert len(translator.available_locales()) == 3


def test_static_translation_keys_used_in_ui_exist() -> None:
    import re as _re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "torexlink"
    key_pattern = _re.compile(r'\bt\(["\']([a-z][a-z0-9_]*)["\']')
    missing: set[str] = set()
    for path in root.rglob("*.py"):
        if path.name == "i18n.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in key_pattern.finditer(text):
            key = match.group(1)
            if key.endswith("_"):
                continue
            if key not in UZ:
                missing.add(f"{path.relative_to(root)}:{key}")
    assert not missing, "Missing translation keys: " + ", ".join(sorted(missing))
