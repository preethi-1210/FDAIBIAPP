from typing import TYPE_CHECKING, Any

__all__ = ["Orchestrator"]

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


def __getattr__(name: str) -> Any:
    if name == "Orchestrator":
        from .orchestrator import Orchestrator

        return Orchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
