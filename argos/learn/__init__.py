"""Self-learning module — the ledger + reweighter that lets Argos improve
from its own journal every night.
"""

from argos.learn.signal_ledger import (
    SignalLedger,
    load_ledger,
    update_from_journal,
    get_weight_multiplier,
)

__all__ = ["SignalLedger", "load_ledger", "update_from_journal", "get_weight_multiplier"]
