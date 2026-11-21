"""Offline qm-saas 1.2.0 replacement, loaded only by pytest (P2 spec 5)."""

from .client import ClusterConfig, ControllerConfig, QmSaas, QmSaasInstance, QOPVersion

__version__ = "1.2.0"
__all__ = ["ClusterConfig", "ControllerConfig", "QmSaas", "QmSaasInstance", "QOPVersion"]
