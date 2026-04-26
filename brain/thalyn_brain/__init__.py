"""Thalyn agent brain sidecar."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("thalyn-brain")
except PackageNotFoundError:  # local checkout without an installed dist
    __version__ = "0.0.0"

__all__ = ["__version__"]
