"""Event registry — unified view over corporate/insider events per symbol.

Consumer surface:
    from argos.events import EventRecord, load_events, events_in_last
"""

from argos.events.registry import EventRecord, load_events, events_in_last

__all__ = ["EventRecord", "load_events", "events_in_last"]
