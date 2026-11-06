"""Shared test helpers."""

from types import SimpleNamespace
from typing import Any

# Test doubles stand in for library models and the coordinator by duck typing.
# Typed as Any so Pyright does not ask for the real classes.
Fake: Any = SimpleNamespace
