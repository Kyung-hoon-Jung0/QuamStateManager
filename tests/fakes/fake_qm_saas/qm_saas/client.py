"""Minimal public qm-saas 1.2.0 surface for P2 spec 5 piece K."""

import datetime as dt
import re
import ssl

from .api import Client


class QOPVersion:
    """Expose the SDK's public version name (P2 spec 5)."""

    def __init__(self, name):
        """Validate the documented version shape (P2 spec 5)."""
        match = re.fullmatch(r"v(\d+)_(\d+)_(\d+)", name)
        if match is None:
            raise ValueError("Invalid version name")
        self.name = name
        self.major, self.minor, self.patch = map(int, match.groups())


class ControllerConfig:
    """Record the shared spec mapping's controller (P2 spec 4.4)."""

    def __init__(self):
        """Begin an empty controller (P2 spec 4.4)."""
        self.slots = {"mw": [], "lf": []}

    def mw_fems(self, *slots):
        """Record MW slots (P2 spec 4.4)."""
        self.slots["mw"] = list(slots)
        return self

    def lf_fems(self, *slots):
        """Record LF slots (P2 spec 4.4)."""
        self.slots["lf"] = list(slots)
        return self

    def to_dict(self):
        """Return a stable ledger representation (P2 spec 4.4)."""
        return self.slots


class ClusterConfig:
    """Expose the SDK's public cluster builder (P2 spec 4.4)."""

    def __init__(self):
        """Start an empty cluster (P2 spec 4.4)."""
        self.controllers = []

    def controller(self):
        """Add one controller, as in qm-saas 1.2.0 (P2 spec 4.4)."""
        controller = ControllerConfig()
        self.controllers.append(controller)
        return controller

    def to_dict(self):
        """Return the cluster's recorded mapping (P2 spec 4.4)."""
        return [controller.to_dict() for controller in self.controllers]


class QmSaasInstance:
    """Match the SDK properties the broker reads (P2 spec 3.3)."""

    def __init__(self, client, version, cluster_config):
        """Delay the POST until spawn (D-17(a))."""
        self._client, self._version, self._cluster = client, version, cluster_config
        self.id = self.token = self.host = self.port = self.expires_at = None

    def spawn(self):
        """Map the server response exactly as qm-saas does (P2 spec 3.5)."""
        if self.id:
            return
        result = self._client.launch_simulator(self._version, self._cluster.to_dict())
        self.id, self.token, self.host, self.port = (result[key] for key in ("id", "token", "host", "port"))
        self.expires_at = dt.datetime.fromisoformat(result["expires_at"])

    def close(self):
        """Expose the instance's idempotent close surface (P2 spec 3.3)."""
        if self.id:
            self._client.close_simulator(self.id)
            self.id = None

    @property
    def qmm_connection_params(self):
        """Match SDK TLS construction for worker-side comparison (P2 spec 3.5)."""
        context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["h2"])
        return dict(host=self.host, port=self.port,
                    connection_headers={"simulation-id": self.id, "simulation-auth": self.token},
                    credentials=context, follow_gateway_redirections=False)


class QmSaas:
    """Expose only the broker's qm-saas entry points (P2 spec 3.1)."""

    def __init__(self, host, port, email, password, auto_cleanup=True, log=None, protocol="https"):
        """Create the logged-in API client (D-11)."""
        self._client = Client(protocol, host, port, email, password, log)

    def simulator(self, version=None, cluster_config=None):
        """Return an unspawned instance (D-17(a))."""
        name = version.name if isinstance(version, QOPVersion) else version
        return QmSaasInstance(self._client, name, cluster_config)
