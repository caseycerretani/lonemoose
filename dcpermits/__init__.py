"""dcpermits — an agent for tracking data-center construction through
publicly available building-permit data.

See :mod:`dcpermits.pipeline` for the agent loop and :mod:`dcpermits.cli`
for the command-line entry point.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .classify import Classifier, ClassifierConfig
from .models import Permit, SourceRef
from .pipeline import Agent, RunConfig

__all__ = [
    "Agent",
    "RunConfig",
    "Classifier",
    "ClassifierConfig",
    "Permit",
    "SourceRef",
    "__version__",
]
