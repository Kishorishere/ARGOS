# Argos — A Multi-Agent Trading Research Desk

Argos is a seven-agent research desk that debates a single trade idea per ticker, per day, across a small universe of tickers. Six of the seven agents are large language models performing different flavors of reasoning — fundamentals, sentiment, technicals, bullish advocacy, bearish advocacy, risk oversight, and final portfolio synthesis. A seventh gate short-circuits to HOLD when the Risk Manager vetoes.

Long-term goal: hand `FinalDecision` outputs into the sibling `nepse-quant-terminal` project as `SignalEnvelope` payloads for paper / live execution on the Nepal Stock Exchange.
