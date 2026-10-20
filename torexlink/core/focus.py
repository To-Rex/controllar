"""Authoritative focus arbitration for a two-device session.

``controller`` is the machine whose physical input is being used.
``target`` is the machine receiving that input. Both normally match, except
while the controller is driving the peer (target=peer, controller=self).
The network server is the single authority, preventing simultaneous ownership.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from ..constants import EDGES, OPPOSITE_EDGE


class FocusError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FocusState:
    epoch: int
    controller: str
    target: str
    reason: str
    entry_edge: str | None = None
    position: float | None = None


class FocusAuthority:
    def __init__(self, host_id: str, client_id: str) -> None:
        self.host_id = host_id
        self.client_id = client_id
        self.epoch = 0
        # The joining machine is active first; this is deterministic and lets
        # its user see immediate feedback after pairing.
        self.state = FocusState(
            epoch=self.epoch,
            controller=client_id,
            target=client_id,
            reason="initial",
        )

    def _peer(self, device_id: str) -> str:
        if device_id == self.host_id:
            return self.client_id
        if device_id == self.client_id:
            return self.host_id
        raise FocusError("unknown device in focus request")

    def initial(self) -> FocusState:
        return self.state

    def apply(
        self,
        requester: str,
        action: str,
        *,
        edge: str | None = None,
        position: float | None = None,
    ) -> FocusState:
        if requester not in (self.host_id, self.client_id):
            raise FocusError("unknown focus requester")
        if edge is not None and edge not in EDGES:
            raise FocusError("invalid edge")
        peer = self._peer(requester)
        current = self.state
        reason = "hotkey"
        entry_edge: str | None = None
        entry_position: float | None = None

        if action == "takeover":
            controller, target, reason = requester, requester, "takeover"
        elif action == "return":
            if requester != current.target or requester == current.controller:
                raise FocusError("focus return is not valid now")
            controller, target, reason = current.controller, current.controller, "remote-return"
        elif action == "edge":
            if requester != current.controller:
                raise FocusError("only the controller can cross an edge")
            if edge is None:
                raise FocusError("edge is required")
            controller, target, reason = requester, peer, "edge"
            entry_edge = OPPOSITE_EDGE[edge]
            entry_position = max(0.0, min(1.0, position if position is not None else 0.5))
        elif action == "toggle":
            if requester != current.controller:
                controller, target, reason = requester, requester, "takeover"
            elif current.target == requester:
                controller, target, reason = requester, peer, "hotkey"
                entry_edge = None
            else:
                controller, target, reason = requester, requester, "hotkey"
        else:
            raise FocusError("unknown focus action")

        self.epoch += 1
        self.state = FocusState(
            epoch=self.epoch,
            controller=controller,
            target=target,
            reason=reason,
            entry_edge=entry_edge,
            position=entry_position,
        )
        return self.state

    @staticmethod
    def request_id() -> str:
        return secrets.token_hex(8)


__all__ = ["FocusAuthority", "FocusError", "FocusState"]
