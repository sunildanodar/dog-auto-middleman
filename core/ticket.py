"""
Ticket Management System
Clean ticket creation, management, and role assignment
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
import discord
from discord.ext import commands

from .state import DealState, StateManager
from .ui import UIComponents, EmbedBuilder, ViewTemplates


@dataclass
class Ticket:
    """Ticket data model"""
    ticket_id: int
    channel_id: int
    creator_id: int
    buyer_id: Optional[int] = None
    seller_id: Optional[int] = None
    crypto: Optional[str] = None
    amount_usd: Optional[float] = None
    amount_crypto: Optional[float] = None
    status: str = "created"
    wallet_address: Optional[str] = None
    encrypted_private: Optional[str] = None
    seller_address: Optional[str] = None
    message_id: Optional[int] = None
    description: Optional[str] = None
    deal_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            'ticket_id': self.ticket_id,
            'channel_id': self.channel_id,
            'creator_id': self.creator_id,
            'buyer_id': self.buyer_id,
            'seller_id': self.seller_id,
            'crypto': self.crypto,
            'amount': self.amount_usd,
            'status': self.status,
            'wallet_address': self.wallet_address,
            'encrypted_private': self.encrypted_private,
            'seller_address': self.seller_address,
            'message_id': self.message_id,
            'description': self.description,
            'deal_id': self.deal_id,
            'locked_amount_crypto': self.amount_crypto
        }
    
    @classmethod
    def from_db_row(cls, row: tuple) -> 'Ticket':
        """Create ticket from database row"""
        if len(row) < 14:
            raise ValueError("Invalid database row format")
        
        return cls(
            ticket_id=row[0],
            channel_id=row[1], 
            creator_id=row[2] if row[2] != row[3] else row[2],  # Use buyer as creator if different
            buyer_id=row[2],
            seller_id=row[3],
            crypto=row[4],
            amount_usd=row[5],
            status=row[6],
            wallet_address=row[7],
            encrypted_private=row[8],
            seller_address=row[9],
            message_id=row[10],
            description=row[11],
            deal_id=row[12],
            amount_crypto=row[13] if len(row) > 13 else None
        )


class TicketManager:
    """High-level ticket management"""
    
    def __init__(self, bot: commands.Bot, state_manager: StateManager):
        self.bot = bot
        self.state_manager = state_manager
        self.active_tickets: Dict[int, Ticket] = {}
        self.pending_creations: Dict[int, Dict] = {}  # user_id -> creation_data
    
    async def create_ticket(
        self,
        creator: discord.User,
        crypto: str,
        trader_user: str,
        you_give: str,
        trader_gives: str,
        category_id: int
    ) -> Ticket:
        """Create new ticket with clean flow"""
        try:
            # Get next ticket ID
            ticket_id = await self._get_next_ticket_id()
            
            # Create ticket channel
            channel = await self._create_ticket_channel(creator, ticket_id, category_id)
            
            # Create ticket object
            ticket = Ticket(
                ticket_id=ticket_id,
                channel_id=channel.id,
                creator_id=creator.id,
                crypto=crypto,
                description=f"You give: {you_give} | Trader gives: {trader_gives}"
            )
            
            # Store ticket
            self.active_tickets[ticket_id] = ticket
            
            # Create state machine
            self.state_manager.create_deal(ticket_id)
            
            # Send startup embed
            await self._send_ticket_startup(channel, ticket, creator)
            
            # Store creation data for role selection
            self.pending_creations[creator.id] = {
                'ticket_id': ticket_id,
                'trader_user': trader_user,
                'you_give': you_give,
                'trader_gives': trader_gives
            }
            
            return ticket
            
        except Exception as e:
            raise RuntimeError(f"Failed to create ticket: {e}")
    
    async def assign_roles(self, user: discord.User, role: str, ticket_id: int) -> bool:
        """Assign buyer/seller roles"""
        ticket = self.active_tickets.get(ticket_id)
        if not ticket:
            return False
        
        # Update ticket roles
        if role.lower() == "sender":
            ticket.buyer_id = user.id
        elif role.lower() == "receiver":
            ticket.seller_id = user.id
        else:
            return False
        
        # Check if both roles assigned
        if ticket.buyer_id and ticket.seller_id:
            # Transition to amount setting
            await self.state_manager.transition_deal(ticket_id, DealState.WAITING_AMOUNT)
            await self._send_amount_prompt(ticket)
        
        return True
    
    async def set_amount(self, ticket_id: int, amount_usd: float) -> bool:
        """Set trade amount"""
        ticket = self.active_tickets.get(ticket_id)
        if not ticket:
            return False
        
        ticket.amount_usd = amount_usd
        ticket.updated_at = datetime.now(timezone.utc)
        
        # Transition to payment waiting
        await self.state_manager.transition_deal(ticket_id, DealState.WAITING_PAYMENT)
        
        # Generate payment address and send payment info
        await self._setup_payment(ticket)
        
        return True
    
    async def get_ticket(self, ticket_id: int) -> Optional[Ticket]:
        """Get ticket by ID"""
        return self.active_tickets.get(ticket_id)
    
    async def get_ticket_by_channel(self, channel_id: int) -> Optional[Ticket]:
        """Get ticket by channel ID"""
        for ticket in self.active_tickets.values():
            if ticket.channel_id == channel_id:
                return ticket
        return None
    
    async def update_ticket(self, ticket_id: int, **kwargs) -> bool:
        """Update ticket fields"""
        ticket = self.active_tickets.get(ticket_id)
        if not ticket:
            return False
        
        for key, value in kwargs.items():
            if hasattr(ticket, key):
                setattr(ticket, key, value)
        
        ticket.updated_at = datetime.now(timezone.utc)
        return True
    
    async def close_ticket(self, ticket_id: int, reason: str = "completed") -> bool:
        """Close ticket and cleanup"""
        ticket = self.active_tickets.get(ticket_id)
        if not ticket:
            return False
        
        # Update status
        ticket.status = reason
        ticket.updated_at = datetime.now(timezone.utc)
        
        # Remove from active tickets
        self.active_tickets.pop(ticket_id, None)
        
        # Remove from state manager
        self.state_manager.remove_deal(ticket_id)
        
        # Close channel after delay
        channel = self.bot.get_channel(ticket.channel_id)
        if channel:
            await asyncio.sleep(5)  # Give time for final messages
            try:
                await channel.delete(reason=f"Ticket {ticket_id} {reason}")
            except:
                pass  # Channel might already be deleted
        
        return True
    
    async def _get_next_ticket_id(self) -> int:
        """Get next available ticket ID"""
        # Import here to avoid circular imports
        from database import get_next_ticket_id
        return get_next_ticket_id()
    
    async def _create_ticket_channel(self, creator: discord.User, ticket_id: int, category_id: int) -> discord.TextChannel:
        """Create Discord channel for ticket"""
        guild = creator.guild
        
        # Get category
        category = guild.get_channel(category_id) if category_id else None
        
        # Create channel
        channel = await guild.create_text_channel(
            name=f"ticket-{ticket_id}",
            category=category,
            topic=f"Trade ticket #{ticket_id} | Created by {creator.display_name}",
            reason=f"New trade ticket #{ticket_id}"
        )
        
        # Set permissions
        await channel.set_permissions(
            guild.default_role,
            view_channel=False,
            send_messages=False
        )
        
        await channel.set_permissions(
            creator,
            view_channel=True,
            send_messages=True,
            read_messages=True
        )
        
        return channel
    
    async def _send_ticket_startup(self, channel: discord.TextChannel, ticket: Ticket, creator: discord.User):
        """Send ticket startup message"""
        embed = EmbedBuilder.ticket_created(ticket.ticket_id, creator)
        view = ViewTemplates.RoleSelection(ticket.ticket_id)
        await channel.send(f"{creator.mention}", embed=embed, view=view)
    
    async def _send_amount_prompt(self, ticket: Ticket):
        """Send amount setting prompt"""
        channel = self.bot.get_channel(ticket.channel_id)
        if not channel:
            return
        
        embed = UIComponents.create_embed(
            title="Set Trade Amount",
            description=(
                "Enter the USD value for your trade\n\n"
                "Examples: `100`, `250.50`, `1000`\n\n"
                "This amount cannot be changed later!"
            ),
            color=UIComponents.UIColors.PRIMARY
        )
        
        # Create amount modal
        modal = UIComponents.create_modal(
            title="Set Amount",
            fields=[
                {
                    "name": "amount",
                    "label": "Amount in USD",
                    "placeholder": "100.00",
                    "style": discord.TextStyle.short,
                    "required": True,
                    "min_length": 1,
                    "max_length": 10
                }
            ]
        )
        
        # Set up modal callback
        async def on_submit(interaction: discord.Interaction):
            try:
                amount = float(modal.amount.value)
                if amount <= 0:
                    await interaction.response.send_message("Amount must be positive.", ephemeral=True)
                    return
                
                if await self.set_amount(ticket.ticket_id, amount):
                    await interaction.response.send_message("Amount set successfully!", ephemeral=True)
                else:
                    await interaction.response.send_message("Failed to set amount.", ephemeral=True)
                    
            except ValueError:
                await interaction.response.send_message("Invalid amount format.", ephemeral=True)
        
        modal.on_submit = on_submit
        
        await channel.send(embed=embed, view=ui.View().add_item(
            ui.Button(label="Set Amount", style=discord.ButtonStyle.primary, custom_id=f"set_amount_{ticket.ticket_id}")
        ))
    
    async def _setup_payment(self, ticket: Ticket):
        """Setup payment for ticket"""
        # This would integrate with the crypto module
        # For now, just send a placeholder
        channel = self.bot.get_channel(ticket.channel_id)
        if not channel:
            return
        
        # Generate payment address (placeholder)
        payment_address = f"placeholder_address_{ticket.ticket_id}"
        
        embed = EmbedBuilder.payment_required(
            amount_usd=ticket.amount_usd or 0,
            address=payment_address,
            crypto=ticket.crypto or "UNKNOWN",
            color=UIHelper.get_crypto_color(ticket.crypto or "")
        )
        
        await channel.send(embed=embed)


class UIHelper:
    """UI helper functions"""
    
    @staticmethod
    def get_crypto_color(crypto: str) -> UIComponents.UIColors:
        """Get color for crypto type"""
        colors = {
            "LTC": UIComponents.UIColors.LTC,
            "USDT": UIComponents.UIColors.BSC,
            "USDT_BEP20": UIComponents.UIColors.BSC,
            "USDT_ETH": UIComponents.UIColors.ETH,
            "CASHAPP": UIComponents.UIColors.CASH_APP,
            "PAYPAL": UIComponents.UIColors.PAYPAL
        }
        return colors.get(crypto.upper(), UIComponents.UIColors.PRIMARY)
