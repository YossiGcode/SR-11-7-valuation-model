"""
config_loader.py — SR 11-7 Validation Engine

Centralised, schema-validated configuration loader.

Design contract
---------------
- ``load_config`` is the single entry point for reading and validating YAML.
- ``get_domain_config`` is the ONLY function any other module may call.
  No module may reach into raw YAML paths directly; this function is the
  enforced abstraction boundary.
- The config is stateless policy: no run archiving, no drift tracking.
- yaml.safe_load is used exclusively; yaml.load is never called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import validate, ValidationError  # noqa: F401 — re-exported for callers

# ---------------------------------------------------------------------------
# Resolved default paths (relative to this file, framework-independent)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent.parent          # project root
_DEFAULT_YAML   = _HERE / "config" / "validation_config.yaml"
_DEFAULT_SCHEMA = _HERE / "config" / "schema" / "validation.schema.json"

# ---------------------------------------------------------------------------
# Module-level parse cache — keyed by (yaml_path, schema_path) so that
# repeated calls within the same interpreter session pay zero I/O cost.
# ---------------------------------------------------------------------------
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(
    yaml_path: str | Path = _DEFAULT_YAML,
    schema_path: str | Path = _DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """
    Load and schema-validate the validation configuration YAML.

    Reads the YAML file with ``yaml.safe_load``, loads the JSON Schema,
    and validates the parsed document with ``jsonschema.validate``.  Raises
    immediately if validation fails — no partial config is ever returned.

    Results are cached by ``(yaml_path, schema_path)`` so repeated calls
    within the same process pay zero I/O cost after the first call.

    Parameters
    ----------
    yaml_path : str or Path
        Path to the YAML config file.  Defaults to
        ``config/validation_config.yaml`` relative to the project root.
    schema_path : str or Path
        Path to the JSON Schema file.  Defaults to
        ``config/schema/validation.schema.json`` relative to the project root.

    Returns
    -------
    dict[str, Any]
        Validated configuration document.

    Raises
    ------
    FileNotFoundError
        If either file does not exist.
    yaml.YAMLError
        If the YAML file cannot be parsed.
    jsonschema.ValidationError
        If the parsed document does not conform to the schema.
    """
    cache_key = (str(yaml_path), str(schema_path))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    yaml_path   = Path(yaml_path)
    schema_path = Path(schema_path)

    if not yaml_path.exists():
        raise FileNotFoundError(f"Config YAML not found: {yaml_path}")
    if not schema_path.exists():
        raise FileNotFoundError(f"Config schema not found: {schema_path}")

    with yaml_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    with schema_path.open("r", encoding="utf-8") as fh:
        schema = json.load(fh)

    # Raises jsonschema.ValidationError with a descriptive message on failure.
    validate(instance=config, schema=schema)

    _CACHE[cache_key] = config
    return config


def get_domain_config(
    domain: str,
    yaml_path: str | Path = _DEFAULT_YAML,
    schema_path: str | Path = _DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """
    Return the validated configuration for a single domain namespace.

    This is the **only** function any module in this project may call to
    access configuration.  Direct access to raw YAML paths is prohibited.

    Parameters
    ----------
    domain : str
        Top-level namespace key in the config.  One of:
        ``"data_integrity"``, ``"ai_audit"``, ``"stress_testing"``.
    yaml_path : str or Path
        Passed through to ``load_config``.  Use the default in production.
    schema_path : str or Path
        Passed through to ``load_config``.  Use the default in production.

    Returns
    -------
    dict[str, Any]
        The validated sub-document for the requested domain.

    Raises
    ------
    KeyError
        If ``domain`` is not a top-level key in the validated config.
    jsonschema.ValidationError
        Propagated from ``load_config`` if the document fails schema
        validation.
    """
    config = load_config(yaml_path=yaml_path, schema_path=schema_path)

    if domain not in config:
        available = ", ".join(sorted(config.keys()))
        raise KeyError(
            f"Domain '{domain}' not found in config. "
            f"Available domains: {available}"
        )

    return config[domain]
