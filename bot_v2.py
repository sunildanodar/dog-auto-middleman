"""
Dog Auto Middleman V2 - Premium Escrow Bot
Clean, modular architecture matching Dog's quality
"""

import discord
import asyncio
import os
from discord.ext import commands
from discord import ui

# Import core modules
from core import (
    StateManager, UIComponents, EmbedBuilder, 
    TicketManager, PaymentProcessor, PaymentTracker, AdminPanel
)
from core.state import DealState
from config import (
    TOKEN, ADMIN_ID, TICKET_CATEGORY_ID, LOG_CHANNEL_ID, 
    PROOF_CHANNEL_ID, CONFIRMATIONS_REQUIRED
)

# Import legacy modules for compatibility
import database
import crypto


class DogAutoMiddleman(commands.Bot):
    """Main bot class with clean architecture"""
    
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        
        # Initialize core systems
        self.state_manager = StateManager()
        self.ticket_manager = None  # Will be set in on_ready
        self.payment_processor = PaymentProcessor(self.state_manager)
        self.payment_tracker = PaymentTracker(self.payment_processor, self.state_manager)
        self.admin_panel = None  # Will be set in on_ready
        
        # Command cooldowns
        self.command_cooldowns = {}
    
    async def on_ready(self):
        """Initialize bot systems"""
        print(f"Bot is online as {self.user}")
        
        # Initialize managers
        self.ticket_manager = TicketManager(self, self.state_manager)
        self.admin_panel = AdminPanel(
            self, self.state_manager, self.ticket_manager,
            self.payment_processor, self.payment_tracker, ADMIN_ID
        )
        
        # Setup slash commands
        await self.setup_slash_commands()
        
        # Sync commands
        await self.tree.sync()
        
        print("Bot systems initialized successfully")
    
    async def setup_slash_commands(self):
        """Setup slash commands"""
        
        # Main panel command
        @self.tree.command(name="panel", description="Open the Dog Auto Middleman panel")
        async def panel_slash(interaction: discord.Interaction):
            await self._handle_panel(interaction)
        
        # Admin commands
        @self.tree.command(name="admin", description="Admin control panel")
        async def admin_slash(interaction: discord.Interaction):
            await self._handle_admin_panel(interaction)
        
        @self.tree.command(name="ticket_info", description="Get ticket information (Admin)")
        async def ticket_info_slash(interaction: discord.Interaction, ticket_id: int):
            await self._handle_ticket_info(interaction, ticket_id)
        
        # Payment confirmation commands
        @self.tree.command(name="confirm_cashapp", description="Confirm Cash App payment (Admin)")
        async def confirm_cashapp_slash(interaction: discord.Interaction):
            await self._handle_payment_confirmation(interaction, "CASHAPP")
        
        @self.tree.command(name="confirm_paypal", description="Confirm PayPal payment (Admin)")
        async def confirm_paypal_slash(interaction: discord.Interaction):
            await self._handle_payment_confirmation(interaction, "PAYPAL")
    
    async def _handle_panel(self, interaction: discord.Interaction):
        """Handle main panel command"""
        try:
            # Create main panel embed
            embed = EmbedBuilder.main_panel()
            
            # Create payment options
            ltc_embed = EmbedBuilder.payment_option(
                crypto="Litecoin",
                description="Fast & low fees\nAverage confirmation: 2.5 minutes\nNetwork: Litecoin Mainnet",
                color=UIComponents.UIColors.LTC,
                thumbnail="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843544/ltc.png"
            )
            
            usdt_bep20_embed = EmbedBuilder.payment_option(
                crypto="USDT (BEP-20)",
                description="Ultra-low fees (~$0.10)\nLightning fast (~3 seconds)\nNetwork: Binance Smart Chain",
                color=UIComponents.UIColors.BSC,
                thumbnail="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843545/bnb.png"
            )
            
            usdt_eth_embed = EmbedBuilder.payment_option(
                crypto="USDT (ETH)",
                description="Most widely supported\nHigher fees (~$5-15)\nNetwork: Ethereum Mainnet",
                color=UIComponents.UIColors.ETH,
                thumbnail="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843546/eth.png"
            )
            
            await interaction.response.send_message(embed=embed)
            await interaction.followup.send("\u200b")  # Spacer
            await interaction.followup.send(embed=ltc_embed, view=self._create_payment_view("LTC"))
            await interaction.followup.send(embed=usdt_bep20_embed, view=self._create_payment_view("USDT_BEP20"))
            await interaction.followup.send(embed=usdt_eth_embed, view=self._create_payment_view("USDT_ETH"))
            
        except Exception as e:
            await interaction.response.send_message(
                f"Error loading panel: {e}", 
                ephemeral=True
            )
    
    def _create_payment_view(self, crypto: str) -> ui.View:
        """Create payment selection view"""
        view = ui.View(timeout=None)
        
        emojis = {
            "LTC": "",
            "USDT_BEP20": "",
            "USDT_ETH": ""
        }
        
        view.add_item(ui.Button(
            label="Start Trade",
            style=discord.ButtonStyle.primary,
            custom_id=f"start_trade_{crypto}",
            emoji=emojis.get(crypto)
        ))
        
        return view
    
    async def _handle_admin_panel(self, interaction: discord.Interaction):
        """Handle admin panel command"""
        if not self.admin_panel.is_admin(interaction.user):
            await interaction.response.send_message(
                "Admin access required.", 
                ephemeral=True
            )
            return
        
        stats = await self.admin_panel.get_system_stats(interaction.user)
        if not stats:
            await interaction.response.send_message(
                "Failed to get system stats.", 
                ephemeral=True
            )
            return
        
        embed = UIComponents.create_embed(
            title="Admin Control Panel",
            description="System overview and controls",
            color=UIComponents.UIColors.INFO
        )
        
        # Add stats fields
        deal_stats = stats['deals']
        embed.add_field(
            name="Active Deals",
            value=f"Total: {deal_stats['total_active']}\nExpiring Soon: {deal_stats['expiring_soon']}",
            inline=True
        )
        
        payment_stats = stats['payments']
        embed.add_field(
            name="Payments",
            value=f"Active: {payment_stats['active_payments']}\nMonitoring: {payment_stats['monitoring_tasks']}",
            inline=True
        )
        
        admin_stats = stats['admin_actions']
        embed.add_field(
            name="Admin Activity",
            value=f"Today: {admin_stats['last_24h']}\nTotal: {admin_stats['total']}",
            inline=True
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def _handle_ticket_info(self, interaction: discord.Interaction, ticket_id: int):
        """Handle ticket info command"""
        if not self.admin_panel.is_admin(interaction.user):
            await interaction.response.send_message(
                "Admin access required.", 
                ephemeral=True
            )
            return
        
        info = await self.admin_panel.get_ticket_info(interaction.user, ticket_id)
        if not info:
            await interaction.response.send_message(
                f"Ticket #{ticket_id} not found.", 
                ephemeral=True
            )
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def _handle_payment_confirmation(self, interaction: discord.Interaction, crypto: str):
        """Handle payment confirmation commands"""
        if not self.admin_panel.is_admin(interaction.user):
            await interaction.response.send_message(
                "Admin access required.", 
                ephemeral=True
            )
            return
        
        # Get ticket from channel
        ticket = await self.ticket_manager.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(
                "No ticket found in this channel.", 
                ephemeral=True
            )
            return
        
        # Confirm payment
        success, message = await self.payment_tracker.manually_confirm_payment(
            ticket.ticket_id, 
            interaction.user.id
        )
        
        if success:
            embed = UIComponents.create_embed(
                title="Payment Confirmed",
                description=f"{crypto} payment confirmed manually. Trade can proceed.",
                color=UIComponents.UIColors.SUCCESS
            )
            
            # Send trade ready message
            await self._send_trade_ready_message(ticket)
            
        else:
            embed = UIComponents.create_embed(
                title="Confirmation Failed",
                description=message,
                color=UIComponents.UIColors.ERROR
            )
        
        await interaction.response.send_message(embed=embed)
    
    async def _send_trade_ready_message(self, ticket):
        """Send trade ready message"""
        channel = self.get_channel(ticket.channel_id)
        if not channel:
            return
        
        embed = EmbedBuilder.trade_ready(ticket.buyer_id, ticket.seller_id)
        view = self._create_trade_controls_view(ticket.ticket_id, ticket.crypto)
        
        await channel.send(
            f"<@{ticket.buyer_id}> <@{ticket.seller_id}>",
            embed=embed,
            view=view
        )
    
    def _create_trade_controls_view(self, ticket_id: int, crypto: str) -> ui.View:
        """Create trade control buttons"""
        view = ui.View(timeout=None)
        
        view.add_item(ui.Button(
            label="Release Funds",
            style=discord.ButtonStyle.success,
            custom_id=f"release_{ticket_id}",
            emoji=""
        ))
        
        view.add_item(ui.Button(
            label="Dispute",
            style=discord.ButtonStyle.danger,
            custom_id=f"dispute_{ticket_id}",
            emoji=""
        ))
        
        return view
    
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle button interactions"""
        if not interaction.data:
            return
        
        custom_id = interaction.data.get("custom_id", "")
        
        # Handle trade start buttons
        if custom_id.startswith("start_trade_"):
            crypto = custom_id.replace("start_trade_", "")
            await self._handle_trade_start(interaction, crypto)
        
        # Handle role selection
        elif custom_id.startswith("role_"):
            parts = custom_id.split("_")
            role = parts[1]
            ticket_id = int(parts[2])
            await self._handle_role_selection(interaction, role, ticket_id)
        
        # Handle trade controls
        elif custom_id.startswith("release_"):
            ticket_id = int(custom_id.replace("release_", ""))
            await self._handle_release_funds(interaction, ticket_id)
        
        elif custom_id.startswith("dispute_"):
            ticket_id = int(custom_id.replace("dispute_", ""))
            await self._handle_dispute(interaction, ticket_id)
    
    async def _handle_trade_start(self, interaction: discord.Interaction, crypto: str):
        """Handle trade start button"""
        # Create trade creation modal
        modal = UIComponents.create_modal(
            title="Create Trade",
            fields=[
                {
                    "name": "trader_user",
                    "label": "Trader's Username or ID",
                    "placeholder": "e.g., username or 123456789",
                    "style": discord.TextStyle.short,
                    "required": True
                },
                {
                    "name": "you_give",
                    "label": "What are you giving?",
                    "placeholder": "Describe your item/payment",
                    "style": discord.TextStyle.short,
                    "required": True
                },
                {
                    "name": "trader_gives",
                    "label": "What is trader giving?",
                    "placeholder": "Describe their item/payment",
                    "style": discord.TextStyle.short,
                    "required": True
                }
            ]
        )
        
        async def on_submit(interaction: discord.Interaction):
            try:
                # Parse trader user
                trader_input = modal.trader_user.value.strip()
                trader_user = await self._resolve_user(interaction.guild, trader_input)
                
                if not trader_user:
                    await interaction.response.send_message(
                        "User not found. Please check the username/ID.",
                        ephemeral=True
                    )
                    return
                
                # Create ticket
                ticket = await self.ticket_manager.create_ticket(
                    creator=interaction.user,
                    crypto=crypto,
                    trader_user=trader_user.display_name,
                    you_give=modal.you_give.value,
                    trader_gives=modal.trader_gives.value,
                    category_id=TICKET_CATEGORY_ID
                )
                
                await interaction.response.send_message(
                    f"Trade ticket #{ticket.ticket_id} created! Check your DMs.",
                    ephemeral=True
                )
                
                # DM user with channel link
                channel = self.get_channel(ticket.channel_id)
                if channel:
                    await interaction.user.send(
                        f"Your trade ticket is ready: {channel.mention}"
                    )
                
            except Exception as e:
                await interaction.response.send_message(
                    f"Error creating ticket: {e}",
                    ephemeral=True
                )
        
        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)
    
    async def _resolve_user(self, guild, user_input: str):
        """Resolve user from input"""
        try:
            # Try ID first
            if user_input.isdigit():
                user_id = int(user_input)
                return guild.get_member(user_id) or await guild.fetch_member(user_id)
            
            # Try username
            user = discord.utils.get(
                guild.members,
                name=user_input
            ) or discord.utils.get(
                guild.members,
                display_name=user_input
            )
            
            return user
            
        except:
            return None
    
    async def _handle_role_selection(self, interaction: discord.Interaction, role: str, ticket_id: int):
        """Handle role selection"""
        success = await self.ticket_manager.assign_roles(
            interaction.user, role, ticket_id
        )
        
        if success:
            await interaction.response.send_message(
                f"Role assigned: {role}",
                ephemeral=True
            )
            
            # Update role selection embed
            ticket = await self.ticket_manager.get_ticket(ticket_id)
            if ticket:
                embed = EmbedBuilder.role_selection(
                    ticket.crypto or "",
                    ticket.buyer_id,
                    ticket.seller_id
                )
                await interaction.message.edit(embed=embed)
        else:
            await interaction.response.send_message(
                "Failed to assign role.",
                ephemeral=True
            )
    
    async def _handle_release_funds(self, interaction: discord.Interaction, ticket_id: int):
        """Handle fund release"""
        ticket = await self.ticket_manager.get_ticket(ticket_id)
        if not ticket:
            await interaction.response.send_message(
                "Ticket not found.",
                ephemeral=True
            )
            return
        
        # Check if user is authorized (buyer or admin)
        if interaction.user.id != ticket.buyer_id and not self.admin_panel.is_admin(interaction.user):
            await interaction.response.send_message(
                "Only the buyer can release funds.",
                ephemeral=True
            )
            return
        
        # Create confirmation modal
        modal = UIComponents.create_modal(
            title="Confirm Release",
            fields=[
                {
                    "name": "seller_address",
                    "label": "Seller's Withdrawal Address",
                    "placeholder": "Enter seller's address",
                    "style": discord.TextStyle.short,
                    "required": True
                }
            ]
        )
        
        async def on_submit(interaction: discord.Interaction):
            success, result = await self.payment_processor.release_funds(
                ticket_id, modal.seller_address.value
            )
            
            if success:
                # Update state
                await self.state_manager.transition_deal(
                    ticket_id, DealState.COMPLETED, 
                    user_id=interaction.user.id
                )
                
                # Send completion message
                embed = EmbedBuilder.trade_completed(ticket_id)
                await interaction.channel.send(embed=embed)
                
                # Close ticket
                await self.ticket_manager.close_ticket(ticket_id)
                
                await interaction.response.send_message(
                    "Funds released successfully!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"Release failed: {result}",
                    ephemeral=True
                )
        
        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)
    
    async def _handle_dispute(self, interaction: discord.Interaction, ticket_id: int):
        """Handle dispute"""
        ticket = await self.ticket_manager.get_ticket(ticket_id)
        if not ticket:
            await interaction.response.send_message(
                "Ticket not found.",
                ephemeral=True
            )
            return
        
        # Update state to disputed
        await self.state_manager.transition_deal(
            ticket_id, DealState.DISPUTED,
            user_id=interaction.user.id
        )
        
        # Log dispute
        self.admin_panel.log_action(
            interaction.user, "DISPUTE", ticket_id,
            f"Dispute initiated by {interaction.user.display_name}"
        )
        
        # Notify admin
        if LOG_CHANNEL_ID:
            log_channel = self.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = UIComponents.create_embed(
                    title="Trade Disputed",
                    description=f"Ticket #{ticket_id} has been disputed by {interaction.user.mention}",
                    color=UIComponents.UIColors.WARNING
                )
                await log_channel.send(embed=embed)
        
        await interaction.response.send_message(
            "Dispute logged. Admin will review the case.",
            ephemeral=True
        )


# Bot startup
def main():
    """Main bot entry point"""
    bot = DogAutoMiddleman()
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
