"""Shared pytest fixtures/configuration.

Adds the project root to ``sys.path`` so tests can import the ``core``,
``foxio`` and ``brain`` packages regardless of the invocation directory.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
