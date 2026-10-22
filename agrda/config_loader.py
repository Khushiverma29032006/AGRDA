"""
Configuration loader for AGRDA.

Loads threshold and experiment settings from TOML config files.

Config priority (highest to lowest):
  1. Frozen config (frozen_v1.toml, etc.) — referenced by --config flag
  2. Default config (configs/default_config.toml)
  3. Hardcoded defaults in ConfigThresholds

Freeze workflow:
  1. Run pilot simulation
  2. Run: agrda freeze-config --run-id <pilot-id> --output configs/frozen_v1.toml
  3. Reference in confirmatory run: agrda simulate --config configs/frozen_v1.toml
  4. NEVER tune thresholds on confirmatory results.

The config file is NOT required — defaults are used if missing.
This allows the tool to run immediately without setup.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default config file location (relative to project root)
DEFAULT_CONFIG_PATH = Path("configs") / "default_config.toml"


def _load_toml(path: Path) -> Dict[str, Any]:
    """Load a TOML file. Uses tomllib (Python 3.11+) or tomli as fallback."""
    try:
        import tomllib
        with path.open("rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli  # type: ignore
        with path.open("rb") as f:
            return tomli.load(f)
    except ImportError:
        pass
    # Manual minimal TOML parser for simple key=value sections
    return _parse_simple_toml(path.read_text(encoding="utf-8"))


def _parse_simple_toml(text: str) -> Dict[str, Any]:
    """
    Minimal TOML parser for simple key = value files.

    Supports:
      - [section] headers
      - key = value (int, float, bool, string)
      - # comments
      - Quoted strings: key = "value"
    """
    result: Dict[str, Any] = {}
    current_section: Optional[str] = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            result.setdefault(current_section, {})
            continue
        if "=" in line:
            key, _, raw_val = line.partition("=")
            key = key.strip()
            raw_val = raw_val.split("#")[0].strip()  # strip inline comments

            # Parse value
            if raw_val.startswith('"') and raw_val.endswith('"'):
                val: Any = raw_val[1:-1]
            elif raw_val.lower() == "true":
                val = True
            elif raw_val.lower() == "false":
                val = False
            else:
                try:
                    val = int(raw_val)
                except ValueError:
                    try:
                        val = float(raw_val)
                    except ValueError:
                        val = raw_val

            if current_section:
                result[current_section][key] = val
            else:
                result[key] = val

    return result


class ConfigLoader:
    """
    Loads and validates AGRDA configuration from TOML files.

    Usage:
        loader = ConfigLoader()
        cfg = loader.load()          # loads default_config.toml
        cfg = loader.load("configs/frozen_v1.toml")  # loads frozen config
        thresholds = loader.get_thresholds(cfg)
    """

    def __init__(self, default_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.default_path = default_path

    def load(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Load configuration from a TOML file.

        Parameters
        ----------
        config_path : path to config file, or None to use default

        Returns a dict with sections: thresholds, window, design, safety,
        experiment, pilot, meta (if frozen).
        """
        target = Path(config_path) if config_path else self.default_path

        if not target.exists():
            logger.warning(
                "Config file not found: %s — using hardcoded defaults", target
            )
            return {}

        try:
            data = _load_toml(target)
            logger.info("Loaded config from: %s", target)

            # Log freeze status
            meta = data.get("meta", {})
            status = meta.get("status", "UNFROZEN")
            if status == "FROZEN":
                logger.info(
                    "Config is FROZEN (pilot: %s, frozen_at: %s)",
                    meta.get("pilot_run_id"), meta.get("frozen_at"),
                )
            else:
                logger.info("Config status: UNFROZEN (initial defaults)")

            return data
        except Exception as e:
            logger.error("Failed to load config %s: %s — using defaults", target, e)
            return {}

    def get_thresholds(self, cfg: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract threshold values from loaded config.

        Returns a flat dict suitable for passing to AuditBattery and
        DecisionPolicy constructors.
        """
        t = cfg.get("thresholds", {})
        return {
            "delta_eff": float(t.get("delta_eff", 0.05)),
            "delta_eq": float(t.get("delta_eq", 0.0125)),
            "kappa": float(t.get("kappa", 0.25)),
            "icc_min": float(t.get("icc_min", 0.70)),
            "first_stage_min": float(t.get("first_stage_min", 0.10)),
        }

    def get_window_spec_kwargs(self, cfg: Dict[str, Any]) -> Dict[str, float]:
        """Extract WindowSpec constructor kwargs from loaded config."""
        w = cfg.get("window", {})
        return {
            "warmup_s": float(w.get("warmup_s", 10.0)),
            "measure_s": float(w.get("measure_s", 30.0)),
            "washout_s": float(w.get("washout_s", 20.0)),
            "jitter_s": float(w.get("jitter_s", 2.0)),
        }

    def is_frozen(self, cfg: Dict[str, Any]) -> bool:
        """Return True if this config has been frozen after a pilot run."""
        return cfg.get("meta", {}).get("status", "UNFROZEN") == "FROZEN"

    def get_pilot_run_id(self, cfg: Dict[str, Any]) -> Optional[str]:
        """Return the pilot run ID used to freeze this config, if any."""
        return cfg.get("meta", {}).get("pilot_run_id")


# Module-level convenience instance
_loader = ConfigLoader()


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load config from path (or default). Module-level convenience function."""
    return _loader.load(config_path)


def get_thresholds(config_path: Optional[str] = None) -> Dict[str, float]:
    """Load config and return threshold dict. Module-level convenience."""
    cfg = _loader.load(config_path)
    return _loader.get_thresholds(cfg)
