"""
stress_tester.py — Module 2
SR 11-7 Model Validation Engine

Performs assumption sensitivity analysis on financial models (DCF, earnings).
Uses one-at-a-time (OAT) methodology: holds all assumptions at base values,
perturbs one assumption across a defined range, and records the output delta.

Returns results as a dict of DataFrames compatible with ledger_writer.py.
Each DataFrame contains a 'status' column with 'STABLE', 'SENSITIVE', or 'BREACH'.
All thresholds are driven exclusively by config/validation_config.yaml via
modules.config_loader — no numbers are hardcoded in this file.
"""

import numpy as np
import pandas as pd
from typing import Any, Callable, Dict, List, Optional

from modules.config_loader import get_domain_config


# ---------------------------------------------------------------------------
# Built-in model (replaceable with any callable)
# ---------------------------------------------------------------------------

def dcf_model(
    free_cash_flow: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth_rate: float,
    years: int = 5,
) -> float:
    """
    Simple DCF valuation model.

    Discounts projected free cash flows over `years` and adds a terminal value.

    Parameters
    ----------
    free_cash_flow : float
        Base-year free cash flow.
    growth_rate : float
        Annual FCF growth rate (e.g. 0.05 for 5%).
    discount_rate : float
        WACC / required rate of return (e.g. 0.10 for 10%).
    terminal_growth_rate : float
        Perpetuity growth rate for terminal value (e.g. 0.02).
    years : int
        Explicit forecast horizon (default 5).

    Returns
    -------
    float
        Estimated intrinsic value (sum of PV of FCFs + PV of terminal value).
    """
    pv_fcfs = sum(
        free_cash_flow * (1 + growth_rate) ** t / (1 + discount_rate) ** t
        for t in range(1, years + 1)
    )
    terminal_fcf = free_cash_flow * (1 + growth_rate) ** years * (1 + terminal_growth_rate)
    terminal_value = terminal_fcf / (discount_rate - terminal_growth_rate)
    pv_terminal = terminal_value / (1 + discount_rate) ** years
    return pv_fcfs + pv_terminal


# ---------------------------------------------------------------------------
# Elasticity status helper
# ---------------------------------------------------------------------------

def _elasticity_status(abs_delta_pct: float, bands: Dict[str, float]) -> str:
    """
    Map an absolute percentage deviation to an elasticity label.

    Parameters
    ----------
    abs_delta_pct : float
        Absolute value of the percentage deviation from base output.
    bands : dict
        Must contain 'stable_max_pct' and 'sensitive_max_pct' (both floats).

    Returns
    -------
    str
        'STABLE'    — abs_delta_pct <= stable_max_pct
        'SENSITIVE' — stable_max_pct < abs_delta_pct <= sensitive_max_pct
        'BREACH'    — abs_delta_pct > sensitive_max_pct
    """
    if abs_delta_pct <= bands["stable_max_pct"]:
        return "STABLE"
    if abs_delta_pct <= bands["sensitive_max_pct"]:
        return "SENSITIVE"
    return "BREACH"


# ---------------------------------------------------------------------------
# Sensitivity helpers
# ---------------------------------------------------------------------------

def _shock_range(base: float, std_dev: float, n_steps: int = 5) -> np.ndarray:
    """
    Build a symmetric shock range centered on `base`.

    Generates `n_steps` evenly-spaced values spanning ±1 standard deviation
    around the base assumption value.

    Parameters
    ----------
    base : float
        Base (central) value of the assumption.
    std_dev : float
        Standard deviation used to define the shock magnitude.
    n_steps : int
        Number of points in the range (default 5; should be odd for symmetry).

    Returns
    -------
    np.ndarray
        Array of assumption values to test.
    """
    return np.linspace(base - std_dev, base + std_dev, n_steps)


def run_sensitivity(
    model_fn: Callable[..., float],
    base_assumptions: Dict[str, float],
    std_devs: Dict[str, float],
    config: Dict[str, Any],
    assumption_names: Optional[List[str]] = None,
    n_steps: int = 5,
) -> pd.DataFrame:
    """
    Run one-at-a-time (OAT) sensitivity analysis on a financial model.

    For each assumption in `assumption_names`, holds all others at base values
    and varies the target assumption across ±1 SD in `n_steps` increments.
    Records the output at each shock level and computes the percentage deviation
    from the base-case output.

    Status is assigned per the elasticity bands in `config`:
      - Per-assumption limits are read from config['assumption_limits'][assumption].
      - If the assumption has no entry in assumption_limits, config['default_bands']
        is used as the fallback.

    Parameters
    ----------
    model_fn : Callable
        The model function to stress. Must accept keyword arguments matching
        keys in `base_assumptions`.
    base_assumptions : dict
        Mapping of assumption name → base value. All passed as kwargs to `model_fn`.
    std_devs : dict
        Mapping of assumption name → standard deviation for shock range.
    config : dict
        Validated stress_testing domain config from config_loader.
    assumption_names : list of str, optional
        Which assumptions to stress. Defaults to all keys in `base_assumptions`.
    n_steps : int
        Number of shock levels per assumption (default 5).

    Returns
    -------
    pd.DataFrame
        Columns: assumption, shocked_value, base_output, stressed_output,
                 delta, delta_pct, status
        status values: STABLE | SENSITIVE | BREACH
    """
    if assumption_names is None:
        assumption_names = list(base_assumptions.keys())

    assumption_limits = config["assumption_limits"]
    default_bands = config["default_bands"]
    state_type: str = config.get("state_type", "elasticity")
    base_output = model_fn(**base_assumptions)
    records = []

    for assumption in assumption_names:
        bands = assumption_limits.get(assumption, default_bands)
        shock_values = _shock_range(
            base=base_assumptions[assumption],
            std_dev=std_devs[assumption],
            n_steps=n_steps,
        )
        for shocked_val in shock_values:
            stressed_kwargs = {**base_assumptions, assumption: shocked_val}
            try:
                stressed_output = model_fn(**stressed_kwargs)
                delta = stressed_output - base_output
                delta_pct = (delta / abs(base_output)) * 100 if base_output != 0 else np.nan
                state = (
                    _elasticity_status(abs(delta_pct), bands)
                    if not np.isnan(delta_pct)
                    else "BREACH"
                )
            except Exception:
                stressed_output = np.nan
                delta = np.nan
                delta_pct = np.nan
                state = "BREACH"

            records.append(
                {
                    "assumption": assumption,
                    "shocked_value": round(shocked_val, 6),
                    "base_output": round(base_output, 4),
                    "stressed_output": round(stressed_output, 4) if not np.isnan(stressed_output) else np.nan,
                    "delta": round(delta, 4) if not np.isnan(delta) else np.nan,
                    "delta_pct": round(delta_pct, 2) if not np.isnan(delta_pct) else np.nan,
                    "state": state,
                    "state_type": state_type,
                }
            )

    return pd.DataFrame(records)


def run_one_sd_summary(
    model_fn: Callable[..., float],
    base_assumptions: Dict[str, float],
    std_devs: Dict[str, float],
    config: Dict[str, Any],
    assumption_names: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Compute the model output at exactly ±1 SD for each assumption.

    A concise summary table — one row per assumption — showing the output when
    each assumption is shocked to −1 SD and +1 SD from base.
    Useful for a quick materiality ranking of assumptions.

    Status is assigned per the elasticity bands in `config`:
      - Per-assumption limits are read from config['assumption_limits'][assumption].
      - If the assumption has no entry in assumption_limits, config['default_bands']
        is used as the fallback.

    Parameters
    ----------
    model_fn : Callable
        The model function to stress.
    base_assumptions : dict
        Mapping of assumption name → base value.
    std_devs : dict
        Mapping of assumption name → standard deviation.
    config : dict
        Validated stress_testing domain config from config_loader.
    assumption_names : list of str, optional
        Which assumptions to stress. Defaults to all keys in `base_assumptions`.

    Returns
    -------
    pd.DataFrame
        Columns: assumption, base_output, output_minus_1sd, output_plus_1sd,
                 delta_pct_minus, delta_pct_plus, max_abs_delta_pct, status
        status values: STABLE | SENSITIVE | BREACH
    """
    if assumption_names is None:
        assumption_names = list(base_assumptions.keys())

    state_type: str = config.get("state_type", "elasticity")
    assumption_limits = config["assumption_limits"]
    default_bands = config["default_bands"]
    base_output = model_fn(**base_assumptions)
    records = []

    for assumption in assumption_names:
        bands = assumption_limits.get(assumption, default_bands)
        sd = std_devs[assumption]
        base_val = base_assumptions[assumption]

        out_minus = model_fn(**{**base_assumptions, assumption: base_val - sd})
        out_plus = model_fn(**{**base_assumptions, assumption: base_val + sd})

        delta_pct_minus = ((out_minus - base_output) / abs(base_output)) * 100
        delta_pct_plus = ((out_plus - base_output) / abs(base_output)) * 100
        max_abs_delta = max(abs(delta_pct_minus), abs(delta_pct_plus))
        state = _elasticity_status(max_abs_delta, bands)

        records.append(
            {
                "assumption": assumption,
                "base_output": round(base_output, 4),
                "output_minus_1sd": round(out_minus, 4),
                "output_plus_1sd": round(out_plus, 4),
                "delta_pct_minus": round(delta_pct_minus, 2),
                "delta_pct_plus": round(delta_pct_plus, 2),
                "max_abs_delta_pct": round(max_abs_delta, 2),
                "state": state,
                "state_type": state_type,
            }
        )

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Top-level runner — ledger_writer interface
# ---------------------------------------------------------------------------

def run_stress_tests(
    model_fn: Callable[..., float],
    base_assumptions: Dict[str, float],
    std_devs: Dict[str, float],
    assumption_names: Optional[List[str]] = None,
    n_steps: int = 5,
) -> Dict[str, pd.DataFrame]:
    """
    Run all stress tests and return results in ledger-compatible format.

    Loads the stress_testing policy from config/validation_config.yaml at call
    time.  All thresholds trace back to the YAML — no numbers are hardcoded.

    Parameters
    ----------
    model_fn : Callable
        Financial model function to stress (e.g. dcf_model).
    base_assumptions : dict
        Base-case assumption values keyed by argument name of `model_fn`.
    std_devs : dict
        Standard deviation for each assumption's shock range.
    assumption_names : list of str, optional
        Subset of assumptions to stress. Defaults to all in `base_assumptions`.
    n_steps : int
        Shock levels per assumption in the full sweep (default 5).

    Returns
    -------
    dict of str → pd.DataFrame
        Keys:
          "sensitivity_sweep" — full OAT results (n_assumptions × n_steps rows)
          "one_sd_summary"    — ±1 SD summary (one row per assumption)
        status column values: STABLE | SENSITIVE | BREACH
    """
    config = get_domain_config("stress_testing")

    sensitivity_sweep = run_sensitivity(
        model_fn=model_fn,
        base_assumptions=base_assumptions,
        std_devs=std_devs,
        config=config,
        assumption_names=assumption_names,
        n_steps=n_steps,
    )

    one_sd_summary = run_one_sd_summary(
        model_fn=model_fn,
        base_assumptions=base_assumptions,
        std_devs=std_devs,
        config=config,
        assumption_names=assumption_names,
    )

    return {
        "sensitivity_sweep": sensitivity_sweep,
        "one_sd_summary": one_sd_summary,
    }
