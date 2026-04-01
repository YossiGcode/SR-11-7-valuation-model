"""
tests/test_config_loader.py

Pytest suite for modules/config_loader.py.

Run from the project root:
    .venv/Scripts/pytest tests/test_config_loader.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from modules.config_loader import load_config, get_domain_config

# ---------------------------------------------------------------------------
# Resolved paths to the real config files (used in happy-path tests)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_YAML_PATH    = _PROJECT_ROOT / "config" / "validation_config.yaml"
_SCHEMA_PATH  = _PROJECT_ROOT / "config" / "schema" / "validation.schema.json"


# ---------------------------------------------------------------------------
# Helpers — build minimal valid / invalid YAML documents on disk
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, data: dict, filename: str = "cfg.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _minimal_valid_config() -> dict:
    """Return the smallest document that satisfies the schema."""
    return {
        "data_integrity": {
            "valid_states": ["PASS", "FAIL", "SKIPPED", "N_A"],
            "state_type":   "validation",
            "missing":      {"default_fail_pct": 0.0},
            "duplicates":   {"allow_exact_duplicates": False},
            "dtypes":       {"enforce_expected_types": True},
            "skip_reasons": {"missing_expected_types": "no expected_types"},
        },
        "ai_audit": {
            "valid_states":    ["PASS", "FAIL", "SKIPPED", "N_A"],
            "state_type":      "validation",
            "extraction":      {"record_is_approximate": True},
            "tolerances":      {"revenue": {"rel_tol": 0.005, "abs_tol": 0.01}},
            "notes_templates": {"deterministic_fail": "Failed tolerance check."},
        },
        "stress_testing": {
            "valid_states":  ["STABLE", "SENSITIVE", "BREACH"],
            "state_type":    "elasticity",
            "default_bands": {"stable_max_pct": 5.0, "sensitive_max_pct": 15.0},
            "assumption_limits": {
                "discount_rate": {"stable_max_pct": 5.0, "sensitive_max_pct": 20.0}
            },
        },
    }


# ---------------------------------------------------------------------------
# Test 1 — Valid config loads successfully and returns a dict
# ---------------------------------------------------------------------------

def test_load_config_returns_dict():
    """load_config with the real project files returns a non-empty dict."""
    config = load_config(yaml_path=_YAML_PATH, schema_path=_SCHEMA_PATH)

    assert isinstance(config, dict), "load_config must return a dict"
    assert len(config) > 0, "Returned config must not be empty"


# ---------------------------------------------------------------------------
# Test 2 — get_domain_config returns correct valid_states for stress_testing
# ---------------------------------------------------------------------------

def test_get_domain_stress_testing_valid_states():
    """
    get_domain_config('stress_testing') must return the three elasticity
    states defined in the canonical config: STABLE, SENSITIVE, BREACH.
    """
    domain = get_domain_config(
        "stress_testing",
        yaml_path=_YAML_PATH,
        schema_path=_SCHEMA_PATH,
    )

    assert domain["valid_states"] == ["STABLE", "SENSITIVE", "BREACH"]


# ---------------------------------------------------------------------------
# Test 3 — Invalid state vocabulary raises ValidationError
# ---------------------------------------------------------------------------

def test_invalid_state_type_raises_validation_error(tmp_path: Path):
    """
    state_type must equal 'elasticity'.  Any other value must cause
    jsonschema.ValidationError to be raised by load_config.
    """
    from jsonschema import ValidationError

    bad_config = _minimal_valid_config()
    bad_config["stress_testing"]["state_type"] = "linear"   # violates const: elasticity

    cfg_path = _write_yaml(tmp_path, bad_config)

    with pytest.raises(ValidationError):
        load_config(yaml_path=cfg_path, schema_path=_SCHEMA_PATH)


# ---------------------------------------------------------------------------
# Test 4 — Missing required namespace raises ValidationError
# ---------------------------------------------------------------------------

def test_missing_required_namespace_raises_validation_error(tmp_path: Path):
    """
    Omitting 'ai_audit' (a required top-level key) must cause
    jsonschema.ValidationError to be raised by load_config.
    """
    from jsonschema import ValidationError

    incomplete = _minimal_valid_config()
    del incomplete["ai_audit"]

    cfg_path = _write_yaml(tmp_path, incomplete)

    with pytest.raises(ValidationError):
        load_config(yaml_path=cfg_path, schema_path=_SCHEMA_PATH)
