from pathlib import Path

import pytest

from portfolio.config import ConfigError, class_weights, load_config, load_targets

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_example_files_load():
    cfg = load_config(EXAMPLES / "config.yaml")
    targets = load_targets(EXAMPLES / "targets.yaml")
    assert cfg.base_currency == "GBP"
    assert cfg.abs_drift == pytest.approx(0.05)
    assert cfg.rel_drift == pytest.approx(0.25)
    assert cfg.cache_dir == EXAMPLES / "prices_cache"
    assert targets.cash_weight == pytest.approx(0.02)
    assert "CASH" not in targets.tickers
    assert sum(class_weights(targets).values()) == pytest.approx(1.0)


def test_weights_must_sum_to_100(tmp_path):
    p = write(tmp_path, "t.yaml", "holdings:\n  A: {weight: 60, asset_class: equity}\n  B: {weight: 30, asset_class: bonds}\n")
    with pytest.raises(ConfigError, match="sum to 90"):
        load_targets(p)


def test_asset_classes_must_match_holdings(tmp_path):
    p = write(
        tmp_path,
        "t.yaml",
        "asset_classes: {equity: 50, bonds: 50}\n"
        "holdings:\n  A: {weight: 60, asset_class: equity}\n  B: {weight: 40, asset_class: bonds}\n",
    )
    with pytest.raises(ConfigError, match="equity"):
        load_targets(p)


def test_bad_base_currency(tmp_path):
    p = write(tmp_path, "c.yaml", "base_currency: JPY\n")
    with pytest.raises(ConfigError, match="base_currency"):
        load_config(p)


def test_defaults_for_empty_config(tmp_path):
    cfg = load_config(write(tmp_path, "c.yaml", ""))
    assert cfg.base_currency == "GBP"
    assert cfg.max_position == pytest.approx(0.25)
