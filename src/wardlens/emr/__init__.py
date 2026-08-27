"""Read-only EMR adapters."""

from wardlens.emr.base import EMRAdapter, EMRError, IncompleteFetchError, LoginError
from wardlens.emr.demo import DemoEMRAdapter
from wardlens.emr.vgh import VGHReadOnlyAdapter

__all__ = [
    "DemoEMRAdapter",
    "EMRAdapter",
    "EMRError",
    "IncompleteFetchError",
    "LoginError",
    "VGHReadOnlyAdapter",
]
