"""
ai_auditor.py — Module 3
SR 11-7 Model Validation Engine

Evaluates extracted numerical claims from AI-generated text against verified
source truth.  Tolerances are driven exclusively by config/validation_config.yaml
via modules.config_loader — no threshold numbers are hardcoded in this file.

N/A rendering contract
-----------------------
The YAML uses 'N_A' for schema compliance.  The string "N/A" is written only at
the exact point a DataFrame record is appended inside evaluate_claims.
config_loader never transforms N_A → N/A; ledger_writer never needs to.

Common Envelope contract
------------------------
Every output record carries:
  state      — PASS | FAIL | N/A  (never the YAML token N_A)
  state_type — sourced from config["state_type"] (value: "validation")
"""

import math
from typing import Any, Optional

import pandas as pd

from modules.config_loader import get_domain_config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> float:
    """Safely coerce a value to float."""
    return float(value)


def _resolve_tolerance_key(source_key: str, tolerances: dict) -> Optional[str]:
    """
    Find the best matching tolerance key for a given source_key.

    Strategy: try progressively shorter left-anchored underscore-joined prefixes,
    longest first.  This correctly matches both simple and compound metric names.

    Examples
    --------
    "revenue_2024"     → tries "revenue_2024" (miss) → "revenue"      (hit)
    "discount_rate_q3" → tries "discount_rate_q3" (miss) → "discount_rate" (hit)
    "unknown_metric"   → no match → None

    Parameters
    ----------
    source_key : str
        The claim's source_key field.
    tolerances : dict
        The tolerances sub-dict from the ai_audit domain config.

    Returns
    -------
    str or None
        The matching tolerance key, or None if no policy is defined.
    """
    if source_key in tolerances:
        return source_key
    parts = source_key.split("_")
    for end in range(len(parts) - 1, 0, -1):
        candidate = "_".join(parts[:end])
        if candidate in tolerances:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_claims(
    claims: list[dict],
    source_truth: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    """
    Evaluate extracted numerical claims against verified source truth.

    Tolerances (rel_tol, abs_tol) are resolved per-claim from the ai_audit
    domain config.  If no tolerance policy covers a claim's metric, the row
    is flagged N/A and processing continues — no exception is raised.

    Parameters
    ----------
    claims : list[dict]
        Structured extracted claims.  Each dict may contain:
          - claim_text     : str   — human-readable claim sentence
          - source_key     : str   — key to look up in source_truth and tolerances
          - extracted_value: float — numeric value extracted from AI text
          - unit           : str   — optional unit label
          - confidence     : float — optional confidence score
          - is_approximate : bool  — True if the AI flagged the claim as approximate

    source_truth : dict[str, Any]
        Verified source values keyed by source_key.

    Returns
    -------
    dict[str, pd.DataFrame]
        {
            "claim_audit"   : one row per claim with full audit trail,
            "claim_summary" : single-row aggregate (pass/fail/N/A counts + state)
        }

    Notes
    -----
    State values in claim_audit: PASS | FAIL | N/A
    - PASS / FAIL are determined strictly by math.isclose() using config tolerances.
    - N/A is used for qualitative claims, missing source keys, or unsupported metrics.
    - The is_approximate flag affects notes wording only — never the state verdict.
    - Every row carries a 'state_type' column sourced from the domain config.
    """
    config = get_domain_config("ai_audit")
    tolerances = config["tolerances"]
    notes_templates = config["notes_templates"]
    state_type: str = config["state_type"]

    audit_records: list[dict[str, Any]] = []

    for claim in claims:
        claim_text = claim.get("claim_text", "")
        source_key = claim.get("source_key")
        extracted_value = claim.get("extracted_value")
        unit = claim.get("unit")
        confidence = claim.get("confidence")
        is_approximate: bool = claim.get("is_approximate") is True

        # --- Guard: missing source_key or extracted_value -------------------
        if source_key is None or extracted_value is None:
            audit_records.append(
                {
                    "claim_text": claim_text,
                    "source_key": source_key,
                    "unit": unit,
                    "confidence": confidence,
                    "extracted_value": extracted_value,
                    "source_value": None,
                    "variance": None,
                    "variance_pct": None,
                    "notes": "Qualitative or unresolved claim; no deterministic comparison performed.",
                    "state": "N/A",
                    "state_type": state_type,
                }
            )
            continue

        # --- Guard: source_key not in truth data ----------------------------
        if source_key not in source_truth:
            audit_records.append(
                {
                    "claim_text": claim_text,
                    "source_key": source_key,
                    "unit": unit,
                    "confidence": confidence,
                    "extracted_value": extracted_value,
                    "source_value": None,
                    "variance": None,
                    "variance_pct": None,
                    "notes": f"Source key '{source_key}' not found in provided truth data.",
                    "state": "N/A",
                    "state_type": state_type,
                }
            )
            continue

        # --- Guard: no tolerance policy for this metric ---------------------
        tol_key = _resolve_tolerance_key(source_key, tolerances)
        if tol_key is None:
            audit_records.append(
                {
                    "claim_text": claim_text,
                    "source_key": source_key,
                    "unit": unit,
                    "confidence": confidence,
                    "extracted_value": extracted_value,
                    "source_value": None,
                    "variance": None,
                    "variance_pct": None,
                    "notes": "Unsupported metric: no tolerance policy defined.",
                    "state": "N/A",
                    "state_type": state_type,
                }
            )
            continue

        tol = tolerances[tol_key]
        rel_tol: float = tol["rel_tol"]
        abs_tol: float = tol["abs_tol"]

        truth_value = source_truth[source_key]

        # --- Type coercion --------------------------------------------------
        try:
            extracted_float = _to_float(extracted_value)
            truth_float = _to_float(truth_value)
        except (TypeError, ValueError):
            audit_records.append(
                {
                    "claim_text": claim_text,
                    "source_key": source_key,
                    "unit": unit,
                    "confidence": confidence,
                    "extracted_value": extracted_value,
                    "source_value": truth_value,
                    "variance": None,
                    "variance_pct": None,
                    "notes": "Type mismatch: extracted or source value could not be converted to float.",
                    "state": "FAIL",
                    "state_type": state_type,
                }
            )
            continue

        # --- Variance calculation -------------------------------------------
        variance = extracted_float - truth_float

        if truth_float == 0:
            variance_pct = None
        else:
            variance_pct = (variance / abs(truth_float)) * 100

        # --- Verdict via math.isclose ---------------------------------------
        passed = math.isclose(
            extracted_float,
            truth_float,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
        )
        state = "PASS" if passed else "FAIL"

        # --- Notes construction --------------------------------------------
        if passed:
            notes = (
                f"Claim passed tolerance check "
                f"(tol_key={tol_key}, rel_tol={rel_tol}, abs_tol={abs_tol})."
            )
        elif is_approximate:
            variance_bps = round(abs(extracted_float - truth_float) * 10000, 1)
            notes = notes_templates["approximate_fail"].format(variance_bps=variance_bps)
        else:
            notes = notes_templates["deterministic_fail"]

        audit_records.append(
            {
                "claim_text": claim_text,
                "source_key": source_key,
                "unit": unit,
                "confidence": confidence,
                "extracted_value": extracted_float,
                "source_value": truth_float,
                "variance": round(variance, 6),
                "variance_pct": round(variance_pct, 4) if variance_pct is not None else None,
                "notes": notes,
                "state": state,
                "state_type": state_type,
            }
        )

    # --- Build audit DataFrame ---------------------------------------------
    audit_df = pd.DataFrame(audit_records)

    if audit_df.empty:
        audit_df = pd.DataFrame(
            [
                {
                    "claim_text": None,
                    "source_key": None,
                    "unit": None,
                    "confidence": None,
                    "extracted_value": None,
                    "source_value": None,
                    "variance": None,
                    "variance_pct": None,
                    "notes": "No claims provided.",
                    "state": "N/A",
                    "state_type": state_type,
                }
            ]
        )

    # --- Summary row -------------------------------------------------------
    pass_count = int((audit_df["state"] == "PASS").sum())
    fail_count = int((audit_df["state"] == "FAIL").sum())
    na_count   = int((audit_df["state"] == "N/A").sum())

    if fail_count > 0:
        summary_state = "FAIL"
    elif pass_count > 0:
        summary_state = "PASS"
    else:
        summary_state = "N/A"

    summary_df = pd.DataFrame(
        [
            {
                "total_claims": len(audit_df),
                "pass_count": pass_count,
                "fail_count": fail_count,
                "na_count": na_count,
                "notes": (
                    "Summary state is FAIL if any claim fails, "
                    "PASS if at least one claim passes and none fail, otherwise N/A."
                ),
                "state": summary_state,
                "state_type": state_type,
            }
        ]
    )

    return {
        "claim_audit": audit_df,
        "claim_summary": summary_df,
    }
