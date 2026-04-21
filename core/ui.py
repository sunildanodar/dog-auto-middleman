"""
Premium UI System
Clean, modern Discord UI components with consistent branding
"""

import discord
from discord import ui, Embed
from typing import Optional, List, Dict, Any, Union
from enum import Enum
import asyncio


class UIColors(Enum):
    """Consistent color scheme"""
    PRIMARY = 0x2B2D31      # Discord dark
    SUCCESS = 0x10B981      # Green
    WARNING = 0xF59E0B      # Yellow
    ERROR = 0xEF4444        # Red
    INFO = 0x3B82F6         # Blue
    CASH_APP = 0x00C244     # Cash App green
    PAYPAL = 0x003087       # PayPal blue
    LTC = 0x345D9C          # Litecoin blue
    BSC = 0xF0B90B          # BSC yellow
    ETH = 0x627EEA          # Ethereum purple


class UIComponents:
    """Reusable UI components"""
    
    @staticmethod
    def create_embed(
        title: str,
        description: str = "",
        color: UIColors = UIColors.PRIMARY,
        thumbnail: Optional[str] = None,
        footer: Optional[str] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        author: Optional[Dict[str, str]] = None
    ) -> Embed:
        """Create consistent embed"""
        embed = Embed(
            title=title,
            description=description,
            color=color.value
        )
        
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        
        if footer:
            embed.set_footer(text=footer, icon_url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png")
        else:
            embed.set_footer(text="Dog Auto Middleman | Secure Escrow Service", 
                           icon_url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png")
        
        if fields:
            for field in fields:
                embed.add_field(**field)
        
        if author:
            embed.set_author(**author)
        
        return embed
    
    @staticmethod
    def create_button(
        label: str,
        style: discord.ButtonStyle = discord.ButtonStyle.primary,
        custom_id: Optional[str] = None,
        emoji: Optional[str] = None,
        disabled: bool = False,
        row: Optional[int] = None
    ) -> ui.Button:
        """Create consistent button"""
        return ui.Button(
            label=label,
            style=style,
            custom_id=custom_id,
            emoji=emoji,
            disabled=disabled,
            row=row
        )
    
    @staticmethod
    def create_modal(
        title: str,
        fields: List[Dict[str, Any]]
    ) -> ui.Modal:
        """Create modal with consistent styling"""
        class CustomModal(ui.Modal):
            def __init__(self):
                super().__init__(title=title)
                for field_config in fields:
                    field = ui.TextInput(**field_config)
                    setattr(self, field_config['name'], field)
                    self.add_item(field)
        
        return CustomModal()


class EmbedBuilder:
    """Specialized embed builders for different contexts"""
    
    @staticmethod
    def main_panel() -> Embed:
        """Main service panel"""
        return UIComponents.create_embed(
            title="Dog Auto Middleman",
            description=(
                "Premium Escrow Service\n\n"
                "Before using:\n"
                "Read our ToS: `#tos`\n"
                "Additional rules: `#mm-tos`\n\n"
                "Fee Structure:\n"
                "Deals $250+: $1.50\n"
                "Deals under $250: $0.50\n"
                "Deals under $50: FREE\n\n"
                "100% Secure | Instant Setup | 24/7 Support"
            ),
            color=UIColors.PRIMARY,
            thumbnail="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png",
            footer="Trusted by 1000+ Traders"
        )
    
    @staticmethod
    def payment_option(crypto: str, description: str, color: UIColors, thumbnail: str) -> Embed:
        """Payment method option embed"""
        return UIComponents.create_embed(
            title=f"{crypto} Payment",
            description=description,
            color=color,
            thumbnail=thumbnail
        )
    
    @staticmethod
    def ticket_created(ticket_id: int, creator: discord.User) -> Embed:
        """Ticket creation confirmation"""
        return UIComponents.create_embed(
            title="Trade Ticket Created",
            description=(
                "Welcome to Dog Auto Middleman!\n\n"
                "Follow these steps:\n"
                "1. Select your role (Buyer/Seller)\n"
                "2. Set the trade amount\n"
                "3. Send payment to the provided address\n"
                "4. Complete your trade safely\n\n"
                f"Ticket Owner: {creator.mention}\n\n"
                "Your funds are 100% secure with us"
            ),
            color=UIColors.PRIMARY,
            thumbnail="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png",
            footer=f"Ticket #{ticket_id}"
        )
    
    @staticmethod
    def role_selection(crypto: str, buyer_id: Optional[int], seller_id: Optional[int]) -> Embed:
        """Role selection embed"""
        buyer_mention = f"<@{buyer_id}>" if buyer_id else "*Waiting...*"
        seller_mention = f"<@{seller_id}>" if seller_id else "*Waiting...*"
        
        return UIComponents.create_embed(
            title="Select Your Role",
            description=(
                f"Choose your position in this trade:\n\n"
                f"Sender - You're sending {crypto} to the bot\n"
                f"Receiver - You'll receive {crypto} from the bot\n\n"
                "Important: Select the correct role to avoid issues!"
            ),
            color=UIColors.PRIMARY,
            fields=[
                {"name": "Sender", "value": buyer_mention, "inline": True},
                {"name": "Receiver", "value": seller_mention, "inline": True}
            ]
        )
    
    @staticmethod
    def payment_required(amount_usd: float, address: str, crypto: str, color: UIColors) -> Embed:
        """Payment request embed"""
        crypto_emoji = {
            "LTC": " Litecoin",
            "USDT": " USDT",
            "CASHAPP": " Cash App",
            "PAYPAL": " PayPal"
        }.get(crypto, "")
        
        return UIComponents.create_embed(
            title=f"Payment Required",
            description=(
                f"Send payment to the address below\n\n"
                "Important: Send the exact amount to avoid delays"
            ),
            color=color,
            fields=[
                {"name": "Amount Due", "value": f"${amount_usd:.2f}", "inline": True},
                {"name": "Payment Address", "value": f"```{address}```", "inline": False},
                {"name": "Status", "value": "Awaiting Payment", "inline": False}
            ]
        )
    
    @staticmethod
    def payment_detected(amount: float, crypto: str, confirmations: int = 0) -> Embed:
        """Payment detected embed"""
        return UIComponents.create_embed(
            title="Payment Detected",
            description=(
                f"Transaction detected and being processed\n\n"
                f"Confirmations: {confirmations}/1"
            ),
            color=UIColors.WARNING,
            fields=[
                {"name": "Amount", "value": f"{amount} {crypto}", "inline": True},
                {"name": "Status", "value": "Confirming...", "inline": True}
            ]
        )
    
    @staticmethod
    def payment_confirmed(amount: float, crypto: str) -> Embed:
        """Payment confirmed embed"""
        return UIComponents.create_embed(
            title="Payment Confirmed",
            description="Payment received and verified. You may proceed with your trade.",
            color=UIColors.SUCCESS,
            fields=[
                {"name": "Amount", "value": f"{amount} {crypto}", "inline": True},
                {"name": "Status", "value": "Funded", "inline": True}
            ]
        )
    
    @staticmethod
    def trade_ready(buyer_id: int, seller_id: int) -> Embed:
        """Trade ready for completion"""
        return UIComponents.create_embed(
            title="Trade Ready",
            description=(
                f"1. <@{seller_id}> Give your trader the items or payment you agreed on\n\n"
                f"2. <@{buyer_id}> Once you have received your items, click \"Release\" so your trader can claim the funds"
            ),
            color=UIColors.SUCCESS
        )
    
    @staticmethod
    def trade_completed(ticket_id: int) -> Embed:
        """Trade completion embed"""
        return UIComponents.create_embed(
            title="Trade Completed",
            description="The trade has been successfully completed and funds released.",
            color=UIColors.SUCCESS,
            footer=f"Ticket #{ticket_id}"
        )
    
    @staticmethod
    def error(message: str) -> Embed:
        """Error embed"""
        return UIComponents.create_embed(
            title="Error",
            description=message,
            color=UIColors.ERROR
        )
    
    @staticmethod
    def warning(message: str) -> Embed:
        """Warning embed"""
        return UIComponents.create_embed(
            title="Warning",
            description=message,
            color=UIColors.WARNING
        )


class ViewTemplates:
    """Reusable view templates"""
    
    class PaymentOptions(ui.View):
        """Payment method selection view"""
        def __init__(self):
            super().__init__(timeout=None)
            
            self.add_item(UIComponents.create_button(
                label="Start Trade",
                style=discord.ButtonStyle.primary,
                custom_id="panel_request_ltc",
                emoji="",
                row=0
            ))
            
            self.add_item(UIComponents.create_button(
                label="Start Trade", 
                style=discord.ButtonStyle.success,
                custom_id="panel_request_usdt_bep20",
                emoji="",
                row=0
            ))
            
            self.add_item(UIComponents.create_button(
                label="Start Trade",
                style=discord.ButtonStyle.secondary,
                custom_id="panel_request_usdt_eth", 
                emoji="",
                row=0
            ))
    
    class RoleSelection(ui.View):
        """Role selection view"""
        def __init__(self, ticket_id: int):
            super().__init__(timeout=None)
            self.ticket_id = ticket_id
            
            self.add_item(UIComponents.create_button(
                label="Sender",
                style=discord.ButtonStyle.primary,
                custom_id=f"role_sender_{ticket_id}",
                emoji=""
            ))
            
            self.add_item(UIComponents.create_button(
                label="Receiver",
                style=discord.ButtonStyle.secondary,
                custom_id=f"role_receiver_{ticket_id}",
                emoji=""
            ))
    
    class TradeControls(ui.View):
        """Trade control buttons (release/refund)"""
        def __init__(self, ticket_id: int, crypto: str):
            super().__init__(timeout=None)
            self.ticket_id = ticket_id
            self.crypto = crypto
            
            self.add_item(UIComponents.create_button(
                label="Release Funds",
                style=discord.ButtonStyle.success,
                custom_id=f"release_{ticket_id}",
                emoji=""
            ))
            
            self.add_item(UIComponents.create_button(
                label="Dispute",
                style=discord.ButtonStyle.danger,
                custom_id=f"dispute_{ticket_id}",
                emoji=""
            ))
    
    class ConfirmAction(ui.View):
        """Generic confirmation view"""
        def __init__(self, ticket_id: int, action: str, confirm_callback):
            super().__init__(timeout=None)
            self.ticket_id = ticket_id
            self.action = action
            self.confirm_callback = confirm_callback
            
            self.add_item(UIComponents.create_button(
                label="Confirm",
                style=discord.ButtonStyle.success,
                custom_id=f"confirm_{action}_{ticket_id}",
                emoji=""
            ))
            
            self.add_item(UIComponents.create_button(
                label="Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id=f"cancel_{action}_{ticket_id}",
                emoji=""
            ))
        
        async def callback(self, interaction: discord.Interaction, button: ui.Button):
            if "confirm" in button.custom_id:
                await self.confirm_callback(interaction)
            else:
                await interaction.response.send_message("Action cancelled.", ephemeral=True)


class UIHelper:
    """UI utility functions"""
    
    @staticmethod
    async def send_chunked_message(
        channel: discord.TextChannel,
        header: str,
        content: List[str],
        max_length: int = 1900
    ):
        """Send message in chunks to avoid Discord limits"""
        current = header
        for line in content:
            if len(current) + len(line) + 1 > max_length:
                await channel.send(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        
        if current:
            await channel.send(current)
    
    @staticmethod
    def format_crypto_amount(amount: float, crypto: str) -> str:
        """Format crypto amount with proper precision"""
        if crypto.upper() == "LTC":
            return f"{amount:.8f}".rstrip("0").rstrip(".")
        else:
            return f"{amount:.2f}"
    
    @staticmethod
    def get_crypto_emoji(crypto: str) -> str:
        """Get emoji for crypto type"""
        emojis = {
            "LTC": "",
            "USDT": "",
            "USDT_BEP20": "",
            "USDT_ETH": "",
            "CASHAPP": "",
            "PAYPAL": ""
        }
        return emojis.get(crypto.upper(), "")
    
    @staticmethod
    def get_crypto_color(crypto: str) -> UIColors:
        """Get color for crypto type"""
        colors = {
            "LTC": UIColors.LTC,
            "USDT": UIColors.BSC,
            "USDT_BEP20": UIColors.BSC,
            "USDT_ETH": UIColors.ETH,
            "CASHAPP": UIColors.CASH_APP,
            "PAYPAL": UIColors.PAYPAL
        }
        return colors.get(crypto.upper(), UIColors.PRIMARY)
