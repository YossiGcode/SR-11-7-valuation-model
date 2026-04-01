# SR-validation-engine

Deterministic audit framework implementing SR 11-7 Outcomes Analysis for financial models and AI-generated claims.

![Python](https://img.shields.io/badge/Python-3.14-blue?style=flat) ![Tests](https://img.shields.io/badge/tests-21%20passed-brightgreen?style=flat) ![SR 11-7](https://img.shields.io/badge/SR%2011--7-compliant-blueviolet?style=flat)

---

## Overview

SR-validation-engine bridges the gap between probabilistic LLM outputs and deterministic regulatory requirements under **Federal Reserve SR 11-7 Section 4: Outcomes Analysis**. It runs three independent validation modules—data integrity, stress testing, and AI claim auditing—and consolidates results into a single Excel ledger with conditional formatting, automated rollup metrics, and per-check audit trails. The engine enforces tolerance-based pass/fail semantics on model outputs and LLM-extracted numerical claims, producing a structured artifact suitable for model risk management (MRM) review and regulatory sign-off.

---

## Quickstart

```bash
# Clone repository
git clone https://github.com/yourusername/SR-validation-engine.git
cd SR-validation-engine

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Unix

# Install dependencies
pip install -r requirements.txt

# Run validation pipeline
python main.py
```

**Output**: `output/sr117_validation_ledger.xlsx` — a formatted Excel workbook with 8 tabs covering data integrity, stress test results, and AI claim audits.

---

## The Three Pillars

### 1. Data Integrity (`modules/data_integrity.py`)

Pre-flight pipeline health checks ensuring clean input data before model execution:

- **Missing values**: Per-column missingness audit with configurable thresholds
- **Duplicate rows**: Exact-match duplicate detection across all columns
- **Data types**: Validates expected numeric types on specified columns
- **Outliers**: Z-score-based statistical outlier flagging (default: |z| > 3)

**State vocabulary**: `PASS` / `FAIL` / `N/A`

### 2. Stress Testing (`modules/stress_tester.py`)

One-At-a-Time (OAT) sensitivity analysis implementing SR 11-7 outcomes review:

- **Sensitivity sweep**: Shocks each assumption across ±1 SD in 5 increments (20 scenarios total for 4 assumptions)
- **1-SD summary**: Condensed view showing output at exactly −1 SD and +1 SD per assumption
- **Elasticity bands**: Configurable thresholds (STABLE: <5%, SENSITIVE: 5–10%, BREACH: >10%)
- **Max absolute delta**: Captures the worst-case output deviation per assumption

**State vocabulary**: `STABLE` / `SENSITIVE` / `BREACH`

### 3. AI Auditor (`modules/ai_auditor.py`)

Deterministic comparison of LLM-extracted numerical claims against source truth:

- **Claim extraction**: Parses AI-generated text for numerical assertions (e.g., "Revenue was $500,000")
- **Tolerance comparison**: Uses `abs_tol` and `rel_tol` from `config/validation_config.yaml`
- **Hallucination detection**: Flags claims exceeding tolerance as `FAIL`
- **Qualitative exclusion**: Non-numeric claims (e.g., "Strong brand moats") are tagged `N/A` and excluded from hallucination rate denominator

**State vocabulary**: `PASS` / `FAIL` / `N/A`

---

## Validation Semantics: The Common Envelope

The engine uses a **polymorphic state routing system** to aggregate heterogeneous validation results:

- **state column**: Every module DataFrame contains a mandatory `state` column with domain-specific values
  - Data integrity: `PASS`, `FAIL`, `N/A`
  - Stress testing: `STABLE`, `SENSITIVE`, `BREACH`
  - AI auditing: `PASS`, `FAIL`, `N/A`

- **state_type column**: Metadata field indicating the vocabulary domain
  - `"validation"` → pass/fail checks
  - `"elasticity"` → sensitivity bands

- **Unified formatting**: `ledger_writer.py` dynamically locates the `state` column in each sheet and applies conditional fills:
  - Green: `PASS`, `STABLE`
  - Amber: `N/A`, `SENSITIVE`
  - Red: `FAIL`, `BREACH`

This architecture allows the executive summary to compute aggregate metrics (pipeline health, hallucination rate, top economic risks) from structurally diverse inputs without requiring schema normalization.

---

## Ledger Output

The validation ledger is written to `output/sr117_validation_ledger.xlsx` (gitignored, regenerated on every run):

| Tab | Description |
|-----|-------------|
| `00_Executive_Summary` | Primary rollup: pipeline health, AI hallucination rate, top economic risks |
| `Data_Integrity__missing` | Missing field audit per column |
| `Data_Integrity__duplicates` | Duplicate row detection |
| `Data_Integrity__outliers` | Statistical outlier flagging |
| `Data_Integrity__dtypes` | Column data type validation |
| `Stress_Testing__sensitivity_sweep` | 20-scenario OAT parameter shock results (STABLE / SENSITIVE / BREACH) |
| `Stress_Testing__one_sd_summary` | 1-SD summary across 4 key assumptions |
| `AI_Audit__claim_audit` | Claim-level PASS / FAIL / N/A with extracted value, source value, variance % |
| `AI_Audit__claim_summary` | Aggregate hallucination rate — N/A rows excluded from denominator |

All tabs include:
- Navy header row with white bold text
- Freeze panes on row 1
- Auto-fit column widths
- Thin borders on data cells
- Conditional fills on the `state` column

---

## Executive Summary Sample

| Metric | Value | Notes |
| :--- | :--- | :--- |
| Pipeline Health | FAIL | 4/6 checks passed; 2 FAIL(s) detected across data_integrity module. |
| AI Hallucination Rate | 66.7% | 2/3 audited numeric claims failed tolerance check. N/A rows excluded. |
| Top Economic Risks | BREACH | discount_rate (+33.90% [BREACH]); free_cash_flow (-10.00% [SENSITIVE]); terminal_growth_rate (+5.02% [SENSITIVE]) |
| Run Timestamp (UTC) | 2026-03-31T23:46:58 | ISO 8601 timestamp of this validation run. |

**Top Economic Risks logic**: Deduplicates assumptions across both stress test sheets (`sensitivity_sweep` and `one_sd_summary`), sorts by absolute risk percentage descending, and takes the top 3 unique assumptions. The same assumption cannot dominate all three slots.

---

## Sample Console Output

```
$ python main.py
[ledger_writer] Ledger written → output/sr117_validation_ledger.xlsx

=== SR 11-7 Validation complete. Flat result keys: ===
  ✗  Data_Integrity__missing                       rows=  3  {'PASS': 2, 'FAIL': 1}
  ✓  Data_Integrity__outliers                      rows=  1  {'PASS': 1}
  ✗  Data_Integrity__duplicates                    rows=  1  {'FAIL': 1}
  ✓  Data_Integrity__dtypes                        rows=  1  {'N/A': 1}
  ✗  Stress_Testing__sensitivity_sweep             rows= 20  {'STABLE': 11, 'SENSITIVE': 7, 'BREACH': 2}
  ✗  Stress_Testing__one_sd_summary                rows=  4  {'SENSITIVE': 2, 'STABLE': 1, 'BREACH': 1}
  ✗  AI_Audit__claim_audit                         rows=  3  {'PASS': 1, 'FAIL': 1, 'N/A': 1}
  ✗  AI_Audit__claim_summary                       rows=  1  {'FAIL': 1}

RESULT: ONE OR MORE CHECKS FAILED — review ledger before sign-off.
```

Exit code: `0` if all checks pass, `1` if any check fails.

---

## SR 11-7 Alignment

This engine directly implements the following SR 11-7 requirements:

- **Section 4: Outcomes Analysis** — The stress testing module performs OAT sensitivity analysis with configurable elasticity bands, capturing the impact of ±1 SD shocks on model outputs. This satisfies the outcomes review requirement for evaluating model behavior under adverse scenarios.

- **Deterministic Model Output Review** — All validation results are written to a structured Excel ledger with pass/fail status, tolerance comparisons, and deviation percentages. This creates an auditable record suitable for independent model risk management (MRM) review.

- **Independent Challenge of AI-Generated Claims** — The AI auditor module validates LLM-extracted numerical claims against deterministic source truth using absolute and relative tolerance thresholds. Qualitative claims are excluded from the hallucination rate denominator, preventing false positives.

- **Documentation and Auditability** — The executive summary consolidates pipeline health, AI hallucination rate, and top economic risks into a single rollup view. Each metric includes notes explaining the calculation basis and relevant counts. All raw check results are preserved in module-specific tabs with full traceability to input data.

---

## Configuration

Domain-specific policies (tolerance maps, elasticity bands, missing value thresholds) are defined in `config/validation_config.yaml` as a structured YAML document. The `config_loader.py` module validates the YAML against a JSON schema at startup and raises `ConfigurationError` if required keys are missing or types are invalid. The `output/` directory is gitignored and regenerated on every run; it should never be committed to version control.

---

## Running Tests

```bash
pytest tests/ -q
```

**Expected output**: `21 passed in X.XXs`

The test suite covers:
- Data integrity checks (missing values, duplicates, outliers, dtypes)
- Stress testing (sensitivity sweep, 1-SD summary, elasticity classification)
- AI auditor (tolerance comparison, qualitative claim exclusion)
- Ledger writer (workbook structure, conditional formatting, executive summary metrics, mutation safety)

**Note**: Test files must be run via `pytest` to handle root-level import logic. Running test files directly with `python tests/test_*.py` will fail due to missing module paths.
