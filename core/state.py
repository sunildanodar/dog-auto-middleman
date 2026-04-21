"""
State Management System
Clean deal state management with transitions and validation
"""

from enum import Enum, auto
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio


class DealState(Enum):
    """Deal states with clear transitions"""
    CREATED = auto()
    WAITING_ROLES = auto()
    WAITING_AMOUNT = auto()
    WAITING_PAYMENT = auto()
    PAYMENT_DETECTED = auto()
    PAYMENT_CONFIRMED = auto()
    FUNDED = auto()
    RELEASE_PENDING = auto()
    COMPLETED = auto()
    DISPUTED = auto()
    CANCELLED = auto()
    EXPIRED = auto()


@dataclass
class StateTransition:
    """State transition configuration"""
    from_state: DealState
    to_state: DealState
    validator: Optional[Callable] = None
    action: Optional[Callable] = None
    requires_admin: bool = False
    timeout_seconds: Optional[int] = None


class StateMachine:
    """Clean state machine for deal management"""
    
    def __init__(self):
        self.transitions: Dict[DealState, List[StateTransition]] = {}
        self.current_state: DealState = DealState.CREATED
        self.state_history: List[tuple] = []
        self.timeouts: Dict[DealState, datetime] = {}
        self._setup_transitions()
    
    def _setup_transitions(self):
        """Setup allowed state transitions"""
        transitions = [
            # Initial flow
            StateTransition(DealState.CREATED, DealState.WAITING_ROLES),
            StateTransition(DealState.WAITING_ROLES, DealState.WAITING_AMOUNT),
            StateTransition(DealState.WAITING_AMOUNT, DealState.WAITING_PAYMENT),
            
            # Payment flow
            StateTransition(DealState.WAITING_PAYMENT, DealState.PAYMENT_DETECTED),
            StateTransition(DealState.PAYMENT_DETECTED, DealState.PAYMENT_CONFIRMED),
            StateTransition(DealState.PAYMENT_CONFIRMED, DealState.FUNDED),
            
            # Completion flow
            StateTransition(DealState.FUNDED, DealState.RELEASE_PENDING),
            StateTransition(DealState.RELEASE_PENDING, DealState.COMPLETED),
            
            # Admin controls
            StateTransition(DealState.FUNDED, DealState.DISPUTED, requires_admin=True),
            StateTransition(DealState.DISPUTED, DealState.COMPLETED, requires_admin=True),
            StateTransition(DealState.DISPUTED, DealState.CANCELLED, requires_admin=True),
            
            # Cancellation flow
            StateTransition(DealState.CREATED, DealState.CANCELLED),
            StateTransition(DealState.WAITING_ROLES, DealState.CANCELLED),
            StateTransition(DealState.WAITING_AMOUNT, DealState.CANCELLED),
            StateTransition(DealState.WAITING_PAYMENT, DealState.CANCELLED),
            
            # Expiration
            StateTransition(DealState.WAITING_PAYMENT, DealState.EXPIRED, timeout_seconds=1200),
        ]
        
        for transition in transitions:
            if transition.from_state not in self.transitions:
                self.transitions[transition.from_state] = []
            self.transitions[transition.from_state].append(transition)
    
    def can_transition_to(self, target_state: DealState, user_id: Optional[int] = None) -> tuple[bool, str]:
        """Check if transition is allowed"""
        if self.current_state not in self.transitions:
            return False, "No transitions defined for current state"
        
        for transition in self.transitions[self.current_state]:
            if transition.to_state == target_state:
                # Check admin requirement
                if transition.requires_admin and user_id is None:
                    return False, "Admin access required"
                
                # Run validator if present
                if transition.validator:
                    try:
                        result = transition.validator()
                        if not result:
                            return False, "Validation failed"
                    except Exception as e:
                        return False, f"Validation error: {e}"
                
                return True, "Transition allowed"
        
        return False, "Invalid transition"
    
    async def transition_to(self, target_state: DealState, user_id: Optional[int] = None, context: Optional[Dict] = None) -> bool:
        """Execute state transition"""
        can_transition, reason = self.can_transition_to(target_state, user_id)
        if not can_transition:
            raise ValueError(f"Cannot transition to {target_state}: {reason}")
        
        # Record transition
        old_state = self.current_state
        self.state_history.append((old_state, target_state, datetime.now(timezone.utc), user_id))
        
        # Update state
        self.current_state = target_state
        
        # Clear old timeout
        if old_state in self.timeouts:
            del self.timeouts[old_state]
        
        # Set new timeout if applicable
        transition = next((t for t in self.transitions.get(old_state, []) if t.to_state == target_state), None)
        if transition and transition.timeout_seconds:
            self.timeouts[target_state] = datetime.now(timezone.utc).timestamp() + transition.timeout_seconds
        
        # Execute action if present
        if transition and transition.action:
            try:
                await transition.action(context or {})
            except Exception as e:
                # Log error but don't fail the transition
                print(f"Error executing transition action: {e}")
        
        return True
    
    def get_timeout_state(self) -> Optional[DealState]:
        """Check for expired timeouts"""
        now = datetime.now(timezone.utc).timestamp()
        for state, timeout_time in self.timeouts.items():
            if now >= timeout_time:
                return state
        return None
    
    def is_final_state(self) -> bool:
        """Check if current state is final"""
        return self.current_state in [DealState.COMPLETED, DealState.CANCELLED, DealState.EXPIRED]
    
    def get_allowed_transitions(self, user_id: Optional[int] = None) -> List[DealState]:
        """Get list of states we can transition to"""
        allowed = []
        if self.current_state in self.transitions:
            for transition in self.transitions[self.current_state]:
                if not transition.requires_admin or user_id is not None:
                    can_transition, _ = self.can_transition_to(transition.to_state, user_id)
                    if can_transition:
                        allowed.append(transition.to_state)
        return allowed


class StateManager:
    """High-level state management for deals"""
    
    def __init__(self):
        self.machines: Dict[int, StateMachine] = {}  # ticket_id -> state_machine
        self._cleanup_task: Optional[asyncio.Task] = None
        self.start_cleanup_task()
    
    def create_deal(self, ticket_id: int) -> StateMachine:
        """Create new deal state machine"""
        machine = StateMachine()
        self.machines[ticket_id] = machine
        return machine
    
    def get_deal_state(self, ticket_id: int) -> Optional[StateMachine]:
        """Get deal state machine"""
        return self.machines.get(ticket_id)
    
    def remove_deal(self, ticket_id: int):
        """Remove completed deal"""
        if ticket_id in self.machines:
            del self.machines[ticket_id]
    
    async def transition_deal(self, ticket_id: int, target_state: DealState, user_id: Optional[int] = None, context: Optional[Dict] = None) -> bool:
        """Transition deal to new state"""
        machine = self.get_deal_state(ticket_id)
        if not machine:
            raise ValueError(f"No deal found for ticket {ticket_id}")
        
        return await machine.transition_to(target_state, user_id, context)
    
    def start_cleanup_task(self):
        """Start background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def _cleanup_loop(self):
        """Background task to handle timeouts and cleanup"""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                expired_deals = []
                for ticket_id, machine in self.machines.items():
                    timeout_state = machine.get_timeout_state()
                    if timeout_state:
                        expired_deals.append((ticket_id, timeout_state))
                
                # Handle expired deals
                for ticket_id, timeout_state in expired_deals:
                    machine = self.machines.get(ticket_id)
                    if machine and not machine.is_final_state():
                        await machine.transition_to(DealState.EXPIRED)
                        print(f"Deal {ticket_id} expired from {timeout_state}")
                
                # Clean up completed deals (older than 1 hour)
                cleanup_deals = []
                for ticket_id, machine in self.machines.items():
                    if machine.is_final_state():
                        # Check if completed more than 1 hour ago
                        last_transition = machine.state_history[-1] if machine.state_history else None
                        if last_transition:
                            transition_time = last_transition[2]
                            if (datetime.now(timezone.utc) - transition_time).total_seconds() > 3600:
                                cleanup_deals.append(ticket_id)
                
                for ticket_id in cleanup_deals:
                    del self.machines[ticket_id]
                    print(f"Cleaned up completed deal {ticket_id}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in cleanup loop: {e}")
    
    def stop_cleanup_task(self):
        """Stop background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
    
    def get_deal_stats(self) -> Dict[str, Any]:
        """Get statistics about active deals"""
        stats = {
            'total_active': len(self.machines),
            'by_state': {},
            'expiring_soon': 0,
        }
        
        for machine in self.machines.values():
            state_name = machine.current_state.name
            stats['by_state'][state_name] = stats['by_state'].get(state_name, 0) + 1
            
            # Check for deals expiring in next 5 minutes
            for state, timeout_time in machine.timeouts.items():
                if (timeout_time - datetime.now(timezone.utc).timestamp()) < 300:
                    stats['expiring_soon'] += 1
        
        return stats
