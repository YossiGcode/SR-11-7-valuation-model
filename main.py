"""
main.py — SR 11-7 Model Validation Engine

Orchestrates all validation modules and builds the master results bundle.
Writes the final SR 11-7 Validation Ledger to disk via write_ledger().
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from modules.data_integrity import run_integrity_checks
from modules.stress_tester import run_stress_tests, dcf_model
from modules.ai_auditor import evaluate_claims
from modules.ledger_writer import build_master_results, flatten_results, write_ledger


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

OUTPUT_DIR  = Path("output")
LEDGER_PATH = OUTPUT_DIR / "sr117_validation_ledger.xlsx"

# Canonical sheet order — module grouping preserved for MRO review
SHEET_ORDER = [
    # Module 1 — Data Integrity
    "Data_Integrity__missing_values",
    "Data_Integrity__duplicate_rows",
    "Data_Integrity__numeric_types",
    # Module 2 — Stress Tester (OAT sensitivity; no N/A path by design)
    "Stress_Tester__oat_sensitivity",
    # Module 3 — AI Auditor
    "AI_Audit__claims",
]


# ---------------------------------------------------------------------------
# Synthetic inputs
# ---------------------------------------------------------------------------

def _make_integrity_input() -> tuple[pd.DataFrame, list[str]]:
    """Small DataFrame with one missing value and one duplicate row."""
    df = pd.DataFrame(
        {
            "revenue": [100.0, 200.0, None, 400.0, 200.0],
            "year":    [2020,  2021,  2022, 2023,  2021],
            "ticker":  ["AAPL", "AAPL", "AAPL", "AAPL", "AAPL"],
        }
    )
    numeric_cols = ["revenue", "year"]
    return df, numeric_cols


def _make_stress_inputs() -> tuple[dict[str, float], dict[str, float]]:
    """Base assumptions and standard deviations for the DCF model."""
    base_assumptions: dict[str, float] = {
        "free_cash_flow":      500_000.0,
        "growth_rate":         0.05,
        "discount_rate":       0.10,
        "terminal_growth_rate": 0.02,
    }
    std_devs: dict[str, float] = {
        "free_cash_flow":      50_000.0,
        "growth_rate":         0.01,
        "discount_rate":       0.02,
        "terminal_growth_rate": 0.005,
    }
    return base_assumptions, std_devs


def _make_audit_inputs() -> tuple[list[dict], dict]:
    """Mock claims list and source truth dict."""
    claims = [
        {
            "claim_text":       "Revenue was $500,000 in base year.",
            "source_key":       "revenue_base",
            "extracted_value":  500_000.0,
            "unit":             "usd",
            "confidence":       1.0,
        },
        {
            "claim_text":       "Discount rate is approximately 12%.",
            "source_key":       "discount_rate",
            "extracted_value":  0.12,
            "unit":             "pct",
            "confidence":       0.8,
        },
        {
            "claim_text":       "The company has strong brand moats.",
            "source_key":       None,
            "extracted_value":  None,
            "unit":             None,
            "confidence":       None,
        },
    ]
    source_truth: dict = {
        "revenue_base":  500_000.0,
        "discount_rate": 0.10,
    }
    return claims, source_truth


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_validation() -> dict[str, pd.DataFrame]:
    """
    Run all three validation modules, bundle results, write the ledger,
    and return the flattened results dict for downstream use.

    Returns
    -------
    dict[str, pd.DataFrame]
        Flat dict keyed as ``Module__check_name``.  Every DataFrame carries
        a ``state`` column (PASS / FAIL / N/A or STABLE / SENSITIVE / BREACH)
        and a ``state_type`` column (validation or elasticity).
    """
    # Module 1 — Data Integrity ------------------------------------------
    integrity_df, numeric_cols = _make_integrity_input()
    data_integrity_results: dict[str, pd.DataFrame] = run_integrity_checks(
        df=integrity_df,
        numeric_cols=numeric_cols,
    )

    # Module 2 — Stress Testing ------------------------------------------
    base_assumptions, std_devs = _make_stress_inputs()
    stress_test_results: dict[str, pd.DataFrame] = run_stress_tests(
        model_fn=dcf_model,
        base_assumptions=base_assumptions,
        std_devs=std_devs,
    )

    # Module 3 — AI Auditor ----------------------------------------------
    claims, source_truth = _make_audit_inputs()
    ai_audit_results: dict[str, pd.DataFrame] = evaluate_claims(
        claims=claims,
        source_truth=source_truth,
    )

    # Bundle + flatten ---------------------------------------------------
    master_results = build_master_results(
        data_integrity_results=data_integrity_results,
        stress_test_results=stress_test_results,
        ai_audit_results=ai_audit_results,
    )
    flat_results: dict[str, pd.DataFrame] = flatten_results(master_results)

    # Write ledger — single-pass in-memory, one disk write on exit -------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = write_ledger(
        flat_results,
        output_path=str(LEDGER_PATH),
        sheet_order=SHEET_ORDER,
    )
    print(f"[ledger_writer] Ledger written → {ledger_path}")

    return flat_results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    flat = run_validation()

    print("\n=== SR 11-7 Validation complete. Flat result keys: ===")
    all_pass = True
    for key, df in flat.items():
        if "state" in df.columns:
            statuses  = df["state"].value_counts().to_dict()
            has_fail  = "FAIL" in statuses or "BREACH" in statuses
            all_pass  = all_pass and not has_fail
            indicator = "✗" if has_fail else "✓"
        else:
            statuses  = {"(no state col)": len(df)}
            indicator = "?"
        print(f"  {indicator}  {key:<45} rows={len(df):>3}  {statuses}")

    print()
    if all_pass:
        print("RESULT: ALL CHECKS PASS — model cleared for SR 11-7 sign-off.")
    else:
        print("RESULT: ONE OR MORE CHECKS FAILED — review ledger before sign-off.")

    sys.exit(0 if all_pass else 1)