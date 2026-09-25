"""Pydantic schemas for the indicator layer.

Every group carries both the raw last-bar values AND a list of
`ThresholdSignal`s — the fired signals with their thresholds, direction and
weight — so the LLM sees "RSI=27.4 (oversold, threshold<30)" not just a
number, and the journal can reconstruct why the composite score leaned bull
or bear.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["bullish", "bearish", "neutral"]


class ThresholdSignal(BaseModel):
    """One fired indicator signal, with every field the audit trail needs."""

    indicator: str
    value: Optional[float] = None
    threshold: Optional[str] = None
    direction: Direction
    weight: float = 1.0
    note: str = ""

    def contribution(self) -> float:
        """+weight for bullish, -weight for bearish, 0 for neutral."""
        if self.direction == "bullish":
            return self.weight
        if self.direction == "bearish":
            return -self.weight
        return 0.0


class Momentum(BaseModel):
    rsi_14: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    stoch_rsi: Optional[float] = None
    roc_10: Optional[float] = None
    williams_r_14: Optional[float] = None
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Trend(BaseModel):
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_hist_prev: Optional[float] = None
    adx_14: Optional[float] = None
    aroon_up: Optional[float] = None
    aroon_down: Optional[float] = None
    psar: Optional[float] = None
    price_vs_ema200_pct: Optional[float] = None
    regime: Optional[Literal["uptrend", "downtrend", "chop"]] = None
    # P13: Hurst exponent + regime for mean-reversion vs momentum detection
    hurst_exponent: Optional[float] = None
    hurst_regime: Optional[Literal["momentum", "mean_revert", "random"]] = None
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Volatility(BaseModel):
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_width: Optional[float] = None
    keltner_upper: Optional[float] = None
    keltner_middle: Optional[float] = None
    keltner_lower: Optional[float] = None
    keltner_width: Optional[float] = None
    atr_14: Optional[float] = None
    atr_pct: Optional[float] = None
    realized_vol_20d_ann: Optional[float] = None
    close_z_20: Optional[float] = None
    vol_regime: Optional[Literal["low", "mid", "high"]] = None
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Volume(BaseModel):
    obv: Optional[float] = None
    obv_slope_10: Optional[float] = None
    vwap: Optional[float] = None
    cmf_20: Optional[float] = None
    mfi_14: Optional[float] = None
    volume_z_20: Optional[float] = None
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Structure(BaseModel):
    pivot_classic: dict[str, float] = Field(default_factory=dict)
    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    nearest_resistance: Optional[float] = None
    nearest_support: Optional[float] = None
    resistance_distance_atr: Optional[float] = None
    support_distance_atr: Optional[float] = None
    high_20d: Optional[float] = None
    low_20d: Optional[float] = None
    high_50d: Optional[float] = None
    low_50d: Optional[float] = None
    high_200d: Optional[float] = None
    low_200d: Optional[float] = None
    break_of_structure: Literal["bullish", "bearish", "none"] = "none"
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Candles(BaseModel):
    bullish_engulfing: bool = False
    bearish_engulfing: bool = False
    hammer: bool = False
    shooting_star: bool = False
    morning_star: bool = False
    evening_star: bool = False
    piercing_line: bool = False
    dark_cloud_cover: bool = False
    tweezer_bottom: bool = False
    tweezer_top: bool = False
    three_white_soldiers: bool = False
    three_black_crows: bool = False
    doji: bool = False
    inside_bar: bool = False
    outside_bar: bool = False
    bull_ringer_score: float = 0.0
    bear_ringer_score: float = 0.0
    signals: list[ThresholdSignal] = Field(default_factory=list)


class Divergences(BaseModel):
    regular_bullish_rsi: bool = False
    regular_bearish_rsi: bool = False
    hidden_bullish_rsi: bool = False
    hidden_bearish_rsi: bool = False
    regular_bullish_macd: bool = False
    regular_bearish_macd: bool = False
    regular_bullish_obv: bool = False
    regular_bearish_obv: bool = False
    strength_score: float = 0.0
    signals: list[ThresholdSignal] = Field(default_factory=list)


class IndicatorSnapshot(BaseModel):
    """The full technical picture, as of one bar."""

    ticker: str
    as_of_date: str
    last_close: float
    last_volume: float
    bars_used: int

    momentum: Momentum = Field(default_factory=Momentum)
    trend: Trend = Field(default_factory=Trend)
    volatility: Volatility = Field(default_factory=Volatility)
    volume: Volume = Field(default_factory=Volume)
    structure: Structure = Field(default_factory=Structure)
    candles: Candles = Field(default_factory=Candles)
    divergences: Divergences = Field(default_factory=Divergences)

    composite_bull_score: float = 0.0
    composite_bear_score: float = 0.0
    net_bias_score: float = 0.0
    fired_signals: list[ThresholdSignal] = Field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Render the snapshot as a compact, LLM-facing evidence block."""
        m = self.momentum
        t = self.trend
        v = self.volatility
        vol = self.volume
        s = self.structure
        c = self.candles
        d = self.divergences

        def fmt(x: Optional[float], digits: int = 2) -> str:
            return "n/a" if x is None else f"{x:.{digits}f}"

        lines = [
            f"### Technical Snapshot — {self.ticker} @ {self.as_of_date}",
            f"Last close: {fmt(self.last_close)}   Last volume: {fmt(self.last_volume, 0)}   "
            f"Bars used: {self.bars_used}",
            "",
            "**Momentum**",
            f"- RSI(14) = {fmt(m.rsi_14)}   Stoch %K/%D = {fmt(m.stoch_k)}/{fmt(m.stoch_d)}   "
            f"StochRSI = {fmt(m.stoch_rsi)}",
            f"- ROC(10) = {fmt(m.roc_10)}%   Williams %R(14) = {fmt(m.williams_r_14)}",
            "",
            "**Trend**",
            f"- EMA 9/21/50/200 = {fmt(t.ema_9)}/{fmt(t.ema_21)}/{fmt(t.ema_50)}/{fmt(t.ema_200)}",
            f"- MACD line/signal/hist = {fmt(t.macd_line, 3)}/{fmt(t.macd_signal, 3)}/{fmt(t.macd_hist, 3)}"
            f" (prev hist {fmt(t.macd_hist_prev, 3)})",
            f"- ADX(14) = {fmt(t.adx_14)}   Aroon Up/Down = {fmt(t.aroon_up)}/{fmt(t.aroon_down)}"
            f"   PSAR = {fmt(t.psar)}",
            f"- Price vs EMA200 = {fmt(t.price_vs_ema200_pct)}%   Regime = {t.regime}",
            "",
            "**Volatility**",
            f"- Bollinger up/mid/lo = {fmt(v.bb_upper)}/{fmt(v.bb_middle)}/{fmt(v.bb_lower)}"
            f"   width = {fmt(v.bb_width)}",
            f"- Keltner up/mid/lo = {fmt(v.keltner_upper)}/{fmt(v.keltner_middle)}/{fmt(v.keltner_lower)}"
            f"   width = {fmt(v.keltner_width)}",
            f"- ATR(14) = {fmt(v.atr_14)}   ATR%% = {fmt(v.atr_pct)}%"
            f"   Realized vol (20d ann) = {fmt(v.realized_vol_20d_ann)}",
            f"- Close z(20) = {fmt(v.close_z_20)}   Vol regime = {v.vol_regime}",
            "",
            "**Volume**",
            f"- OBV = {fmt(vol.obv, 0)}   OBV slope(10) = {fmt(vol.obv_slope_10, 3)}",
            f"- VWAP = {fmt(vol.vwap)}   CMF(20) = {fmt(vol.cmf_20, 3)}   MFI(14) = {fmt(vol.mfi_14)}",
            f"- Volume z(20) = {fmt(vol.volume_z_20, 2)}",
            "",
            "**Structure**",
            f"- 20d high/low = {fmt(s.high_20d)}/{fmt(s.low_20d)}   "
            f"50d high/low = {fmt(s.high_50d)}/{fmt(s.low_50d)}",
            f"- Nearest resistance/support = {fmt(s.nearest_resistance)}/{fmt(s.nearest_support)}"
            f"   distance (ATR units) = {fmt(s.resistance_distance_atr)}/{fmt(s.support_distance_atr)}",
            f"- Pivot classic P/R1/S1 = "
            f"{fmt(s.pivot_classic.get('P'))}/{fmt(s.pivot_classic.get('R1'))}/{fmt(s.pivot_classic.get('S1'))}",
            f"- Break of structure = {s.break_of_structure}",
            "",
            "**Candlestick reversals (last bar unless noted)**",
            f"- Bullish engulfing: {c.bullish_engulfing}   Bearish engulfing: {c.bearish_engulfing}",
            f"- Hammer: {c.hammer}   Shooting star: {c.shooting_star}",
            f"- Morning star: {c.morning_star}   Evening star: {c.evening_star}",
            f"- Piercing line: {c.piercing_line}   Dark cloud cover: {c.dark_cloud_cover}",
            f"- Tweezer bottom/top: {c.tweezer_bottom}/{c.tweezer_top}",
            f"- Three white soldiers/black crows: {c.three_white_soldiers}/{c.three_black_crows}",
            f"- Doji: {c.doji}   Inside/outside bar: {c.inside_bar}/{c.outside_bar}",
            f"- **Bull-ringer score**: {c.bull_ringer_score:.2f}   "
            f"**Bear-ringer score**: {c.bear_ringer_score:.2f}",
            "",
            "**Divergences**",
            f"- RSI: reg-bull={d.regular_bullish_rsi} reg-bear={d.regular_bearish_rsi} "
            f"hid-bull={d.hidden_bullish_rsi} hid-bear={d.hidden_bearish_rsi}",
            f"- MACD: reg-bull={d.regular_bullish_macd} reg-bear={d.regular_bearish_macd}",
            f"- OBV: reg-bull={d.regular_bullish_obv} reg-bear={d.regular_bearish_obv}",
            f"- Strength score: {d.strength_score:.2f}",
            "",
            f"**Composite bias** — bull {self.composite_bull_score:.2f}, "
            f"bear {self.composite_bear_score:.2f}, net {self.net_bias_score:+.2f}",
            f"Signals fired ({len(self.fired_signals)}):",
        ]
        for sig in self.fired_signals:
            val_str = f" {sig.value:.4f}" if sig.value is not None else ""
            thr_str = f" [threshold: {sig.threshold}]" if sig.threshold else ""
            lines.append(
                f"  - [{sig.direction}] {sig.indicator}{val_str}{thr_str} "
                f"weight={sig.weight:.2f} — {sig.note}"
            )
        return "\n".join(lines)
