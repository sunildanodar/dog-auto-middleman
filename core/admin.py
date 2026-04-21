"""
Admin Control System
Comprehensive admin panel and management commands
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import discord
from discord.ext import commands

from .state import DealState, StateManager
from .ticket import TicketManager
from .payment import PaymentProcessor, PaymentTracker
from .ui import UIComponents, EmbedBuilder


@dataclass
class AdminAction:
    """Admin action logging"""
    admin_id: int
    action: str
    target_ticket_id: Optional[int]
    details: str
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'admin_id': self.admin_id,
            'action': self.action,
            'target_ticket_id': self.target_ticket_id,
            'details': self.details,
            'timestamp': self.timestamp.isoformat()
        }


class AdminPanel:
    """Main admin control panel"""
    
    def __init__(self, bot: commands.Bot, state_manager: StateManager, 
                 ticket_manager: TicketManager, payment_processor: PaymentProcessor,
                 payment_tracker: PaymentTracker, admin_id: int):
        self.bot = bot
        self.state_manager = state_manager
        self.ticket_manager = ticket_manager
        self.payment_processor = payment_processor
        self.payment_tracker = payment_tracker
        self.admin_id = admin_id
        self.action_log: List[AdminAction] = []
    
    def is_admin(self, user: discord.User) -> bool:
        """Check if user is admin"""
        return user.id == self.admin_id or (user.guild_permissions and user.guild_permissions.administrator)
    
    def log_action(self, admin: discord.User, action: str, target_ticket_id: Optional[int] = None, details: str = ""):
        """Log admin action"""
        action_log = AdminAction(
            admin_id=admin.id,
            action=action,
            target_ticket_id=target_ticket_id,
            details=details,
            timestamp=datetime.now(timezone.utc)
        )
        self.action_log.append(action_log)
    
    async def force_release(self, admin: discord.User, ticket_id: int, seller_address: str) -> tuple[bool, str]:
        """Force release funds to seller"""
        if not self.is_admin(admin):
            return False, "Admin access required"
        
        try:
            # Get payment info
            payment_info = self.payment_processor.active_payments.get(ticket_id)
            if not payment_info:
                return False, "Payment not found"
            
            # Release funds
            success, result = await self.payment_processor.release_funds(ticket_id, seller_address)
            
            if success:
                # Update state
                await self.state_manager.transition_deal(ticket_id, DealState.COMPLETED, user_id=admin.id)
                
                # Log action
                self.log_action(admin, "FORCE_RELEASE", ticket_id, f"Released to {seller_address[:10]}...")
                
                # Close ticket
                await self.ticket_manager.close_ticket(ticket_id, "admin_released")
                
                return True, f"Funds released successfully. TXID: {result}"
            else:
                return False, f"Release failed: {result}"
                
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def force_cancel(self, admin: discord.User, ticket_id: int, reason: str = "") -> tuple[bool, str]:
        """Force cancel ticket"""
        if not self.is_admin(admin):
            return False, "Admin access required"
        
        try:
            # Update state
            await self.state_manager.transition_deal(ticket_id, DealState.CANCELLED, user_id=admin.id)
            
            # Log action
            self.log_action(admin, "FORCE_CANCEL", ticket_id, reason)
            
            # Close ticket
            await self.ticket_manager.close_ticket(ticket_id, "admin_cancelled")
            
            # Cleanup payment
            await self.payment_tracker.cleanup_payment(ticket_id)
            
            return True, "Ticket cancelled successfully"
            
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def refund_payment(self, admin: discord.User, ticket_id: int, refund_address: str) -> tuple[bool, str]:
        """Refund payment to buyer"""
        if not self.is_admin(admin):
            return False, "Admin access required"
        
        try:
            # Get payment info
            payment_info = self.payment_processor.active_payments.get(ticket_id)
            if not payment_info:
                return False, "Payment not found"
            
            # Refund (similar to release but to different address)
            success, result = await self.payment_processor.release_funds(ticket_id, refund_address)
            
            if success:
                # Update state
                await self.state_manager.transition_deal(ticket_id, DealState.CANCELLED, user_id=admin.id)
                
                # Log action
                self.log_action(admin, "REFUND", ticket_id, f"Refunded to {refund_address[:10]}...")
                
                # Close ticket
                await self.ticket_manager.close_ticket(ticket_id, "refunded")
                
                return True, f"Refund sent successfully. TXID: {result}"
            else:
                return False, f"Refund failed: {result}"
                
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def get_ticket_info(self, admin: discord.User, ticket_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed ticket information"""
        if not self.is_admin(admin):
            return None
        
        ticket = await self.ticket_manager.get_ticket(ticket_id)
        if not ticket:
            return None
        
        payment_info = await self.payment_tracker.get_payment_status(ticket_id)
        state_machine = self.state_manager.get_deal_state(ticket_id)
        
        return {
            'ticket': ticket,
            'payment': payment_info,
            'state': state_machine.current_state if state_machine else None,
            'state_history': state_machine.state_history if state_machine else []
        }
    
    async def get_system_stats(self, admin: discord.User) -> Optional[Dict[str, Any]]:
        """Get system statistics"""
        if not self.is_admin(admin):
            return None
        
        # Get deal stats
        deal_stats = self.state_manager.get_deal_stats()
        
        # Get payment stats
        payment_stats = {
            'active_payments': len(self.payment_processor.active_payments),
            'monitoring_tasks': len(self.payment_tracker.monitoring_tasks),
            'by_status': {}
        }
        
        for payment in self.payment_processor.active_payments.values():
            status = payment.status.value
            payment_stats['by_status'][status] = payment_stats['by_status'].get(status, 0) + 1
        
        # Get recent admin actions
        recent_actions = [action for action in self.action_log 
                         if (datetime.now(timezone.utc) - action.timestamp).total_seconds() < 86400]
        
        return {
            'deals': deal_stats,
            'payments': payment_stats,
            'admin_actions': {
                'total': len(self.action_log),
                'last_24h': len(recent_actions),
                'recent': recent_actions[-10:]  # Last 10 actions
            },
            'tickets': {
                'active': len(self.ticket_manager.active_tickets),
                'by_status': {}
            }
        }


class AdminCommands:
    """Admin command implementations"""
    
    def __init__(self, bot: commands.Bot, admin_panel: AdminPanel):
        self.bot = bot
        self.admin_panel = admin_panel
    
    async def admin_panel_command(self, ctx: commands.Context):
        """Show admin control panel"""
        if not self.admin_panel.is_admin(ctx.author):
            await ctx.send("Admin access required.", ephemeral=True)
            return
        
        stats = await self.admin_panel.get_system_stats(ctx.author)
        if not stats:
            await ctx.send("Failed to get system stats.", ephemeral=True)
            return
        
        embed = UIComponents.create_embed(
            title="Admin Control Panel",
            description="System overview and controls",
            color=UIComponents.UIColors.INFO
        )
        
        # Deal stats
        deal_stats = stats['deals']
        embed.add_field(
            name="Active Deals",
            value=f"Total: {deal_stats['total_active']}\nExpiring Soon: {deal_stats['expiring_soon']}",
            inline=True
        )
        
        # Payment stats
        payment_stats = stats['payments']
        embed.add_field(
            name="Payments",
            value=f"Active: {payment_stats['active_payments']}\nMonitoring: {payment_stats['monitoring_tasks']}",
            inline=True
        )
        
        # Admin actions
        admin_stats = stats['admin_actions']
        embed.add_field(
            name="Admin Activity",
            value=f"Today: {admin_stats['last_24h']}\nTotal: {admin_stats['total']}",
            inline=True
        )
        
        await ctx.send(embed=embed, view=self._create_admin_view())
    
    async def ticket_info_command(self, ctx: commands.Context, ticket_id: int):
        """Get detailed ticket information"""
        if not self.admin_panel.is_admin(ctx.author):
            await ctx.send("Admin access required.", ephemeral=True)
            return
        
        info = await self.admin_panel.get_ticket_info(ctx.author, ticket_id)
        if not info:
            await ctx.send(f"Ticket #{ticket_id} not found.", ephemeral=True)
            return
        
        ticket = info['ticket']
        payment = info['payment']
        state = info['state']
        
        embed = UIComponents.create_embed(
            title=f"Ticket #{ticket_id} Information",
            color=UIComponents.UIColors.INFO
        )
        
        # Basic info
        embed.add_field(
            name="Basic Info",
            value=f"Creator: <@{ticket.creator_id}>\n"
                  f"Buyer: <@{ticket.buyer_id}>\n"
                  f"Seller: <@{ticket.seller_id}>\n"
                  f"Status: {ticket.status}",
            inline=False
        )
        
        # Deal info
        if ticket.amount_usd and ticket.crypto:
            embed.add_field(
                name="Deal Details",
                value=f"Amount: ${ticket.amount_usd:.2f}\n"
                      f"Crypto: {ticket.crypto}\n"
                      f"State: {state.name if state else 'Unknown'}",
                inline=False
            )
        
        # Payment info
        if payment:
            embed.add_field(
                name="Payment Info",
                value=f"Status: {payment.status.value}\n"
                      f"Address: `{payment.address[:20]}...`\n"
                      f"Created: {payment.created_at.strftime('%Y-%m-%d %H:%M')}",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    async def force_release_command(self, ctx: commands.Context, ticket_id: int, address: str):
        """Force release funds"""
        if not self.admin_panel.is_admin(ctx.author):
            await ctx.send("Admin access required.", ephemeral=True)
            return
        
        # Confirmation modal
        modal = UIComponents.create_modal(
            title="Confirm Force Release",
            fields=[
                {
                    "name": "confirmation",
                    "label": f"Type 'CONFIRM' to release funds for ticket #{ticket_id}",
                    "placeholder": "CONFIRM",
                    "style": discord.TextStyle.short,
                    "required": True
                }
            ]
        )
        
        async def on_submit(interaction: discord.Interaction):
            if modal.confirmation.value.upper() != "CONFIRM":
                await interaction.response.send_message("Confirmation cancelled.", ephemeral=True)
                return
            
            success, message = await self.admin_panel.force_release(ctx.author, ticket_id, address)
            
            if success:
                embed = UIComponents.create_embed(
                    title="Force Release Successful",
                    description=message,
                    color=UIComponents.UIColors.SUCCESS
                )
            else:
                embed = UIComponents.create_embed(
                    title="Force Release Failed",
                    description=message,
                    color=UIComponents.UIColors.ERROR
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        modal.on_submit = on_submit
        await ctx.response.send_modal(modal)
    
    async def force_cancel_command(self, ctx: commands.Context, ticket_id: int, reason: str = ""):
        """Force cancel ticket"""
        if not self.admin_panel.is_admin(ctx.author):
            await ctx.send("Admin access required.", ephemeral=True)
            return
        
        success, message = await self.admin_panel.force_cancel(ctx.author, ticket_id, reason)
        
        if success:
            embed = UIComponents.create_embed(
                title="Ticket Cancelled",
                description=message,
                color=UIComponents.UIColors.SUCCESS
            )
        else:
            embed = UIComponents.create_embed(
                title="Cancellation Failed",
                description=message,
                color=UIComponents.UIColors.ERROR
            )
        
        await ctx.send(embed=embed, ephemeral=True)
    
    def _create_admin_view(self) -> discord.ui.View:
        """Create admin control view"""
        view = discord.ui.View(timeout=None)
        
        # Quick actions
        view.add_item(discord.ui.Button(
            label="View Active Tickets",
            style=discord.ButtonStyle.primary,
            custom_id="admin_view_tickets"
        ))
        
        view.add_item(discord.ui.Button(
            label="System Stats",
            style=discord.ButtonStyle.secondary,
            custom_id="admin_stats"
        ))
        
        view.add_item(discord.ui.Button(
            label="Recent Actions",
            style=discord.ButtonStyle.secondary,
            custom_id="admin_actions"
        ))
        
        return view
