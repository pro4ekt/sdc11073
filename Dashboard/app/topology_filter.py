"""
topology_filter.py — pure decision logic of the room filter (Ziel 1).

Three phases, one decision vocabulary:

    discovery         — the WS-Discovery scopes of a Hello/ProbeMatch name a location
                        (scheme ``sdc.ctxt.loc``, IEEE 11073-20701 §9.4); a foreign room
                        is rejected WITHOUT connecting (no TLS, no metadata, no
                        subscription).
    context_precheck  — no location scope: connect and read the context states
                        (GetContextStates) BEFORE any subscription; a foreign room is
                        rejected without a subscription.
    mdib              — the legacy check on the ConsumerMdib after GetMdib (kept as a
                        confirmation / for devices that reached it without a decision).

    decision ∈ {'accept', 'reject', 'unknown'}; 'unknown' = no location at all
    (accepted, counted separately — I9).

Parsing of location scopes is delegated to ``sdc11073.location.SdcLocation``; no own
URL parsing here.  The module has no Qt / threading dependencies so it can be unit
tested directly.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sdc11073.location import SdcLocation

LOCATION_SCHEME = SdcLocation.scheme          # 'sdc.ctxt.loc'

DECISION_ACCEPT = 'accept'
DECISION_REJECT = 'reject'
DECISION_UNKNOWN = 'unknown'


def location_from_scopes(scopes: Iterable[str]) -> Optional[SdcLocation]:
    """Return the first location scope of ``scopes`` parsed by sdc11073, or None.

    Non-location scopes (``sdc.mds.pkp:…``, ``sdc.cdc.type:…``) raise
    ``UrlSchemeError`` inside the library and are skipped; malformed location
    scopes are skipped as well.
    """
    for scope in scopes or ():
        try:
            return SdcLocation.from_scope_string(str(scope))
        except Exception:  # UrlSchemeError or malformed path/query
            continue
    return None


def room_from_scopes(scopes: Iterable[str]) -> Optional[str]:
    """Return the room (``rm`` query parameter) named by the location scope, or None."""
    loc = location_from_scopes(scopes)
    if loc is None:
        return None
    return loc.rm or None


def room_from_context_states(states: Iterable[object]) -> Optional[str]:
    """Return the room of the (associated) LocationContextState among ``states``.

    ``states`` are BICEPS state containers (from GetContextStates or the MDIB).
    An associated state is preferred; otherwise the first state with a room.
    """
    first_room: Optional[str] = None
    for s in states or ():
        node_type = getattr(s, 'NODETYPE', None)
        name = getattr(node_type, 'localname', '') if node_type is not None else ''
        if name != 'LocationContextState':
            continue
        detail = getattr(s, 'LocationDetail', None)
        room = getattr(detail, 'Room', None) if detail is not None else None
        if not room:
            continue
        assoc = str(getattr(s, 'ContextAssociation', '') or '')
        if assoc.lower().startswith('assoc'):
            return str(room)
        if first_room is None:
            first_room = str(room)
    return first_room


def decide_room(target_room: Optional[str], room: Optional[str]) -> str:
    """Map (filter, observed room) onto the decision vocabulary.

    No filter → 'accept' (baseline mode for FF1).  Filter set: unknown room →
    'unknown' (accepted, I9), equal → 'accept', different → 'reject'.
    """
    if not target_room:
        return DECISION_ACCEPT
    if not room:
        return DECISION_UNKNOWN
    return DECISION_ACCEPT if room == target_room else DECISION_REJECT
