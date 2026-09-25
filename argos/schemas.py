"""
Pydantic Output Schemas — structured output models per agent.

Every LLM call uses structured output via .with_structured_output().
Every schema includes a reasoning/key_factors field for auditable rationale.
Confidence scores are floats 0–1, not vague labels.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Analyst signals (shared shape) ──────────────────────────────────────────

class AnalystSignal(BaseModel):
    """Output schema for all three analyst agents (Fundamentals, Sentiment, Technicals)."""

    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0–1")
    key_factors: list[str] = Field(
        max_length=5,
        description="Top factors driving the signal (max 5)",
    )
    reasoning: str = Field(
        description="Concise rationale for the signal",
    )
    # Populated by the Technicals node with the raw IndicatorSnapshot dict so
    # the decision journal has every fired indicator alongside the LLM's take.
    # Kept out of the LLM's structured output (default None; set by node code).
    indicator_snapshot: Optional[dict[str, Any]] = Field(
        default=None,
        description="Raw IndicatorSnapshot (technicals only); LLM does not emit this",
    )


# ── Debate schemas ───────────────────────────────────────────────────────────

class DebateArgument(BaseModel):
    """Output schema for Bull and Bear advocates.

    position is NOT hardcoded — each advocate analyzes the evidence and
    argues the side the evidence supports. If evidence is weak for their
    assigned side, they should say so with low confidence.
    """

    position: Literal["buy", "sell"]
    strongest_evidence: str = Field(
        description="The single strongest piece of evidence for this position",
    )
    rebuttal_of_opposing_view: str = Field(
        description="Direct rebuttal of the opposing advocate's specific claims",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0–1")
    reasoning: str = Field(
        description="Full rationale for the argument",
    )
    evidence_summary: str = Field(
        default="",
        description="Key numbers and data points cited in this argument",
    )


# ── Multi-horizon forecast (P17) ────────────────────────────────────────────

class HorizonForecast(BaseModel):
    """Direction + expected return + confidence bands for one horizon.

    Every scored (symbol, date) gets three of these — 1d / 1w / 1m — so
    the user can filter recommendations by horizon: "show me 1-month
    bullish picks with confidence > 0.7 and expected return > 5%".
    """

    horizon: Literal["1d", "1w", "1m"]
    direction: Literal["up", "down", "flat"]
    probability_up: float = Field(ge=0.0, le=1.0,
        description="Posterior probability the closing price will be higher at horizon-end")
    expected_return_pct: float = Field(
        description="Expected % return over the horizon (can be negative for down direction)")
    p10_return_pct: float = Field(
        description="10th percentile of the forecast distribution (bear case)")
    p50_return_pct: float = Field(
        description="Median forecast — the point estimate for 'projected profit'")
    p90_return_pct: float = Field(
        description="90th percentile (bull case)")
    confidence: float = Field(ge=0.0, le=1.0,
        description="Composite confidence in this forecast, 0.5 = coin flip")
    key_drivers: list[str] = Field(default_factory=list, max_length=5,
        description="Top signals driving this horizon's forecast")


class MultiHorizonForecast(BaseModel):
    """The forecast bundle — three horizons per symbol per date.

    This is the deliverable the user reads on the daily dashboard and
    filters by (per-day/per-week/per-month views).
    """
    symbol: str
    as_of_date: str
    last_close: float
    horizons: dict[str, HorizonForecast]  # keys: "1d", "1w", "1m"

    @property
    def strongest_horizon(self) -> str:
        """Which horizon has the highest conviction × |expected return|?"""
        if not self.horizons:
            return "1d"
        return max(
            self.horizons.keys(),
            key=lambda h: self.horizons[h].confidence * abs(self.horizons[h].expected_return_pct),
        )

    @property
    def any_high_conviction(self) -> bool:
        """True if ANY horizon has probability_up > 0.70 or < 0.30 with confidence > 0.65."""
        for h in self.horizons.values():
            if h.confidence > 0.65 and (h.probability_up > 0.70 or h.probability_up < 0.30):
                return True
        return False


# ── Exit Manager (U11) ──────────────────────────────────────────────────────

class ExitDecision(BaseModel):
    """Exit-manager output for open positions."""

    action: Literal["hold_position", "exit_at_market", "tighten_stop", "move_to_breakeven"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="≤200 words, cite specific indicators")
    new_stop_pct: Optional[float] = Field(
        default=None, ge=0.0, le=0.5,
        description="New stop as % of entry price; required for tighten_stop / move_to_breakeven",
    )


# ── Blind Judge (U1) ────────────────────────────────────────────────────────

class JudgeVerdict(BaseModel):
    """Independent judge that scores the debate transcript with side labels
    hidden. Its job is to name which argument (Argument A / Argument B) is
    more evidence-supported, and how confident. The Portfolio Manager treats
    this as a tiebreaker when Bull and Bear confidences are close.
    """

    winner: Literal["A", "B", "tie"] = Field(
        description="Which argument was stronger — A, B, or a genuine tie",
    )
    winner_side: Literal["bull", "bear", "tie"] = Field(
        description="Fill after side-mapping; the node computes this",
        default="tie",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Judge's confidence in the winner (0.5 = coin flip)",
    )
    reasoning: str = Field(description="Judge's rationale — cite specific claims by name")
    key_factor: str = Field(description="The single factor that tipped the verdict")


# ── Risk Manager ─────────────────────────────────────────────────────────────

class RiskVerdict(BaseModel):
    """Output schema for the Risk Manager."""

    veto: bool = Field(description="True if the trade is vetoed entirely")
    max_position_size: float = Field(
        ge=0.0,
        le=1.0,
        description="Maximum position size as fraction of portfolio (0.0 = no position)",
    )
    reasoning: str = Field(
        description="Reasoning for the verdict, including uncertainty assessment",
    )
    risk_factors: list[str] = Field(
        max_length=3,
        description="Key risk factors considered",
    )


# ── Portfolio Manager ────────────────────────────────────────────────────────

class FinalDecision(BaseModel):
    """Output schema for the Portfolio Manager — the final synthesis.

    U3: every trade decision now carries its OWN exit plan — stop, target,
    trailing distance, and time cap. The simulator honors all four. The
    Portfolio Manager fills them by combining ATR from the indicator snapshot
    with a target 2:1 reward/risk ratio.
    """

    action: Literal["buy", "sell", "hold"]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0–1")
    position_size: float = Field(
        ge=0.0,
        le=1.0,
        description="Recommended position size (must be ≤ risk manager's max)",
    )
    rationale: str = Field(
        description="Full synthesis rationale referencing upstream outputs",
    )
    dissent_notes: str = Field(
        default="",
        description="Any concerns or caveats about the final decision",
    )

    # ── U3 exit plan (populated for buy/sell; ignored for hold) ─────────
    stop_loss_pct: Optional[float] = Field(
        default=None, ge=0.0, le=0.5,
        description="Hard stop as % of entry price (typical: 1.5 × ATR/close). "
                    "If price moves against us by this %, exit at market.",
    )
    take_profit_pct: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Profit target as % of entry price (typical: 2 × stop_loss_pct "
                    "for a 2:1 R:R). If price reaches this %, exit at market.",
    )
    trailing_stop_atr: Optional[float] = Field(
        default=None, ge=0.0, le=10.0,
        description="Chandelier-style trailing stop distance in ATR units. "
                    "Activates once price moves 1×ATR in our favor.",
    )
    time_stop_bars: Optional[int] = Field(
        default=None, ge=0, le=60,
        description="If neither stop nor target hit within N bars, close and "
                    "re-evaluate. Default 5.",
    )
    # U2: audit fields populated by the node after Kelly sizing runs.
    # LLM's raw guess vs the derived Kelly size so the journal shows what
    # was overridden and why.
    sizing_trace: Optional[dict[str, Any]] = Field(default=None,
        description="Kelly-fractional sizing breakdown (populated by node)")
    sizing_llm_original: Optional[float] = Field(default=None,
        description="What the LLM originally guessed before Kelly override")
