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

    def __str__(self):
        """Return the version name, as SDK 1.2.0 (D-47)."""
        return self.name


class ControllerConfig:
    """Record the shared spec mapping's controller (P2 spec 4.4)."""

    def __init__(self):
        """Begin an empty controller (P2 spec 4.4)."""
        self.slots = {}

    def mw_fems(self, *slots):
        """Record MW slots (P2 spec 4.4)."""
        self.add_slots(slots, "MW_FEM")
        return self

    def lf_fems(self, *slots):
        """Record LF slots (P2 spec 4.4)."""
        self.add_slots(slots, "LF_FEM")
        return self

    def add_slots(self, slots, kind):
        """Validate all slots before applying any, as SDK 1.2.0 (P2 spec 5)."""
        if len(set(slots)) != len(slots) or any(not 1 <= slot <= 8 or str(slot) in self.slots for slot in slots):
            raise ValueError("Invalid or repeated FEM slot")
        self.slots.update({str(slot): kind for slot in slots})

    def to_dict(self):
        """Return a stable ledger representation (P2 spec 4.4)."""
        return {"slots": self.slots}


class ClusterConfig:
    """Expose the SDK's public cluster builder (P2 spec 4.4)."""

    def __init__(self):
        """Start an empty cluster (P2 spec 4.4)."""
        self.controllers = {}

    def controller(self):
        """Add one controller, as in qm-saas 1.2.0 (P2 spec 4.4)."""
        controller = ControllerConfig()
        self.controllers["con" + str(len(self.controllers) + 1)] = controller
        return controller

    def to_dict(self):
        """Return the cluster's recorded mapping (P2 spec 4.4)."""
        return {"controllers": {key: value.to_dict() for key, value in self.controllers.items()}}


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
        result = self._client.launch_simulator(self._version, self._cluster.to_dict() if self._cluster else {})
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
        if protocol not in ("http", "https"):
            raise ValueError("Invalid protocol")
        self._client = Client(protocol, host, port, email, password, log)

    def versions(self):
        """Return the offered versions as version objects, as SDK 1.2.0 (D-47)."""
        return [QOPVersion(name) for name in self._client.versions()]

    def simulator(self, version=None, cluster_config=None):
        """Return an unspawned instance (D-17(a))."""
        normalized = version if isinstance(version, QOPVersion) else QOPVersion(version)
        if cluster_config is not None and normalized.major != 3:
            raise ValueError("Cluster configuration requires QoP v3")
        name = normalized.name
        return QmSaasInstance(self._client, name, cluster_config)
