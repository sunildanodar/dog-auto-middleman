"""
state_machine.py
────────────────
Centralized state system for the Dog Auto Middleman escrow bot.

Design goals:
  • Zero circular imports  – imports only from database.py and config.py
  • Single source of truth – all valid states and transitions live here
  • Backwards compatible  – maps directly onto the existing `status` TEXT column
  • Easy to extend        – add a new state in one place (EscrowState + TRANSITIONS)

Usage (in bot.py):
  from state_machine import EscrowState, TicketStateMachine, get_ticket_state

  ok = TicketStateMachine.advance(ticket_id, EscrowState.PAYMENT_PENDING)
  state = get_ticket_state(ticket_id)   # returns current EscrowState string
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Set

from database import get_ticket, log_event, update_ticket


# ──────────────────────────────────────────────────────────────────────────────
# 1.  STATE CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

class EscrowState:
    """
    All valid ticket states.

    Each constant maps to a string stored in the `tickets.status` DB column.
    New states can be added here without touching any other file.
    """

    # ── Flow states ────────────────────────────────────────────────────────────
    ROLE_SELECTION    = "waiting"           # Initial state after ticket creation
    ROLE_CONFIRMATION = "role_confirmed"    # Both users have selected roles
    AMOUNT_SET        = "amount_set"        # Sender set + both confirmed the USD amount
    PAYMENT_PENDING   = "pending_payment"   # Wallet generated, waiting for on-chain TX
    TX_DETECTED       = "unconfirmed"       # TX seen on chain, confirmations pending
    TX_CONFIRMED      = "paid"              # Required confirmations reached
    RELEASING         = "releasing"         # Withdrawal in progress
    COMPLETED         = "completed"         # Funds sent, deal closed

    # ── Terminal / exception states ────────────────────────────────────────────
    CANCELLED         = "cancelled"
    DISPUTED          = "disputed"

    # ── Convenience set: states where the ticket is still "live" ──────────────
    ACTIVE_STATES: Set[str] = {
        "waiting",
        "role_confirmed",
        "amount_set",
        "pending_payment",
        "unconfirmed",
        "paid",
        "releasing",
        "disputed",
    }

    # ── Convenience set: final states (no further transitions allowed) ─────────
    TERMINAL_STATES: Set[str] = {"completed", "cancelled"}

    @classmethod
    def all_states(cls) -> Set[str]:
        """Return the full set of known state strings."""
        return cls.ACTIVE_STATES | cls.TERMINAL_STATES

    @classmethod
    def is_valid(cls, state: str) -> bool:
        """Return True if *state* is a recognised EscrowState value."""
        return state in cls.all_states()


# ──────────────────────────────────────────────────────────────────────────────
# 2.  VALID TRANSITION MAP
# ──────────────────────────────────────────────────────────────────────────────

#  Keys   → current state
#  Values → set of states that can be reached from that state

VALID_TRANSITIONS: Dict[str, Set[str]] = {
    EscrowState.ROLE_SELECTION:    {EscrowState.ROLE_CONFIRMATION},
    EscrowState.ROLE_CONFIRMATION: {EscrowState.AMOUNT_SET},
    EscrowState.AMOUNT_SET:        {EscrowState.PAYMENT_PENDING},
    EscrowState.PAYMENT_PENDING:   {EscrowState.TX_DETECTED,  EscrowState.CANCELLED},
    EscrowState.TX_DETECTED:       {EscrowState.TX_CONFIRMED, EscrowState.CANCELLED},
    EscrowState.TX_CONFIRMED:      {EscrowState.RELEASING,    EscrowState.CANCELLED, EscrowState.DISPUTED},
    EscrowState.RELEASING:         {EscrowState.COMPLETED},

    # Disputed can be resolved to either side
    EscrowState.DISPUTED:          {EscrowState.TX_CONFIRMED, EscrowState.CANCELLED},

    # Terminal states have no outgoing transitions
    EscrowState.COMPLETED:         set(),
    EscrowState.CANCELLED:         set(),
}


# ──────────────────────────────────────────────────────────────────────────────
# 3.  IN-MEMORY STATE CACHE
# ──────────────────────────────────────────────────────────────────────────────

# Mirrors the DB value for fast access without hitting SQLite on every check.
# Key: ticket_id (int)  →  Value: current state string
_state_cache: Dict[int, str] = {}


# ──────────────────────────────────────────────────────────────────────────────
# 4.  CORE STATE MACHINE CLASS
# ──────────────────────────────────────────────────────────────────────────────

class TicketStateMachine:
    """
    Validate and execute state transitions for a single ticket.

    All public methods are *classmethods* so the caller never needs to
    instantiate this class — it acts as a namespace for state operations.
    """

    # ── Read ──────────────────────────────────────────────────────────────────

    @classmethod
    def get_state(cls, ticket_id: int) -> Optional[str]:
        """
        Return the current state of *ticket_id*.

        Checks the in-memory cache first; falls back to the database.
        Returns None if the ticket does not exist.
        """
        # Fast path: cache hit
        cached = _state_cache.get(ticket_id)
        if cached is not None:
            return cached

        # Slow path: DB read
        ticket = get_ticket(ticket_id)
        if ticket is None:
            return None

        state = str(ticket[6] or EscrowState.ROLE_SELECTION).strip().lower()
        _state_cache[ticket_id] = state
        return state

    # ── Validate ─────────────────────────────────────────────────────────────

    @classmethod
    def can_transition(cls, current_state: str, target_state: str) -> bool:
        """Return True if the *current_state* → *target_state* transition is valid."""
        allowed = VALID_TRANSITIONS.get(current_state, set())
        return target_state in allowed

    # ── Write ────────────────────────────────────────────────────────────────

    @classmethod
    def advance(
        cls,
        ticket_id: int,
        target_state: str,
        *,
        force: bool = False,
        actor_id: Optional[int] = None,
    ) -> bool:
        """
        Attempt to transition *ticket_id* to *target_state*.

        Parameters
        ----------
        ticket_id    : Ticket to update.
        target_state : Desired next state (use EscrowState constants).
        force        : If True, skip the transition-validity check (admin use only).
        actor_id     : Discord user ID that triggered the change (for audit log).

        Returns
        -------
        True if the transition was applied; False if it was rejected.
        """
        if not EscrowState.is_valid(target_state):
            print(f"[STATE] Unknown target state '{target_state}' for ticket {ticket_id}")
            return False

        current = cls.get_state(ticket_id)
        if current is None:
            print(f"[STATE] Ticket {ticket_id} not found — cannot advance state")
            return False

        # Skip transition if already in the target state (idempotent)
        if current == target_state:
            return True

        # Validate the transition unless forced
        if not force and not cls.can_transition(current, target_state):
            print(
                f"[STATE] Rejected invalid transition "
                f"'{current}' → '{target_state}' for ticket {ticket_id}"
            )
            return False

        # Apply: write to DB and update cache
        update_ticket(ticket_id, status=target_state)
        _state_cache[ticket_id] = target_state

        # Audit trail
        actor_part = f" actor={actor_id}" if actor_id else ""
        log_event(
            ticket_id,
            "state_transition",
            f"{current}→{target_state}{actor_part}",
        )
        print(f"[STATE] Ticket {ticket_id}: {current} → {target_state}")
        return True

    # ── Force (admin bypass) ─────────────────────────────────────────────────

    @classmethod
    def force_state(cls, ticket_id: int, target_state: str, actor_id: Optional[int] = None) -> bool:
        """
        Set state unconditionally (admin/recovery use only).
        Skips the transition-validity check; still validates that the
        target is a known state.
        """
        return cls.advance(ticket_id, target_state, force=True, actor_id=actor_id)

    # ── Invalidate cache ─────────────────────────────────────────────────────

    @classmethod
    def evict(cls, ticket_id: int) -> None:
        """Remove *ticket_id* from the in-memory cache (e.g. after ticket close)."""
        _state_cache.pop(ticket_id, None)

    @classmethod
    def evict_all(cls) -> None:
        """Clear the entire cache (e.g. on bot restart)."""
        _state_cache.clear()


# ──────────────────────────────────────────────────────────────────────────────
# 5.  CONVENIENCE HELPERS  (importable directly by bot.py)
# ──────────────────────────────────────────────────────────────────────────────

def get_ticket_state(ticket_id: int) -> Optional[str]:
    """Shorthand for TicketStateMachine.get_state(ticket_id)."""
    return TicketStateMachine.get_state(ticket_id)


def advance_ticket_state(
    ticket_id: int,
    new_state: str,
    *,
    actor_id: Optional[int] = None,
) -> bool:
    """
    Shorthand for TicketStateMachine.advance().

    Returns True on success, False if the transition was rejected.
    Drop-in ready for use inside any button callback or monitor coroutine.

    Example
    -------
    if not advance_ticket_state(ticket_id, EscrowState.PAYMENT_PENDING):
        # transition rejected — handle gracefully
        ...
    """
    return TicketStateMachine.advance(ticket_id, new_state, actor_id=actor_id)


def is_ticket_in_state(ticket_id: int, *states: str) -> bool:
    """
    Return True if the ticket's current state is any of *states*.

    Example
    -------
    if is_ticket_in_state(ticket_id, EscrowState.TX_CONFIRMED, EscrowState.RELEASING):
        ...
    """
    current = get_ticket_state(ticket_id)
    return current in states


def assert_state_or_raise(ticket_id: int, *allowed_states: str) -> str:
    """
    Return the current state if it is in *allowed_states*, else raise ValueError.

    Useful for button callbacks that must validate state before acting.

    Example
    -------
    try:
        state = assert_state_or_raise(ticket_id, EscrowState.TX_CONFIRMED)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    """
    current = get_ticket_state(ticket_id)
    if current not in allowed_states:
        friendly = " or ".join(f"'{s}'" for s in allowed_states)
        raise ValueError(
            f"Action not allowed in state '{current}'. Expected: {friendly}."
        )
    return current


# ──────────────────────────────────────────────────────────────────────────────
# 6.  STATE METADATA  (human-readable labels for embeds / logging)
# ──────────────────────────────────────────────────────────────────────────────

STATE_META: Dict[str, Dict] = {
    EscrowState.ROLE_SELECTION:    {"label": "Role Selection",    "emoji": "🎭", "color": 0x5865F2},
    EscrowState.ROLE_CONFIRMATION: {"label": "Role Confirmed",    "emoji": "✅", "color": 0x5865F2},
    EscrowState.AMOUNT_SET:        {"label": "Amount Set",        "emoji": "💵", "color": 0x5865F2},
    EscrowState.PAYMENT_PENDING:   {"label": "Waiting Payment",   "emoji": "⏳", "color": 0xF59E0B},
    EscrowState.TX_DETECTED:       {"label": "TX Detected",       "emoji": "⚠️", "color": 0xF59E0B},
    EscrowState.TX_CONFIRMED:      {"label": "TX Confirmed",      "emoji": "✅", "color": 0x3BA55C},
    EscrowState.RELEASING:         {"label": "Releasing",         "emoji": "📤", "color": 0x3BA55C},
    EscrowState.COMPLETED:         {"label": "Completed",         "emoji": "🏁", "color": 0x3BA55C},
    EscrowState.CANCELLED:         {"label": "Cancelled",         "emoji": "❌", "color": 0x475569},
    EscrowState.DISPUTED:          {"label": "Disputed",          "emoji": "⚖️", "color": 0xED4245},
}


def get_state_meta(state: str) -> Dict:
    """Return display metadata (label, emoji, color) for a given state string."""
    return STATE_META.get(state, {"label": state.title(), "emoji": "❓", "color": 0x2B2D31})
