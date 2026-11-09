"""Environmental state types (implemented in Rust).

Classes:
    EnvironmentalState: A discrete environmental condition. ``metadata`` must
        be JSON-compatible; the ``metadata`` attribute returns a copy.
    StateTransition: Records a change from one state to another.

Transition states (``is_transition=True``) indicate the system is moving
between stable states; threshold evaluation is typically suspended.
"""

from hwtest_core._native import EnvironmentalState, StateTransition

__all__ = ["EnvironmentalState", "StateTransition"]
