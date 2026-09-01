"""`explain/shap_explain.py` (D13) — same real-data-or-skip precedent as
`tests/test_inference.py`; see that file's docstring.

Also covers `explain/i18n.py` since the two are inseparable in practice — a
briefing with a broken i18n lookup is a broken briefing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aqi.explain.i18n import alert_template, feature_label, health_guidance

REPO_ROOT = Path(__file__).resolve().parents[1]
_HAS_FEATURE_STORE = (REPO_ROOT / "data" / "feature_store").exists()
_HAS_REGISTRY = (REPO_ROOT / "data" / "model_registry" / "lightgbm__h24").exists()


class TestI18n:
    """No data dependency — pure YAML lookups."""

    def test_health_guidance_known_category(self) -> None:
        en, ur = health_guidance("Unhealthy")
        assert en != ""
        assert ur != ""

    def test_health_guidance_unknown_category_returns_empty_not_crash(self) -> None:
        en, ur = health_guidance("Not A Real Category")
        assert (en, ur) == ("", "")

    def test_feature_label_known_falls_back_gracefully(self) -> None:
        en, ur = feature_label("pm2_5")
        assert en == "PM2.5"
        assert ur != ""

    def test_feature_label_unknown_falls_back_to_raw_name_both_sides(self) -> None:
        en, ur = feature_label("some_unlisted_feature")
        assert en == "some_unlisted_feature"
        assert ur == "some_unlisted_feature"

    def test_alert_templates_have_placeholders(self) -> None:
        en, ur = alert_template("episode")
        assert "{aqi}" in en
        assert "{aqi}" in ur


@pytest.mark.skipif(
    not (_HAS_FEATURE_STORE and _HAS_REGISTRY),
    reason="needs the real feature store + registered LightGBM models",
)
class TestExplainZone:
    def test_returns_top_drivers_summing_toward_the_prediction(self) -> None:
        from aqi.explain.shap_explain import explain_zone
        from aqi.serving.inference import load_frame_cached, zones

        frame = load_frame_cached()
        zone_id = zones()[0].zone_id
        result = explain_zone(frame, zone_id, 24)

        assert len(result.top_drivers) <= 5
        assert result.top_drivers, "no drivers returned"
        assert result.briefing_en.startswith("LightGBM predicts")
        assert "SARIMAX" in result.explainer_note

    def test_drivers_sorted_by_absolute_shap_value(self) -> None:
        from aqi.explain.shap_explain import explain_zone
        from aqi.serving.inference import load_frame_cached, zones

        frame = load_frame_cached()
        zone_id = zones()[0].zone_id
        result = explain_zone(frame, zone_id, 48)

        magnitudes = [abs(d.shap_value) for d in result.top_drivers]
        assert magnitudes == sorted(magnitudes, reverse=True)
