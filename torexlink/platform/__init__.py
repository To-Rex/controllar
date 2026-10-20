"""OS integration: permissions, autostart and firewall."""

from .autostart import AutostartManager
from .diagnostics import DiagnosticReport
from .firewall import FirewallManager
from .permissions import PermissionManager

__all__ = [
    "AutostartManager",
    "DiagnosticReport",
    "FirewallManager",
    "PermissionManager",
]
