"""Phase-gated warm worker barriers (D-8, D-11, P2 spec 2.3)."""
from functools import wraps

from nodesim_guard import Guard
from nodesim_proto import is_allowed_saas_host


class WarmGuard(Guard):
    """Keep the cold guard's audit rules with a replaceable sim-only lease (P2 spec 2.3)."""

    # The warm worker never imports a SaaS client (D-11, D-13); the broker owns it.
    SAAS_NAMES = ()

    def __init__(self, scratch):
        """Start without a lease or network permissions (D-8)."""
        super().__init__(scratch)
        self.phase = "idle"
        self.endpoints = frozenset()
        self.barriers = {}

    def remember(self, name, owner, attribute):
        """Record the exact installed descriptor, including classmethods (P2 spec 2.3)."""
        self.barriers[name] = (owner, attribute, vars(owner)[attribute])

    def check_barriers(self):
        """Refuse changed barriers before running another phase (P2 spec 2.3)."""
        for name, (owner, attribute, expected) in self.barriers.items():
            if vars(owner).get(attribute) is not expected:
                self.fail("barrier", name + " replaced")
        if self.verdict:
            raise RuntimeError(self.verdict)

    def transition(self, before, after):
        """Gate network and P1 loopback/registry relaxations by phase (P2 spec 2.3)."""
        if self.phase != before:
            self.fail("phase", self.phase + " -> " + after)
        self.check_barriers()
        self.phase = after
        self.allowed = after == "sim"
        self.allowed_endpoints = self.endpoints if self.allowed else frozenset()

    def enter_build(self):
        """Enter a network-free build (D-8)."""
        self.transition("idle", "build")

    def exit_build(self):
        """Check build-time tampering before releasing the lab lock (P2 spec 2.3)."""
        self.transition("build", "idle")

    def enter_sim(self):
        """Permit only the current lease's transport (D-11)."""
        self.transition("idle", "sim")

    def exit_sim(self):
        """Remove transport permissions between clicks (P2 spec 2.3)."""
        self.transition("sim", "idle")

    def allow(self, endpoints, write_roots=()):
        """The warm worker cannot use the cold widening API (D-8)."""
        self.fail("allow", "warm worker")

    def set_lease(self, params, endpoints):
        """The warm worker accepts leases only through replace_lease (D-11)."""
        self.fail("lease", "use replace_lease")

    def replace_lease(self, params, endpoints):
        """Validate the shared host rule and replace endpoints only while idle (D-11)."""
        if self.phase != "idle":
            raise ValueError("not between clicks")
        endpoints = frozenset(endpoints)
        if params is not None:
            if (not is_allowed_saas_host(params["host"], params["port"]) or not endpoints
                    or any(not is_allowed_saas_host(host, port) for host, port in endpoints)):
                raise ValueError("Only the cloud simulator is allowed.")
        self.lease_params = params
        self.endpoints = endpoints if params is not None else frozenset()
        self.allowed_endpoints = frozenset()
        self._loopback.clear()
        self._proxy_keys.clear()

    def lease(self):
        """QMM construction is permitted only during simulation (P2 spec 2.3)."""
        return self.lease_params if self.phase == "sim" else None

    def install_classes(self, lease=None):
        """Install cold barriers once and gate the kept QMM simulate method (P2 spec 2.3)."""
        super().install_classes()
        from qm import QuantumMachinesManager as QMM
        original = QMM.simulate

        @wraps(original)
        def simulate(instance, *args, **kwargs):
            """Refuse use of a kept native channel during builds (D-8)."""
            if self.phase != "sim":
                self.fail("QuantumMachinesManager.simulate", "outside sim")
            return original(instance, *args, **kwargs)

        QMM.simulate = simulate
        self.barrier_assignments.append(("simulate", QMM, "simulate", simulate))
        for name in ("__init__", "simulate", "open_qm"):
            self.remember(name, QMM, name)
        # FI4-1, R3-1: pin every captured module binding, so a node-made subclass is never a sim-time class.
        for key, (module, attribute) in self.sim_owners.items():
            self.remember(key, module, attribute)
