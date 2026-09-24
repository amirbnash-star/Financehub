import pytest
import yaml

from portfolio.build import PROFILES, model_targets, risk_profile_issues
from portfolio.config import Config, load_targets


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("base", ["GBP", "EUR"])
def test_model_targets_are_valid_and_fit_profile(tmp_path, profile, base):
    p = tmp_path / "targets.yaml"
    p.write_text(yaml.safe_dump(model_targets(profile, base), sort_keys=False))
    targets = load_targets(p)  # raises if weights do not sum to 100
    assert risk_profile_issues(targets, Config(risk_profile=profile, horizon_years=20)) == []


def test_mismatch_is_reported(tmp_path):
    p = tmp_path / "targets.yaml"
    p.write_text(yaml.safe_dump(model_targets("aggressive", "GBP")))
    issues = risk_profile_issues(load_targets(p), Config(risk_profile="cautious", horizon_years=3))
    assert len(issues) == 2
    assert "outside" in issues[0] and "horizon" in issues[1]
