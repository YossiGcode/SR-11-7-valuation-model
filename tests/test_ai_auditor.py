"""
tests/test_ai_auditor.py

Behavior tests for the config-driven claim audit engine in modules/ai_auditor.py.

Coverage:
  1. Zero source value uses abs_tol only — no ZeroDivisionError, variance_pct is null
  2. is_approximate=True does NOT change a FAIL verdict (notes wording only)
  3. Unsupported metric renders exactly status='N/A' at output time
  4. Supported metric produces PASS when within config tolerance
  5. Supported metric produces FAIL when deviation exceeds config tolerance
  6. _resolve_tolerance_key longest-prefix matching (deterministic mapping table)
  7. Return contract: dict[str, pd.DataFrame], expected columns, status vocabulary

Design notes
------------
- All tests that call evaluate_claims use the mock_ai_audit fixture, which
  patches get_domain_config as imported inside modules.ai_auditor.  The real
  YAML is never touched.
- Test 6 calls _resolve_tolerance_key directly — it is a pure function with
  no config dependency, so no patching is needed.
- pandas converts None to NaN in numeric columns; variance_pct nullness is
  asserted with pd.isna() rather than `is None`.
- All tolerance math in comments is independently verifiable with math.isclose.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from modules.ai_auditor import _resolve_tolerance_key, evaluate_claims


# ---------------------------------------------------------------------------
# Mock ai_audit domain config — mirrors canonical YAML structure exactly
# ---------------------------------------------------------------------------
_MOCK_AI_AUDIT = {
    "valid_states": ["PASS", "FAIL", "SKIPPED", "N_A"],
    "state_type": "validation",
    "extraction": {"record_is_approximate": True},
    "tolerances": {
        "revenue":       {"rel_tol": 0.005, "abs_tol": 0.01},
        "discount_rate": {"rel_tol": 0.0,   "abs_tol": 0.001},
    },
    "notes_templates": {
        "approximate_fail":  "Failed {variance_bps}bps variance; AI flagged claim as approximate.",
        "deterministic_fail": "Verified against source truth and failed tolerance check.",
    },
}


@pytest.fixture
def mock_ai_audit(monkeypatch):
    """
    Patch get_domain_config as imported inside modules.ai_auditor so that
    evaluate_claims receives _MOCK_AI_AUDIT regardless of the domain argument.
    Patching the symbol in modules.ai_auditor (not modules.config_loader) is
    required because the import creates a local name binding in the module.
    """
    monkeypatch.setattr(
        "modules.ai_auditor.get_domain_config",
        lambda domain: _MOCK_AI_AUDIT,
    )


# ---------------------------------------------------------------------------
# Test 1 — Zero source value: abs_tol only, no division issues
# ---------------------------------------------------------------------------

def test_zero_bound_uses_abs_tol_without_division_issues(mock_ai_audit):
    """
    When source truth is 0.0:
      - variance_pct must be null (no division by zero)
      - math.isclose uses abs_tol only, not the ratio branch
      - extracted=0.00005 is within abs_tol=0.01 → PASS

    math.isclose(0.00005, 0.0, rel_tol=0.005, abs_tol=0.01):
      abs(0.00005) = 0.00005 <= max(0.005 * 0.00005, 0.01) = 0.01 → True
    """
    claims = [
        {
            "claim_text": "Revenue was negligible this quarter.",
            "source_key": "revenue_base",
            "extracted_value": 0.00005,
        }
    ]
    source_truth = {"revenue_base": 0.0}

    result = evaluate_claims(claims, source_truth)
    row = result["claim_audit"].iloc[0]

    assert row["state"] == "PASS", f"Expected PASS; got {row['state']!r}"
    assert pd.isna(row["variance_pct"]), (
        "variance_pct must be null (NaN/None) when source value is zero"
    )
    # Self-documenting: verify the expected math independently
    assert math.isclose(0.00005, 0.0, rel_tol=0.005, abs_tol=0.01)


# ---------------------------------------------------------------------------
# Test 2 — Approximate claim that exceeds tolerance still fails
# ---------------------------------------------------------------------------

def test_approximate_claim_that_exceeds_tolerance_still_fails(mock_ai_audit):
    """
    is_approximate=True must affect only notes wording — never flip FAIL to PASS.

    Values: extracted=1.5, truth=1.0 (50% deviation, far outside rel_tol=0.005)
    math.isclose(1.5, 1.0, rel_tol=0.005, abs_tol=0.01):
      abs(0.5) = 0.5 <= max(0.005 * 1.5, 0.01) = 0.01 → False → FAIL

    Notes must contain the approximate_fail substring, proving is_approximate
    routed to the correct notes branch without changing the verdict.
    """
    claims = [
        {
            "claim_text": "Revenue was approximately $1.5M.",
            "source_key": "revenue_q1",
            "extracted_value": 1.5,
            "is_approximate": True,
        }
    ]
    source_truth = {"revenue_q1": 1.0}

    result = evaluate_claims(claims, source_truth)
    row = result["claim_audit"].iloc[0]

    assert row["state"] == "FAIL", (
        "Approximate claims that exceed tolerance must still produce FAIL"
    )
    assert "AI flagged claim as approximate." in row["notes"], (
        f"Expected approximate_fail note substring; got: {row['notes']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Unsupported metric renders exactly "N/A"
# ---------------------------------------------------------------------------

def test_unsupported_metric_renders_exactly_na(mock_ai_audit):
    """
    A source_key with no matching tolerance policy (direct or prefix) must
    produce status='N/A' and the exact unsupported-metric notes string.

    'ceo_sentiment' is present in source_truth (passes the missing-key guard)
    but absent from tolerances and has no prefix match → N/A.

    The 'N/A' string must appear in the output DataFrame, not the YAML's 'N_A'.
    """
    claims = [
        {
            "claim_text": "CEO sentiment was strongly positive.",
            "source_key": "ceo_sentiment",
            "extracted_value": 0.9,
        }
    ]
    source_truth = {"ceo_sentiment": 0.85}  # present in truth; absent from tolerances

    result = evaluate_claims(claims, source_truth)
    row = result["claim_audit"].iloc[0]

    assert row["state"] == "N/A", (
        f"Expected state='N/A' for unsupported metric; got {row['state']!r}"
    )
    assert row["notes"] == "Unsupported metric: no tolerance policy defined."


# ---------------------------------------------------------------------------
# Test 4 — Supported metric passes within tolerance
# ---------------------------------------------------------------------------

def test_supported_metric_passes_using_config_tolerance(mock_ai_audit):
    """
    Exact match on a supported metric must produce PASS.
    source_key='revenue_q4' resolves to 'revenue' via prefix matching.

    math.isclose(45_000_000.0, 45_000_000.0, rel_tol=0.005, abs_tol=0.01) → True
    """
    claims = [
        {
            "claim_text": "Revenue was $45M in Q4.",
            "source_key": "revenue_q4",
            "extracted_value": 45_000_000.0,
        }
    ]
    source_truth = {"revenue_q4": 45_000_000.0}

    result = evaluate_claims(claims, source_truth)
    row = result["claim_audit"].iloc[0]

    assert row["state"] == "PASS", f"Exact match must produce PASS; got {row['state']!r}"


# ---------------------------------------------------------------------------
# Test 5 — Supported metric fails when deviation exceeds tolerance
# ---------------------------------------------------------------------------

def test_supported_metric_fails_using_config_tolerance(mock_ai_audit):
    """
    Large deviation on a supported metric must produce FAIL with deterministic_fail notes.
    source_key='revenue_q4' → 'revenue' (rel_tol=0.005).

    math.isclose(50_000_000, 45_000_000, rel_tol=0.005, abs_tol=0.01):
      abs(5_000_000) = 5_000_000 <= max(0.005 * 50_000_000, 0.01) = 250_000 → False → FAIL

    is_approximate not set (defaults False) → deterministic_fail note.
    """
    claims = [
        {
            "claim_text": "Revenue was $50M in Q4.",
            "source_key": "revenue_q4",
            "extracted_value": 50_000_000.0,
        }
    ]
    source_truth = {"revenue_q4": 45_000_000.0}

    result = evaluate_claims(claims, source_truth)
    row = result["claim_audit"].iloc[0]

    assert row["state"] == "FAIL", f"Large deviation must produce FAIL; got {row['state']!r}"
    assert row["notes"] == _MOCK_AI_AUDIT["notes_templates"]["deterministic_fail"], (
        f"Expected deterministic_fail note; got: {row['notes']!r}"
    )


# ---------------------------------------------------------------------------
# Test 6 — _resolve_tolerance_key: deterministic longest-prefix mapping table
# ---------------------------------------------------------------------------

def test_resolve_tolerance_key_handles_source_key_normalization():
    """
    _resolve_tolerance_key must map source_keys to tolerance keys using
    longest-prefix left-anchored matching.  This test exhausts the mapping
    table to prevent silent regressions in the matching algorithm.

    Tolerance keys in mock config: "revenue", "discount_rate"

    source_key                → expected match
    ─────────────────────────────────────────────────
    "revenue"                 → "revenue"          (exact)
    "revenue_base"            → "revenue"          (prefix: parts[0])
    "revenue_q4_adjusted"     → "revenue"          (prefix: parts[0] wins over parts[0:1])
    "discount_rate"           → "discount_rate"    (exact)
    "discount_rate_2024"      → "discount_rate"    (prefix: parts[0:2])
    "discount_rate_q1_adj"    → "discount_rate"    (prefix: parts[0:2] wins longest first)
    "ceo_sentiment"           → None              (no match)
    "discount"                → None              (partial compound key — no match)
    """
    tolerances = _MOCK_AI_AUDIT["tolerances"]

    assert _resolve_tolerance_key("revenue",             tolerances) == "revenue"
    assert _resolve_tolerance_key("revenue_base",        tolerances) == "revenue"
    assert _resolve_tolerance_key("revenue_q4_adjusted", tolerances) == "revenue"
    assert _resolve_tolerance_key("discount_rate",       tolerances) == "discount_rate"
    assert _resolve_tolerance_key("discount_rate_2024",  tolerances) == "discount_rate"
    assert _resolve_tolerance_key("discount_rate_q1_adj", tolerances) == "discount_rate"
    assert _resolve_tolerance_key("ceo_sentiment",       tolerances) is None
    assert _resolve_tolerance_key("discount",            tolerances) is None


# ---------------------------------------------------------------------------
# Test 7 — Return contract preserved
# ---------------------------------------------------------------------------

def test_return_contract_preserved(mock_ai_audit):
    """
    evaluate_claims must return dict[str, pd.DataFrame] with:
      - exactly the keys "claim_audit" and "claim_summary"
      - all expected columns in claim_audit
      - status values only within {PASS, FAIL, N/A}

    Uses a mixed batch: one resolvable claim (PASS) and one qualitative claim (N/A)
    to exercise both code paths and verify the status vocabulary spans correctly.
    """
    claims = [
        {
            "claim_text": "Revenue was $45M.",
            "source_key": "revenue_2024",
            "extracted_value": 45_000_000.0,
        },
        {
            "claim_text": "Management is optimistic.",
            "source_key": None,
            "extracted_value": None,
        },
    ]
    source_truth = {"revenue_2024": 45_000_000.0}

    result = evaluate_claims(claims, source_truth)

    # --- Top-level shape ---
    assert isinstance(result, dict), "evaluate_claims must return a dict"
    assert set(result.keys()) == {"claim_audit", "claim_summary"}, (
        f"Unexpected keys: {set(result.keys())}"
    )

    audit   = result["claim_audit"]
    summary = result["claim_summary"]

    assert isinstance(audit,   pd.DataFrame), "claim_audit must be a DataFrame"
    assert isinstance(summary, pd.DataFrame), "claim_summary must be a DataFrame"

    # --- Column contract for claim_audit ---
    required_cols = {
        "claim_text", "source_key", "unit", "confidence",
        "extracted_value", "source_value", "variance",
        "variance_pct", "notes", "state", "state_type",
    }
    assert required_cols.issubset(set(audit.columns)), (
        f"claim_audit missing columns: {required_cols - set(audit.columns)}"
    )
    assert "state" in summary.columns

    # --- State vocabulary ---
    valid_states = {"PASS", "FAIL", "N/A"}
    assert set(audit["state"].unique()).issubset(valid_states), (
        f"Unexpected audit state values: {set(audit['state'].unique())}"
    )
    assert summary["state"].iloc[0] in valid_states, (
        f"Unexpected summary state: {summary['state'].iloc[0]!r}"
    )
