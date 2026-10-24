"""Load and validate the ATS Matrix config (config/ats_matrix.json).

Every value is type- and range-checked at startup, so a typo in the config
stops the system before it trades rather than halfway through a session.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "ats_matrix.json"
FEE_CATEGORIES = {"crypto": "crypto_category_rate", "sports": "sports_category_rate",
                  "politics_finance": "politics_finance_category_rate"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BetaConfig:
    loop_target_seconds: float
    max_categorical_paths: int
    autopilot: bool
    learning_enabled: bool
    learning_interval_seconds: float


@dataclass(frozen=True)
class RiskConfig:
    max_drawdown: float  # fraction, e.g. 0.05
    max_trade_usdc: float
    slippage_tolerance_pct: float
    min_depth_usdc: float
    oracle_buffer: float  # fraction, e.g. 0.03


@dataclass(frozen=True)
class Thresholds:
    neg_risk_epsilon: float
    cross_book_min_spread: float
    maker_band: tuple[float, float]


@dataclass(frozen=True)
class ATSConfig:
    version: str
    beta: BetaConfig
    risk: RiskConfig
    fee_rates: dict[str, float]
    thresholds: Thresholds


def _get(data: dict, path: str):
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"missing config key: {path}")
        node = node[key]
    return node


def _num(data: dict, path: str, lo: float, hi: float, integer: bool = False) -> float:
    value = _get(data, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f"{path} must be a number")
    if integer and int(value) != value:
        raise ConfigError(f"{path} must be a whole number")
    if not lo <= value <= hi:
        raise ConfigError(f"{path} must be between {lo} and {hi}, got {value}")
    return int(value) if integer else float(value)


def _bool(data: dict, path: str) -> bool:
    value = _get(data, path)
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be true or false")
    return value


def parse(data: dict) -> ATSConfig:
    beta = "brain_beta_configuration"
    risk = "risk_gate_limits"
    thr = "strategy_activation_thresholds"
    band = (_num(data, f"{thr}.maker_rebate_hunting_price_band.min_usd", 0, 1),
            _num(data, f"{thr}.maker_rebate_hunting_price_band.max_usd", 0, 1))
    if band[0] >= band[1]:
        raise ConfigError("maker_rebate_hunting_price_band min_usd must be below max_usd")
    return ATSConfig(
        version=str(_get(data, "system_metadata.version")),
        beta=BetaConfig(
            loop_target_seconds=_num(data, f"{beta}.execution_loop_target_seconds", 0.01, 3600),
            max_categorical_paths=_num(data, f"{beta}.max_categorical_paths", 1, 100_000, integer=True),
            autopilot=_bool(data, f"{beta}.autopilot_24_7"),
            learning_enabled=_bool(data, f"{beta}.self_adaptive_learning.enabled"),
            learning_interval_seconds=_num(data, f"{beta}.self_adaptive_learning.interval_hours", 0.001, 24 * 30) * 3600,
        ),
        risk=RiskConfig(
            max_drawdown=_num(data, f"{risk}.global_max_drawdown_percent", 0.1, 100) / 100,
            max_trade_usdc=_num(data, f"{risk}.max_trade_size_usdc", 1, 1e9),
            slippage_tolerance_pct=_num(data, f"{risk}.slippage_tolerance_percent", 0, 100),
            min_depth_usdc=_num(data, f"{risk}.polymarket_min_liquidity_depth_2pct_usdc", 0, 1e12),
            oracle_buffer=_num(data, f"{risk}.oracle_dispute_buffer_percent", 0, 100) / 100,
        ),
        fee_rates={cat: _num(data, f"polymarket_fee_constants.{key}", 0, 1) for cat, key in FEE_CATEGORIES.items()},
        thresholds=Thresholds(
            neg_risk_epsilon=_num(data, f"{thr}.negative_risk_squeeze_epsilon", 0, 1),
            cross_book_min_spread=_num(data, f"{thr}.cross_book_arbitrage_min_spread_usdc", 0, 1),
            maker_band=band,
        ),
    )


def load(path: str | Path | None = None) -> ATSConfig:
    path = Path(path or os.environ.get("ATS_CONFIG") or DEFAULT_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except ValueError as err:
        raise ConfigError(f"{path} is not valid JSON: {err}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return parse(data)
