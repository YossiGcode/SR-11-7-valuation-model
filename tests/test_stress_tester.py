"""
tests/test_stress_tester.py

Behavior tests for the config-driven elasticity engine in modules/stress_tester.py.

Coverage:
  1. default_bands fallback for assumptions absent from assumption_limits
  2. Inclusive stable boundary (== stable_max_pct → STABLE)
  3. Inclusive sensitive boundary (== sensitive_max_pct → SENSITIVE)
  4. BREACH is strictly greater-than sensitive_max_pct
  5. run_stress_tests return contract: shape, columns, status vocabulary

Design notes
------------
- Tests 2–4 call _elasticity_status directly — the classification primitive —
  to avoid floating-point accumulation through model → shock → round pipeline.
  Direct tests of the pure function are deterministic and self-documenting.
- Test 1 calls run_sensitivity (which takes config as a parameter) to exercise
  the fallback branch in production code without monkeypatching.
- Test 5 calls run_stress_tests (which calls get_domain_config internally) and
  patches the symbol as imported inside modules.stress_tester, not the source.
"""

from __future__ import annotations

import pandas as pd
import pytest

from modules.stress_tester import _elasticity_status, run_sensitivity, run_stress_tests, dcf_model


# ---------------------------------------------------------------------------
# Shared mock config — passed directly to run_sensitivity in tests 1 and 5.
# assumption_limits covers only "discount_rate"; all other assumptions fall back
# to default_bands.
# ---------------------------------------------------------------------------
_STRESS_CONFIG = {
    "valid_states": ["STABLE", "SENSITIVE", "BREACH"],
    "state_type": "elasticity",
    "default_bands": {"stable_max_pct": 5.0, "sensitive_max_pct": 15.0},
    "assumption_limits": {
        "discount_rate": {"stable_max_pct": 5.0, "sensitive_max_pct": 20.0},
    },
}

# Minimal single-assumption linear model: output == the assumption value.
# Accepts one kwarg whose name matches the assumption being stressed.
def _linear_model(**kwargs) -> float:
    """Return the sole kwarg value (assumes exactly one argument)."""
    return next(iter(kwargs.values()))


# ---------------------------------------------------------------------------
# Test 1 — default_bands fallback for an assumption not in assumption_limits
# ---------------------------------------------------------------------------

def test_default_bands_fallback_for_unmodeled_assumption():
    """
    When an assumption name is absent from assumption_limits, the engine must
    silently fall back to default_bands rather than raising KeyError or
    accidentally using a neighbouring assumption's limits.

    Proof strategy (discriminating assertion):
      std_dev=17 → shock at ±17 from base 100 → abs(delta_pct) = 17%
        - Under default_bands   (sensitive_max_pct=15) : 17 > 15  → BREACH  ✓
        - Under discount_rate   (sensitive_max_pct=20) : 17 ≤ 20  → SENSITIVE ✗
    If every result row is BREACH, default_bands was used.
    """
    result = run_sensitivity(
        model_fn=_linear_model,
        base_assumptions={"unmodeled_shock": 100.0},
        std_devs={"unmodeled_shock": 17.0},   # ±17% deviation
        config=_STRESS_CONFIG,
        assumption_names=["unmodeled_shock"],
        n_steps=2,   # linspace(83, 117, 2) → [83.0, 117.0] → both yield 17% delta
    )

    assert not result.empty, "Result DataFrame must not be empty"
    assert "state" in result.columns, "state column must be present"
    assert (result["state"] == "BREACH").all(), (
        f"Expected BREACH for all rows (default_bands.sensitive_max_pct=15 < 17%); "
        f"got: {result['state'].tolist()}"
    )


# ---------------------------------------------------------------------------
# Test 2 — stable boundary is inclusive (== stable_max_pct → STABLE)
# ---------------------------------------------------------------------------

def test_stable_boundary_exactly_equal_to_stable_max_pct():
    """
    abs(delta_pct) == stable_max_pct must return STABLE, not SENSITIVE.
    The boundary condition `<=` must be inclusive.
    """
    bands = {"stable_max_pct": 5.0, "sensitive_max_pct": 15.0}
    assert _elasticity_status(5.0, bands) == "STABLE", (
        "Boundary value == stable_max_pct must be classified STABLE (inclusive <=)"
    )


# ---------------------------------------------------------------------------
# Test 3 — sensitive boundary is inclusive (== sensitive_max_pct → SENSITIVE)
# ---------------------------------------------------------------------------

def test_sensitive_boundary_exactly_equal_to_sensitive_max_pct():
    """
    abs(delta_pct) == sensitive_max_pct must return SENSITIVE, not BREACH.
    Proves that BREACH requires strictly greater-than, not greater-than-or-equal.
    """
    bands = {"stable_max_pct": 5.0, "sensitive_max_pct": 15.0}
    assert _elasticity_status(15.0, bands) == "SENSITIVE", (
        "Boundary value == sensitive_max_pct must be SENSITIVE (inclusive <=), not BREACH"
    )


# ---------------------------------------------------------------------------
# Test 4 — BREACH when delta_pct strictly exceeds sensitive_max_pct
# ---------------------------------------------------------------------------

def test_breach_when_delta_exceeds_sensitive_max_pct():
    """
    abs(delta_pct) > sensitive_max_pct must return BREACH.
    Uses 15.001 (epsilon above boundary) to eliminate any boundary ambiguity.
    """
    bands = {"stable_max_pct": 5.0, "sensitive_max_pct": 15.0}
    assert _elasticity_status(15.001, bands) == "BREACH", (
        "Value strictly above sensitive_max_pct must be BREACH"
    )


# ---------------------------------------------------------------------------
# Test 5 — run_stress_tests return contract preserved
# ---------------------------------------------------------------------------

def test_return_contract_preserved(monkeypatch):
    """
    run_stress_tests must:
      - return dict[str, pd.DataFrame]
      - expose exactly the keys "sensitivity_sweep" and "one_sd_summary"
      - include all specified columns in each DataFrame
      - emit only STABLE / SENSITIVE / BREACH in the status column
    Patches get_domain_config as imported inside modules.stress_tester, not
    at the source module, so the binding inside stress_tester is replaced.
    """
    monkeypatch.setattr(
        "modules.stress_tester.get_domain_config",
        lambda domain: _STRESS_CONFIG,
    )

    base = {
        "free_cash_flow":      100.0,
        "growth_rate":         0.05,
        "discount_rate":       0.10,
        "terminal_growth_rate": 0.02,
    }
    std_devs = {k: abs(v) * 0.1 for k, v in base.items()}

    result = run_stress_tests(
        model_fn=dcf_model,
        base_assumptions=base,
        std_devs=std_devs,
    )

    # --- Top-level shape ---
    assert isinstance(result, dict), "run_stress_tests must return a dict"
    assert set(result.keys()) == {"sensitivity_sweep", "one_sd_summary"}, (
        f"Unexpected keys: {set(result.keys())}"
    )

    sweep   = result["sensitivity_sweep"]
    summary = result["one_sd_summary"]

    assert isinstance(sweep,   pd.DataFrame), "sensitivity_sweep must be a DataFrame"
    assert isinstance(summary, pd.DataFrame), "one_sd_summary must be a DataFrame"

    # --- Column contracts ---
    sweep_required = {
        "assumption", "shocked_value", "base_output", "stressed_output",
        "delta", "delta_pct", "state", "state_type",
    }
    summary_required = {
        "assumption", "base_output", "output_minus_1sd", "output_plus_1sd",
        "delta_pct_minus", "delta_pct_plus", "max_abs_delta_pct", "state", "state_type",
    }
    assert sweep_required.issubset(set(sweep.columns)), (
        f"sensitivity_sweep missing columns: {sweep_required - set(sweep.columns)}"
    )
    assert summary_required.issubset(set(summary.columns)), (
        f"one_sd_summary missing columns: {summary_required - set(summary.columns)}"
    )

    # --- State vocabulary ---
    valid_states = {"STABLE", "SENSITIVE", "BREACH"}
    assert set(sweep["state"].unique()).issubset(valid_states), (
        f"Unexpected state values in sweep: {set(sweep['state'].unique())}"
    )
    assert set(summary["state"].unique()).issubset(valid_states), (
        f"Unexpected state values in summary: {set(summary['state'].unique())}"
    )
