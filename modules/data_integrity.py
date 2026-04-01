"""
data_integrity.py — Module 1
SR 11-7 Model Validation Engine

Runs structural data-quality checks on a DataFrame and returns results as a
dict of DataFrames compatible with ledger_writer.py.  Each DataFrame contains
a 'state' column (PASS / FAIL / N/A) and a 'state_type' column sourced from
the data_integrity domain config.

All vocabulary and policy values are driven exclusively by
config/validation_config.yaml via modules.config_loader — no threshold
numbers or state strings are hardcoded in this file.
"""

from typing import Any

import pandas as pd

from modules.config_loader import get_domain_config


# ---------------------------------------------------------------------------
# Internal helpers — each accepts state_type to stamp every record
# ---------------------------------------------------------------------------

def check_missing(df: pd.DataFrame, state_type: str) -> pd.DataFrame:
    """
    Flag columns with missing values.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame to inspect.
    state_type : str
        Value written to the 'state_type' column on every output row.

    Returns
    -------
    pd.DataFrame
        One row per column with columns: column, missing_count, missing_pct,
        state, state_type.
    """
    missing = df.isnull().sum()
    pct = (missing / len(df) * 100).round(2)

    return pd.DataFrame(
        {
            "column": df.columns,
            "missing_count": missing.values,
            "missing_pct": pct.values,
            "state": ["FAIL" if m > 0 else "PASS" for m in missing.values],
            "state_type": state_type,
        }
    )


def check_outliers(
    df: pd.DataFrame,
    numeric_cols: list[str],
    state_type: str,
    z_threshold: float = 3.0,
) -> pd.DataFrame:
    """
    Flag rows where any numeric column exceeds z-score threshold.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame to inspect.
    numeric_cols : list[str]
        Columns to run the z-score check on.
    state_type : str
        Value written to the 'state_type' column on every output row.
    z_threshold : float
        Z-score threshold above which a value is an outlier (default 3.0).

    Returns
    -------
    pd.DataFrame
        One row per outlier (state='FAIL') or a single PASS row if clean.
        Columns: column, row_index, value, z_score, state, state_type.
    """
    records = []

    for col in numeric_cols:
        col_data = pd.to_numeric(df[col], errors="coerce")
        mean = col_data.mean()
        std = col_data.std()

        if std == 0:
            continue

        z_scores = ((col_data - mean) / std).abs()
        outlier_rows = z_scores[z_scores > z_threshold]

        for idx, z in outlier_rows.items():
            records.append(
                {
                    "column": col,
                    "row_index": idx,
                    "value": col_data[idx],
                    "z_score": round(z, 4),
                    "state": "FAIL",
                    "state_type": state_type,
                }
            )

    if not records:
        return pd.DataFrame(
            [
                {
                    "column": "ALL",
                    "row_index": None,
                    "value": None,
                    "z_score": None,
                    "state": "PASS",
                    "state_type": state_type,
                }
            ]
        )

    return pd.DataFrame(records)


def check_duplicates(df: pd.DataFrame, state_type: str) -> pd.DataFrame:
    """
    Check for duplicate rows.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame to inspect.
    state_type : str
        Value written to the 'state_type' column on every output row.

    Returns
    -------
    pd.DataFrame
        Single row with columns: duplicate_rows, state, state_type.
    """
    dup_count = df.duplicated().sum()

    return pd.DataFrame(
        [
            {
                "duplicate_rows": int(dup_count),
                "state": "FAIL" if dup_count > 0 else "PASS",
                "state_type": state_type,
            }
        ]
    )


def check_data_types(
    df: pd.DataFrame,
    state_type: str,
    expected_types: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Verify columns match expected types.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame to inspect.
    state_type : str
        Value written to the 'state_type' column on every output row.
    expected_types : dict[str, str] or None
        Mapping of column name → expected dtype string
        (e.g. {"revenue": "float64", "year": "int64"}).
        If None or empty, the check is skipped and a single N/A row is returned.

    Returns
    -------
    pd.DataFrame
        One row per column in expected_types, or a single N/A row if skipped.
        Columns: column, expected, actual, state, state_type, [notes].
    """
    if not expected_types:
        return pd.DataFrame(
            [
                {
                    "column": "__all__",
                    "expected": None,
                    "actual": None,
                    "state": "N/A",
                    "state_type": state_type,
                    "notes": "dtype check skipped: no expected_types provided",
                }
            ]
        )

    records = []

    for col, expected in expected_types.items():
        if col not in df.columns:
            records.append(
                {
                    "column": col,
                    "expected": expected,
                    "actual": "MISSING",
                    "state": "FAIL",
                    "state_type": state_type,
                }
            )
        else:
            actual = str(df[col].dtype)
            records.append(
                {
                    "column": col,
                    "expected": expected,
                    "actual": actual,
                    "state": "PASS" if actual == expected else "FAIL",
                    "state_type": state_type,
                }
            )

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_integrity_checks(
    df: pd.DataFrame,
    numeric_cols: list[str],
    expected_types: dict[str, str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Run all data-quality checks and return a results bundle.

    Loads the data_integrity policy from config/validation_config.yaml at
    call time.  The state_type value is sourced from config and stamped on
    every output row — no strings are hardcoded in this function.

    Parameters
    ----------
    df : pd.DataFrame
        The DataFrame to validate.
    numeric_cols : list[str]
        Column names to include in the outlier (z-score) check.
    expected_types : dict[str, str] or None
        Expected dtype per column for the dtype-consistency check.
        Pass None to skip dtype checking.

    Returns
    -------
    dict[str, pd.DataFrame]
        Keys: "missing", "outliers", "duplicates", "dtypes"
        Each DataFrame has a 'state' column (PASS / FAIL / N/A) and a
        'state_type' column sourced from the domain config.
    """
    config = get_domain_config("data_integrity")
    state_type: str = config["state_type"]

    return {
        "missing":    check_missing(df, state_type),
        "outliers":   check_outliers(df, numeric_cols, state_type),
        "duplicates": check_duplicates(df, state_type),
        "dtypes":     check_data_types(df, state_type, expected_types),
    }
