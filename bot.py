import discord
import asyncio
import time
import datetime
import json
import random
import re
import secrets
import traceback
import requests
import os
import subprocess
import shutil
from pathlib import Path
from discord.ext import commands
from discord import ui
from functools import lru_cache
from typing import Optional, Dict, List, Tuple, Callable, Awaitable
from config import TOKEN, LOG_CHANNEL_ID, PROOF_CHANNEL_ID, TICKET_CATEGORY_ID, ADMIN_ID, CONFIRMATIONS_REQUIRED, BLOCKCYPHER_TOKEN, CODE_VERSION, DB_BACKUP_INTERVAL_MINUTES, REQUIRE_PERSISTENT_DB, ALLOW_FAKE_PAYMENTS, DB_NAME, DB_BACKUP_DIR, BACKUP_ALERT_MAX_AGE_MINUTES, BACKUP_STARTUP_MAX_AGE_MINUTES, PAYMENT_POLL_INTERVAL_SECONDS, LTC_NETWORK_FEE_USD, FEE_PERCENT
from crypto import generate_ltc_wallet, generate_bep20_wallet, detect_ltc_payment, detect_usdt_payment, send_ltc, send_usdt, sweep_ltc_to_master, sweep_usdt_to_master, usd_to_ltc, decrypt_key, private_hex_to_ltc_address
from database import init, save_ticket, update_ticket, get_ticket, get_ticket_by_channel, get_next_ticket_id, get_tickets_by_status, log_event, get_ticket_events, verify_ticket_audit_chain, create_db_backup, database_safety_snapshot, create_encrypted_backup_export
from control_panel.log_buffer import append_control_log
from control_panel.runtime import attach_bot, mark_ready
from control_panel.aiohttp_api import start_control_api
# State machine — centralized state system (see state_machine.py)
from state_machine import (
    EscrowState,
    TicketStateMachine,
    advance_ticket_state,
    get_ticket_state,
    is_ticket_in_state,
    assert_state_or_raise,
    get_state_meta,
)

import sqlite3

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
print("[STARTUP] Dog Auto Middleman bot process started (unique diagnostic print)")


async def respond_auto_mm_disabled(interaction: discord.Interaction) -> None:
    msg = (
        "Auto middleman is **disabled**. Use **Manual MM** in the middleman request channel, "
        "or ask staff to re-enable Auto MM from the control panel."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


async def guard_auto_mm_interaction(interaction: discord.Interaction) -> bool:
    from control_panel.runtime import is_auto_mm_enabled

    if is_auto_mm_enabled():
        return True
    await respond_auto_mm_disabled(interaction)
    return False
init()
active_monitors = set()
SAFETY_LOCKDOWN = False
security_alert_last_sent: Dict[str, int] = {}
withdraw_processing = set()
fake_confirmation_tasks: Dict[int, asyncio.Task] = {}
sensitive_command_last_used: Dict[Tuple[int, str], float] = {}
SENSITIVE_COMMAND_COOLDOWN_SECONDS = 8
MIN_DEAL_USD = 1.0
MAX_DEAL_USD = 100000.0

# ── Missing constants (referenced throughout file but previously undeclared) ────
# Bot branding used in embeds
SPARKLES_TITLE = "🐕 Dog Auto Middleman"
SPARKLES_FOOTER = "Dog Auto Middleman | Secure Escrow"

# Withdrawal retry settings
WITHDRAW_RETRY_MAX_ATTEMPTS = 3
WITHDRAW_RETRY_BASE_SECONDS = 30
WITHDRAW_CONFIRM_COOLDOWN_SECONDS = 10

# Task registry for active payment monitors (ticket_id → asyncio.Task)
monitor_tasks: Dict[int, asyncio.Task] = {}

# Advanced Features: Deal tracking and safety systems
deal_confirmations: Dict[int, Dict] = {}  # ticket_id -> confirmation data
user_blacklist: set = set()  # Blacklisted users
active_deal_locks: set = set()  # Locked deals (no edits allowed)
deal_summaries: Dict[int, discord.Message] = {}  # Track deal summary messages

# Premium UI Color System - Consistent Professional Theme
PREMIUM_COLORS = {
    'primary': 0x2B2D31,      # Discord Dark (main)
    'accent': 0x5865F2,        # Discord Blurple (accent)
    'success': 0x3BA55C,        # Discord Green
    'warning': 0xED4245,        # Discord Red
    'pending': 0xF59E0B,        # Warm Yellow
    'detected': 0x5865F2,      # Discord Blurple
    'confirming': 0xEB459E,     # Discord Pink
    'confirmed': 0x3BA55C,      # Discord Green
    'locked': 0x5865F2,         # Discord Blurple
    'completed': 0x3BA55C,      # Discord Green
    'disputed': 0xED4245,       # Discord Red
    'cancelled': 0x475569,      # Discord Dark Gray
    'background': 0x36393F,     # Discord Dark Gray
    'text': 0xDCDDDE,            # Discord White
    'muted': 0x747F8D            # Discord Muted Gray
}

# Clean Status System - No Emojis
STATUS_CONFIG = {
    'pending': {
        'color': PREMIUM_COLORS['pending'],
        'label': 'Pending'
    },
    'detected': {
        'color': PREMIUM_COLORS['detected'],
        'label': 'Detected'
    },
    'confirming': {
        'color': PREMIUM_COLORS['confirming'],
        'label': 'Confirming'
    },
    'confirmed': {
        'color': PREMIUM_COLORS['confirmed'],
        'label': 'Confirmed'
    },
    'locked': {
        'color': PREMIUM_COLORS['locked'],
        'label': 'Locked'
    },
    'completed': {
        'color': PREMIUM_COLORS['completed'],
        'label': 'Completed'
    },
    'disputed': {
        'color': PREMIUM_COLORS['disputed'],
        'label': 'Disputed'
    },
    'cancelled': {
        'color': PREMIUM_COLORS['cancelled'],
        'label': 'Cancelled'
    }
}

# Legacy compatibility
STATUS_COLORS = {status: config['color'] for status, config in STATUS_CONFIG.items()}


@lru_cache(maxsize=1)
def get_runtime_code_version():
    for env_key in ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT", "SOURCE_VERSION"):
        commit_value = str(os.getenv(env_key, "")).strip()
        if commit_value:
            return commit_value[:7]

    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        short_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
        if short_sha:
            return short_sha
    except Exception:
        pass

    return CODE_VERSION

# Sparkles-Level UI System - Advanced Discord Techniques
def generate_deal_id(ticket_id: int) -> str:
    """Generate unique deal ID for tracking"""
    return f"DM-{ticket_id:04d}-{int(time.time()) % 10000:04d}"

# Professional pixel-perfect spacing system
ZERO_WIDTH = "\u200b"           # Zero-width space for precision
THIN_SPACE = "\u2009"            # Thin space (1/4 em)
HAIR_SPACE = "\u200a"            # Hair space (1/8 em)
SIX_PER_EM = "\u2006"            # Six-per-em space
PUNCT_SPACE = "\u2008"            # Punctuation space

# Professional Unicode characters
PROGRESS_FULL = "█"              # Full progress block
PROGRESS_EMPTY = "░"             # Empty progress block
PROGRESS_PARTIAL = "▓"           # Partial progress block
CHECKMARK = "✓"                 # Checkmark
CROSS = "✗"                       # Cross
ARROW_RIGHT = "→"                # Right arrow
BULLET = "•"                     # Bullet point
SEPARATOR = "│"                   # Separator
DOT = "·"                        # Fine dot
SQUARE = "■"                     # Square
DIAMOND = "◆"                   # Diamond
TRIANGLE = "▲"                  # Triangle
CIRCLE = "●"                     # Circle
LINE_H = "─"                     # Horizontal line
LINE_V = "│"                     # Vertical line
LINE_CORNER = "┌"                  # Corner
LINE_T = "┬"                     # T-junction
LINE_CROSS = "┼"                 # Cross junction
LINE_END = "└"                  # End line

# Professional spacing combinations
DOUBLE_SPACER = f"{ZERO_WIDTH}{ZERO_WIDTH}"  # Double zero-width
TRIPLE_SPACER = f"{ZERO_WIDTH}{ZERO_WIDTH}{ZERO_WIDTH}"  # Triple zero-width
PRO_PAD = f"{ZERO_WIDTH}{THIN_SPACE}{ZERO_WIDTH}"  # Professional padding
PRO_MARGIN = f"{ZERO_WIDTH}{HAIR_SPACE}{ZERO_WIDTH}"  # Professional margin
TEXT_CENTER = f"{ZERO_WIDTH}{SIX_PER_EM}{ZERO_WIDTH}"  # Text centering
FIELD_PAD = f"{ZERO_WIDTH}{PUNCT_SPACE}{ZERO_WIDTH}"  # Field padding
VALUE_PAD = f"{ZERO_WIDTH}{THIN_SPACE}{ZERO_WIDTH}"  # Value padding

class SparklesEmbedBuilder:
    """STRICT Sparkles-style embed builder - exact 3-section format"""
    
    @staticmethod
    def create_main_deal_dashboard(ticket_id: int, buyer_id: int, seller_id: int, amount: float, crypto: str, status: str = "Pending", confirmations: int = 0, required_confs: int = 1, address: str = None) -> discord.Embed:
        """STRICT 3-section format - exact spacing matching screenshots"""
        status_config = STATUS_CONFIG.get(status.lower(), STATUS_CONFIG['pending'])
        
        embed = discord.Embed(
            title="",
            description="",
            color=status_config['color']
        )
        
        # SECTION 1: Participants
        embed.add_field(
            name="👥",
            value=f"<@{buyer_id}> • <@{seller_id}>",
            inline=False
        )
        
        # SECTION 2: Amount
        embed.add_field(
            name="💰",
            value=f"${amount:.2f} {crypto}",
            inline=False
        )
        
        # SECTION 3: Status/Progress
        if status == 'Confirming':
            progress_bar = f"{PROGRESS_FULL * confirmations}{PROGRESS_EMPTY * (required_confs - confirmations)}"
            status_value = f"{confirmations}/{required_confs}\n{progress_bar}"
        elif status == 'Detected':
            status_value = "Detected"
        elif status == 'Confirmed':
            status_value = "Confirmed"
        elif status == 'Completed':
            status_value = "Completed"
        elif status == 'Disputed':
            status_value = "Disputed"
        else:
            status_value = "Pending"
        
        embed.add_field(
            name="📊",
            value=status_value,
            inline=False
        )
        
        return embed
    
    @staticmethod
    def create_status_update_embed(status: str, details: str = "") -> discord.Embed:
        """Exact status update embed matching screenshots"""
        status_config = STATUS_CONFIG.get(status, STATUS_CONFIG['pending'])
        
        embed = discord.Embed(
            title=status_config['label'],
            description=details,
            color=status_config['color']
        )
        
        return embed
    
    @staticmethod
    def create_error_embed(error_message: str, guidance: str = "") -> discord.Embed:
        """Exact error embed matching screenshots"""
        embed = discord.Embed(
            title="Error",
            description=error_message,
            color=PREMIUM_COLORS['warning']
        )
        
        if guidance:
            embed.add_field(name="Next step", value=guidance, inline=False)
        
        return embed
    
    @staticmethod
    def create_success_embed(title: str, message: str) -> discord.Embed:
        """Exact success embed matching screenshots"""
        embed = discord.Embed(
            title=title,
            description=message,
            color=PREMIUM_COLORS['success']
        )
        
        return embed
    
    @staticmethod
    def create_instruction_embed(title: str, instructions: List[str], current_step: int = 0) -> discord.Embed:
        """Exact instruction embed matching screenshots"""
        embed = discord.Embed(
            title=title,
            description="Follow these steps to complete your trade:",
            color=PREMIUM_COLORS['primary']
        )
        
        for i, instruction in enumerate(instructions, 1):
            prefix = "✓" if i < current_step else "→" if i == current_step else "•"
            embed.add_field(
                name=f"{prefix} Step {i}: {instruction}",
                value="\u200b",
                inline=False
            )
        
        return embed

# Legacy compatibility
def create_deal_summary_embed(ticket_id: int, buyer_id: int, seller_id: int, amount: float, crypto: str, status: str = "pending") -> discord.Embed:
    """Legacy compatibility function - uses Sparkles builder"""
    return SparklesEmbedBuilder.create_main_deal_dashboard(ticket_id, buyer_id, seller_id, amount, crypto, status.capitalize())

def create_payment_status_embed(ticket_id: int, address: str, amount: float, crypto: str, confirmations: int = 0, required_confs: int = 1) -> discord.Embed:
    """Exact payment status embed matching screenshots"""
    progress = f"{confirmations}/{required_confs}"
    progress_bar = "█" * confirmations + "░" * (required_confs - confirmations)
    
    embed = discord.Embed(
        title="Payment Status",
        description="Payment monitoring in progress",
        color=STATUS_COLORS.get('confirming' if confirmations < required_confs else 'confirmed', 0x3B82F6)
    )
    
    embed.add_field(
        name="Send To",
        value=f"```{address}```",
        inline=False
    )
    
    embed.add_field(
        name="Amount",
        value=f"{amount} {crypto}",
        inline=True
    )
    
    embed.add_field(
        name="Confirmations",
        value=f"{progress}\n`{progress_bar}`",
        inline=True
    )
    
    if confirmations < required_confs:
        embed.add_field(
            name="Next Steps",
            value=f"Waiting for {required_confs - confirmations} more confirmation(s)\nEstimated time: {(required_confs - confirmations) * 2.5:.1f} minutes",
            inline=False
        )
    else:
        embed.add_field(
            name="Status",
            value="Payment confirmed and secured\nFunds are now in escrow",
            inline=False
        )
    
    return embed

class SparklesButtonSystem:
    """STRICT Sparkles-style button system - exact 5-button system"""
    
    @staticmethod
    def create_main_deal_view(ticket_id: int, status: str, user_id: int, buyer_id: int, seller_id: int) -> ui.View:
        """STRICT 5-button system - Address, Amount, Release, Cancel, Dispute"""
        view = ui.View(timeout=None)
        
        is_buyer = user_id == buyer_id
        is_seller = user_id == seller_id
        is_admin = user_id == ADMIN_ID
        
        # Row 1: Address, Amount (only when payment needed)
        if status in ['Detected', 'Confirming', 'Confirmed']:
            view.add_item(SparklesButtonSystem._create_button("Address", ticket_id, discord.ButtonStyle.secondary, "address"))
            view.add_item(SparklesButtonSystem._create_button("Amount", ticket_id, discord.ButtonStyle.secondary, "amount"))
        
        # Row 2: Release (only when confirmed)
        if status == 'Confirmed':
            if is_buyer or is_admin:
                view.add_item(SparklesButtonSystem._create_button("Release", ticket_id, discord.ButtonStyle.success, "release"))
        
        # Row 3: Cancel, Dispute (always available)
        if status not in ['Completed']:
            view.add_item(SparklesButtonSystem._create_button("Cancel", ticket_id, discord.ButtonStyle.danger, "cancel"))
            view.add_item(SparklesButtonSystem._create_button("Dispute", ticket_id, discord.ButtonStyle.danger, "dispute"))
        
        return view
    
    @staticmethod
    def _create_button(label: str, ticket_id: int, style: discord.ButtonStyle, button_type: str) -> ui.Button:
        """STRICT button creation - short labels only"""
        return ui.Button(
            label=label,
            style=style,
            custom_id=f"sparkles_{button_type}_{ticket_id}"
        )
    

# Legacy compatibility
class CopyButton(ui.View):
    """Legacy copy button for backward compatibility"""
    def __init__(self, text_to_copy: str, label: str = "Copy", emoji: str = "📋"):
        super().__init__(timeout=None)
        self.text_to_copy = text_to_copy
        
        self.add_item(ui.Button(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"copy_{hash(text_to_copy)}"
        ))
    
    async def callback(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            f"```{self.text_to_copy}```\n\n✅ Copied to clipboard!",
            ephemeral=True
        )

class DealControls(ui.View):
    """Legacy deal controls for backward compatibility"""
    def __init__(self, ticket_id: int, is_buyer: bool = False, is_admin: bool = False):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.is_buyer = is_buyer
        self.is_admin = is_admin
        
        # Copy buttons
        self.add_item(ui.Button(
            label="Copy Address",
            emoji="📋",
            style=discord.ButtonStyle.secondary,
            custom_id=f"copy_address_{ticket_id}"
        ))
        
        self.add_item(ui.Button(
            label="Copy Amount",
            emoji="💰",
            style=discord.ButtonStyle.secondary,
            custom_id=f"copy_amount_{ticket_id}"
        ))
        
        # Action buttons based on user role
        if is_admin or is_buyer:
            self.add_item(ui.Button(
                label="Release Funds",
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"release_{ticket_id}"
            ))
        
        # Dispute button for all users
        self.add_item(ui.Button(
            label="Dispute",
            emoji="⚠️",
            style=discord.ButtonStyle.danger,
            custom_id=f"dispute_{ticket_id}"
        ))
        
        # Admin controls
        if is_admin:
            self.add_item(ui.Button(
                label="Force Cancel",
                emoji="❌",
                style=discord.ButtonStyle.danger,
                custom_id=f"cancel_{ticket_id}"
            ))

class SparklesFlowManager:
    """STRICT Sparkles-style flow manager - edit-only behavior"""
    
    @staticmethod
    async def create_guided_setup(ticket_id: int, channel: discord.TextChannel, buyer_id: int, seller_id: int, crypto: str) -> discord.Message:
        """STRICT: Single message, no setup embed, only main dashboard"""
        
        # STRICT: Only main dashboard embed
        deal_embed = SparklesEmbedBuilder.create_main_deal_dashboard(
            ticket_id, buyer_id, seller_id, 0, crypto, 'Pending'
        )
        
        # STRICT: Single message with view
        deal_msg = await channel.send(embed=deal_embed, view=SparklesButtonSystem.create_main_deal_view(ticket_id, 'Pending', buyer_id, buyer_id, seller_id))
        
        # Store for updates
        deal_summaries[ticket_id] = deal_msg
        
        return deal_msg
    
    @staticmethod
    async def update_deal_dashboard(ticket_id: int, status: str, **kwargs):
        """STRICT: ALWAYS edit same message, NEVER send new"""
        if ticket_id not in deal_summaries:
            return
        
        ticket = get_ticket(ticket_id)
        if not ticket:
            return
        
        buyer_id = ticket[2]
        seller_id = ticket[3]
        amount = ticket[5]
        crypto = ticket[4]
        
        confirmations = kwargs.get('confirmations', 0)
        required_confs = kwargs.get('required_confs', 1)
        
        # STRICT: Always edit, never send new
        updated_embed = SparklesEmbedBuilder.create_main_deal_dashboard(
            ticket_id, buyer_id, seller_id, amount, crypto, status,
            confirmations, required_confs
        )
        
        updated_view = SparklesButtonSystem.create_main_deal_view(ticket_id, status, buyer_id, buyer_id, seller_id)
        
        try:
            await deal_summaries[ticket_id].edit(embed=updated_embed, view=updated_view)
        except discord.NotFound:
            channel = deal_summaries[ticket_id].channel
            deal_summaries[ticket_id] = await channel.send(embed=updated_embed, view=updated_view)
        except discord.HTTPException:
            pass
    
    @staticmethod
    async def send_status_update(channel: discord.TextChannel, status: str, details: str = ""):
        """Exact status update matching screenshots"""
        status_config = STATUS_CONFIG.get(status, STATUS_CONFIG['pending'])
        
        embed = discord.Embed(
            title=status_config['label'],
            description=details,
            color=status_config['color']
        )
        
        await channel.send(embed=embed)
    
    @staticmethod
    async def send_error_message(channel: discord.TextChannel, error: str, guidance: str = ""):
        """Exact error message matching screenshots"""
        embed = discord.Embed(
            title="Error",
            description=error,
            color=PREMIUM_COLORS['warning']
        )
        
        if guidance:
            embed.add_field(name="Next step", value=guidance, inline=False)
        
        await channel.send(embed=embed)
    
    @staticmethod
    async def send_success_message(channel: discord.TextChannel, title: str, message: str):
        """Exact success message matching screenshots"""
        embed = discord.Embed(
            title=title,
            description=message,
            color=PREMIUM_COLORS['success']
        )
        
        await channel.send(embed=embed)

def log_action(action: str, ticket_id: int, user_id: int, details: str = ""):
    """Comprehensive action logging for security and audit"""
    timestamp = datetime.datetime.now().isoformat()
    log_entry = {
        'timestamp': timestamp,
        'action': action,
        'ticket_id': ticket_id,
        'user_id': user_id,
        'details': details
    }
    
    # Log to database
    log_event(ticket_id, f"{action}_{user_id}", details)
    
    # Also log to console for monitoring
    print(f"[ACTION] {timestamp} | {action.upper()} | Ticket #{ticket_id} | User {user_id} | {details}")
    append_control_log(
        f"[ACTION] {timestamp} | {action.upper()} | Ticket #{ticket_id} | User {user_id} | {details}"
    )

    # Send to log channel if available
    if LOG_CHANNEL_ID:
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title=f"🔍 Action Logged: {action}",
                description=f"Ticket #{ticket_id} | <@{user_id}>\n{details}",
                color=0x3B82F6
            )
            embed.set_footer(text=f"ID: {ticket_id} | {timestamp}")
            asyncio.create_task(log_channel.send(embed=embed))

def is_user_blacklisted(user_id: int) -> bool:
    """Check if user is blacklisted"""
    return user_id in user_blacklist

def add_to_blacklist(user_id: int, reason: str = ""):
    """Add user to blacklist with logging"""
    user_blacklist.add(user_id)
    log_action("BLACKLIST_ADD", 0, user_id, reason)
    print(f"[BLACKLIST] User {user_id} added to blacklist. Reason: {reason}")

def is_deal_locked(ticket_id: int) -> bool:
    """Check if deal is locked (no edits allowed)"""
    return ticket_id in active_deal_locks

def lock_deal(ticket_id: int):
    """Lock deal to prevent edits after funding"""
    active_deal_locks.add(ticket_id)
    log_action("DEAL_LOCK", ticket_id, 0, "Deal locked after payment detected")

def unlock_deal(ticket_id: int):
    """Unlock deal (admin only)"""
    active_deal_locks.discard(ticket_id)
    log_action("DEAL_UNLOCK", ticket_id, 0, "Deal unlocked by admin")


def get_ltc_wallet_balance(address):
    if not address:
        return 0.0
    try:
        response = requests.get(f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return 0.0

    try:
        confirmed = max(float(payload.get("balance", 0)) / 1e8, 0.0)
    except (TypeError, ValueError):
        confirmed = 0.0
    try:
        final_balance = max(float(payload.get("final_balance", 0)) / 1e8, 0.0)
    except (TypeError, ValueError):
        final_balance = 0.0
    try:
        unconfirmed = float(payload.get("unconfirmed_balance", 0)) / 1e8
    except (TypeError, ValueError):
        unconfirmed = 0.0

    return max(final_balance, confirmed + max(unconfirmed, 0.0))


def get_ltc_price_usd():
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd",
            timeout=10,
        )
        response.raise_for_status()
        price = float(response.json()["litecoin"]["usd"])
        if price > 0:
            return price
    except Exception:
        pass
    return 100.0


async def send_chunked_dm(user, header, lines, max_len=1700):
    current = header.strip()
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            await user.send(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        await user.send(current)


def get_wallet_rows_from_db(db_path):
    db_file = Path(db_path)
    if not db_file.exists():
        return []
    conn = sqlite3.connect(str(db_file))
    try:
        c = conn.cursor()
        c.execute("""
            SELECT ticket_id, wallet_address, status, crypto, encrypted_private
            FROM tickets
            WHERE TRIM(COALESCE(wallet_address, '')) != ''
            ORDER BY ticket_id DESC
        """)
        return c.fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def candidate_wallet_databases():
    candidates = []
    seen = set()

    primary = Path(DB_NAME)
    if primary not in seen:
        candidates.append(primary)
        seen.add(primary)

    backup_dir = Path(DB_BACKUP_DIR)
    if backup_dir.exists():
        backups = sorted(
            [p for p in backup_dir.glob("*.db") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for backup in backups[:50]:
            if backup not in seen:
                candidates.append(backup)
                seen.add(backup)

    return candidates


async def discover_wallet_rows_from_logs(guild, max_messages=6000):
    if guild is None or LOG_CHANNEL_ID <= 0:
        return []

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel is None:
        try:
            log_channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except Exception:
            return []

    if log_channel is None or not hasattr(log_channel, "history"):
        return []

    rows = []
    seen = set()

    wallet_pattern = re.compile(r"wallet=(0x[a-fA-F0-9]{40}|[LM][a-km-zA-HJ-NP-Z1-9]{25,34}|ltc1[a-z0-9]{20,90})")
    ticket_pattern = re.compile(r"\[ticket:(\d+)\]")

    try:
        async for message in log_channel.history(limit=max_messages):
            text = str(message.content or "")
            if "wallet=" not in text and "payment_requested" not in text:
                continue

            ticket_match = ticket_pattern.search(text)
            ticket_id = int(ticket_match.group(1)) if ticket_match else 0

            for match in wallet_pattern.finditer(text):
                wallet_address = match.group(1).strip()
                if not wallet_address:
                    continue

                if looks_like_ltc_address(wallet_address):
                    crypto_guess = "LTC"
                elif looks_like_evm_address(wallet_address):
                    crypto_guess = "USDT"
                else:
                    continue

                key = (crypto_guess, wallet_address)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((ticket_id, wallet_address, "unknown", crypto_guess, None))
    except Exception:
        return rows

    return rows


def extract_wallet_candidates_from_text(text):
    if not text:
        return []
    wallets = []
    patterns = [
        r"0x[a-fA-F0-9]{40}",
        r"[LM][a-km-zA-HJ-NP-Z1-9]{25,34}",
        r"ltc1[a-z0-9]{20,90}",
    ]
    for pattern in patterns:
        wallets.extend(re.findall(pattern, text))
    return wallets


async def discover_wallet_rows_from_guild_history(guild, max_channels=250, max_messages_per_channel=2000):
    if guild is None:
        return []

    rows = []
    seen = set()
    channels = list(getattr(guild, "text_channels", []) or [])[:max_channels]
    bot_user_id = bot.user.id if bot.user else None

    for channel in channels:
        try:
            async for message in channel.history(limit=max_messages_per_channel):
                if bot_user_id and message.author and message.author.id != bot_user_id:
                    continue

                ticket_id = 0
                if channel.name and channel.name.startswith("ticket-"):
                    m = re.search(r"ticket-(\d+)", channel.name)
                    if m:
                        try:
                            ticket_id = int(m.group(1))
                        except ValueError:
                            ticket_id = 0

                candidates = []
                candidates.extend(extract_wallet_candidates_from_text(str(message.content or "")))

                for embed in (message.embeds or []):
                    candidates.extend(extract_wallet_candidates_from_text(str(getattr(embed, "title", "") or "")))
                    candidates.extend(extract_wallet_candidates_from_text(str(getattr(embed, "description", "") or "")))
                    for field in (getattr(embed, "fields", []) or []):
                        field_name = str(getattr(field, "name", "") or "").lower()
                        field_value = str(getattr(field, "value", "") or "")
                        if "payment address" in field_name or "address" in field_name:
                            candidates.extend(extract_wallet_candidates_from_text(field_value))

                for wallet_address in candidates:
                    if looks_like_ltc_address(wallet_address):
                        crypto_guess = "LTC"
                    elif looks_like_evm_address(wallet_address):
                        crypto_guess = "USDT"
                    else:
                        continue
                    key = (crypto_guess, wallet_address)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append((ticket_id, wallet_address, "unknown", crypto_guess, None))
        except Exception:
            continue

    return rows


def database_row_counts(db_path):
    db_file = Path(db_path)
    if not db_file.exists():
        return 0, 0
    try:
        conn = sqlite3.connect(str(db_file))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM tickets")
        ticket_count = int(c.fetchone()[0] or 0)
        c.execute("SELECT COUNT(*) FROM tickets WHERE TRIM(COALESCE(wallet_address, '')) != ''")
        wallet_count = int(c.fetchone()[0] or 0)
        conn.close()
        return ticket_count, wallet_count
    except sqlite3.Error:
        return 0, 0


def ensure_tracked_wallets_table():
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_wallets (
                wallet_address TEXT PRIMARY KEY,
                crypto TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_tracked_wallet_rows():
    ensure_tracked_wallets_table()
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT wallet_address, crypto, note
            FROM tracked_wallets
            ORDER BY created_at DESC
            """
        )
        rows = c.fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()

    normalized = []
    for wallet_address, crypto, note in rows:
        normalized.append((0, wallet_address, "tracked", crypto, None))
    return normalized


def add_tracked_wallet(wallet_address, crypto, note=""):
    ensure_tracked_wallets_table()
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO tracked_wallets(wallet_address, crypto, note) VALUES (?, ?, ?)",
            (wallet_address, crypto, note),
        )
        conn.commit()
    finally:
        conn.close()


def remove_tracked_wallet(wallet_address):
    ensure_tracked_wallets_table()
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute("DELETE FROM tracked_wallets WHERE wallet_address=?", (wallet_address,))
        conn.commit()
        return c.rowcount
    finally:
        conn.close()


# Command to DM the user all wallets with money in them
@bot.command(name="mywallets", help="DMs you all wallets with any money in them and their private keys, sorted by balance, regardless of status.")
async def mywallets(ctx):
    try:
        if ctx.author.id != ADMIN_ID:
            await ctx.send("Only the configured admin can use this command.", delete_after=10)
            return

        rows = []
        source_dbs = []
        for db_path in candidate_wallet_databases():
            db_rows = get_wallet_rows_from_db(db_path)
            if not db_rows:
                continue
            source_dbs.append(str(db_path))
            rows.extend(db_rows)

        tracked_rows = get_tracked_wallet_rows()
        if tracked_rows:
            rows.extend(tracked_rows)
            source_dbs.append("tracked_wallets_table")

        if not rows:
            log_rows = await discover_wallet_rows_from_logs(ctx.guild)
            if log_rows:
                rows.extend(log_rows)
                source_dbs.append("discord_log_channel")

        if not rows:
            history_rows = await discover_wallet_rows_from_guild_history(ctx.guild)
            if history_rows:
                rows.extend(history_rows)
                source_dbs.append("guild_history_scan")

        if not rows:
            await ctx.author.send(
                f"No generated wallets were found.\n"
                f"Active DB: `{Path(DB_NAME).resolve()}`\n"
                f"Backup dir checked: `{Path(DB_BACKUP_DIR).resolve()}`"
            )
            await ctx.send("DM sent (no generated wallets found).", delete_after=10)
            return

        # Keep one row per unique generated wallet.
        unique_wallets = {}
        for ticket_id, wallet_address, status, crypto, encrypted_private in rows:
            clean_address = str(wallet_address or "").strip()
            clean_crypto = str(crypto or "").upper().strip()
            if not clean_address or not clean_crypto:
                continue
            key = (clean_crypto, clean_address)
            if key not in unique_wallets:
                unique_wallets[key] = (ticket_id, clean_address, status, clean_crypto, encrypted_private)

        ltc_price_usd = get_ltc_price_usd()
        wallet_list = []
        for ticket_id, wallet_address, status, crypto, encrypted_private in unique_wallets.values():
            crypto_display = asset_label(crypto)

            # Detect by address shape first so mislabeled rows still get scanned.
            if looks_like_ltc_address(wallet_address) or crypto == "LTC":
                balance = get_ltc_wallet_balance(wallet_address)
                usd_value = balance * ltc_price_usd
                balance_str = f"{format_asset_amount(balance, 'LTC')} LTC (~${usd_value:.2f})"
                crypto_display = "LTC"
            else:
                if not looks_like_evm_address(wallet_address):
                    continue
                # EVM address can hold either BEP-20 or ETH USDT; check both and keep highest.
                _paid, _conf, _txid, balance_bep20 = detect_usdt_payment(
                    wallet_address,
                    999999999,
                    network="BEP20",
                )
                _paid, _conf, _txid, balance_eth = detect_usdt_payment(
                    wallet_address,
                    999999999,
                    network="ETH",
                )
                balance = max(float(balance_bep20 or 0.0), float(balance_eth or 0.0))
                usd_value = float(balance or 0.0)
                if float(balance_bep20 or 0.0) >= float(balance_eth or 0.0):
                    crypto_display = "USDT [BEP-20]"
                else:
                    crypto_display = "USDT [ETH]"
                balance_str = f"{format_asset_amount(balance, 'USDT')} {crypto_display} (~${usd_value:.2f})"

            if float(balance or 0.0) <= 0:
                continue

            try:
                priv = decrypt_key(encrypted_private) if encrypted_private else None
            except Exception:
                priv = "(decryption failed)"
            if not priv:
                priv = "(not available - wallet recovered without DB key)"

            wallet_list.append({
                "ticket_id": ticket_id,
                "crypto": crypto_display,
                "wallet_address": wallet_address,
                "balance": balance,
                "usd_value": usd_value,
                "balance_str": balance_str,
                "priv": priv,
                "status": status
            })

        wallet_list.sort(key=lambda x: x["usd_value"], reverse=True)

        if not wallet_list:
            await ctx.author.send("No generated on-chain wallets currently hold funds.")
            await ctx.send("DM sent (no funded generated wallets found).", delete_after=10)
            return

        lines = []
        for w in wallet_list:
            lines.append(
                f"[Ticket #{w['ticket_id']}] {w['crypto']} wallet: `{w['wallet_address']}` | "
                f"Balance: {w['balance_str']} | Status: {w['status']}"
            )
            lines.append(f"Private Key: `{w['priv']}`")
            lines.append("")

        await send_chunked_dm(
            ctx.author,
            "All generated wallets with funds (highest to lowest, any status):",
            lines,
        )
        await ctx.author.send(f"Scanned databases: {', '.join(f'`{p}`' for p in source_dbs)}")
        await ctx.send("DM sent with all wallets, balances, and private keys.", delete_after=10)
    except Exception as exc:
        await ctx.send(f"Error: {exc}")


@bot.command(name="addwallet", help="Manually add a wallet so !mywallets always checks it.")
async def addwallet(ctx, wallet_address: str, *, note: str = ""):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    wallet_address = str(wallet_address or "").strip()
    if looks_like_ltc_address(wallet_address):
        crypto = "LTC"
    elif looks_like_evm_address(wallet_address):
        crypto = "USDT"
    else:
        await ctx.send("Invalid wallet address format.")
        return

    add_tracked_wallet(wallet_address, crypto, note.strip()[:200])
    await ctx.send(f"Tracked wallet added: `{wallet_address}` ({crypto})")


@bot.command(name="removewallet", help="Remove a manually tracked wallet.")
async def removewallet(ctx, wallet_address: str):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    removed = remove_tracked_wallet(str(wallet_address or "").strip())
    if removed:
        await ctx.send(f"Removed tracked wallet: `{wallet_address}`")
    else:
        await ctx.send(f"Wallet not found in tracked list: `{wallet_address}`")


@bot.command(name="trackedwallets", help="List wallets manually pinned for !mywallets.")
async def trackedwallets(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    ensure_tracked_wallets_table()
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute("SELECT wallet_address, crypto, note, created_at FROM tracked_wallets ORDER BY created_at DESC")
        rows = c.fetchall()
    finally:
        conn.close()

    if not rows:
        await ctx.send("No tracked wallets saved.")
        return

    lines = [f"`{w}` | `{c}` | note: `{(n or '').strip() or '-'}` | added `{t}`" for w, c, n, t in rows[:100]]
    await ctx.send("Tracked wallets:\n" + "\n".join(lines))


@bot.command(name="lockdown", help="Enable/disable safety lockdown for risky actions.")
async def lockdown(ctx, state: str = "status"):
    global SAFETY_LOCKDOWN
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send(f"Only admin ID `{ADMIN_ID}` or server owner can use this command.")
        return

    desired = str(state or "status").strip().lower()
    if desired in ("on", "enable", "enabled", "true", "1"):
        SAFETY_LOCKDOWN = True
        await audit(ctx.guild, 0, "safety_lockdown_enabled", f"by={ctx.author.id}")
        await ctx.send("Safety lockdown is now `ON`. Risky actions are blocked.")
        return
    if desired in ("off", "disable", "disabled", "false", "0"):
        SAFETY_LOCKDOWN = False
        await audit(ctx.guild, 0, "safety_lockdown_disabled", f"by={ctx.author.id}")
        await ctx.send("Safety lockdown is now `OFF`.")
        return

    await ctx.send(f"Safety lockdown status: `{'ON' if SAFETY_LOCKDOWN else 'OFF'}`")


@bot.command(name="dbinfo", help="Show database path and ticket counts for troubleshooting.")
async def dbinfo(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    db_path = Path(DB_NAME)
    backup_dir = Path(DB_BACKUP_DIR)
    exists = db_path.exists()
    size = db_path.stat().st_size if exists else 0

    ticket_count = 0
    wallet_count = 0
    if exists:
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM tickets")
            ticket_count = int(c.fetchone()[0] or 0)
            c.execute("SELECT COUNT(*) FROM tickets WHERE TRIM(COALESCE(wallet_address, '')) != ''")
            wallet_count = int(c.fetchone()[0] or 0)
            conn.close()
        except sqlite3.Error:
            pass

    backups = []
    if backup_dir.exists():
        backups = [p for p in backup_dir.glob("*.db") if p.is_file()]

    msg = (
        f"DB path: `{db_path.resolve()}`\n"
        f"DB exists: `{exists}`\n"
        f"DB size bytes: `{size}`\n"
        f"Tickets in active DB: `{ticket_count}`\n"
        f"Rows with wallet_address: `{wallet_count}`\n"
        f"Backup dir: `{backup_dir.resolve()}`\n"
        f"Backup .db files: `{len(backups)}`"
    )
    await ctx.send(msg)


@bot.command(name="dbbackups", help="List backup database files with ticket/wallet row counts.")
async def dbbackups(ctx):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    backup_dir = Path(DB_BACKUP_DIR)
    if not backup_dir.exists():
        await ctx.send(f"Backup directory does not exist: `{backup_dir}`")
        return

    backups = sorted(
        [p for p in backup_dir.glob("*.db") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        await ctx.send(f"No backup .db files found in `{backup_dir}`")
        return

    lines = []
    for path in backups[:30]:
        ticket_count, wallet_count = database_row_counts(path)
        modified = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"`{path.name}` | modified `{modified}` | tickets `{ticket_count}` | wallet_rows `{wallet_count}` | bytes `{path.stat().st_size}`"
        )

    await ctx.send("Backups:\n" + "\n".join(lines))


@bot.command(name="restorebackup", help="Restore active DB from a backup file name, or use 'latest'.")
async def restorebackup(ctx, backup_name: str = "latest"):
    if ctx.author.id != ADMIN_ID:
        await ctx.send("Only the configured admin can use this command.", delete_after=10)
        return

    backup_dir = Path(DB_BACKUP_DIR)
    backups = sorted(
        [p for p in backup_dir.glob("*.db") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        await ctx.send("No backup .db files available to restore.")
        return

    selected = None
    if str(backup_name).strip().lower() == "latest":
        selected = backups[0]
    else:
        safe_name = Path(backup_name).name
        selected = backup_dir / safe_name
        if not selected.exists() or not selected.is_file():
            await ctx.send(f"Backup file not found: `{safe_name}`")
            return

    active_db = Path(DB_NAME)
    active_db.parent.mkdir(parents=True, exist_ok=True)

    if active_db.exists():
        pre_restore_name = f"pre_restore_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        pre_restore_path = backup_dir / pre_restore_name
        try:
            shutil.copy2(active_db, pre_restore_path)
        except Exception as exc:
            await ctx.send(f"Failed to snapshot current DB before restore: `{exc}`")
            return

    try:
        shutil.copy2(selected, active_db)
    except Exception as exc:
        await ctx.send(f"Restore failed: `{exc}`")
        return

    ticket_count, wallet_count = database_row_counts(active_db)
    await ctx.send(
        f"Restored active DB from `{selected.name}`.\n"
        f"Active DB now has tickets `{ticket_count}`, wallet_rows `{wallet_count}`.\n"
        f"If monitors still show old state, restart the bot once."
    )
slash_synced = False
withdraw_cooldowns = {}
withdraw_retry_tasks = {}

PAYMENT_POLL_INTERVAL_SECONDS = max(PAYMENT_POLL_INTERVAL_SECONDS, 10)

class RequestCashAppView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💸 Start Trade", style=discord.ButtonStyle.primary, custom_id="panel_request_cashapp", emoji="💸")
    async def cashapp(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("CASHAPP"))

# --- Cash App Utilities ---
def generate_cashapp_wallet():
    # Generate a random $cashtag
    tag = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
    return {"address": f"${tag}"}

def build_cashapp_payment_embed(ticket, wallet_address):
    amount_usd = float(ticket[5])
    embed = discord.Embed(
        title="💸 Cash App Payment Required",
        description=(
            "📱 **Send payment to the $cashtag below**\n\n"
            "⚠️ **Important:** Send the **exact amount** to avoid delays"
        ),
        color=0x00C244,
    )
    embed.add_field(name="💰 Amount Due", value=f"**${amount_usd:.2f}**", inline=True)
    embed.add_field(name="📱 $Cashtag", value=f"**{wallet_address}**", inline=True)
    embed.add_field(name="⏱️ Status", value="**Awaiting Payment**", inline=False)
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843547/cashapp.png")
    embed.set_footer(text="🐕 Dog Auto Middleman | Secure Escrow Service")
    return embed

# --- Cash App Panel Command ---
@bot.command(name="cashapp", help="Show the Cash App-only panel for creating a Cash App ticket")
async def cashapp_panel(ctx):
    layout = ui.LayoutView(timeout=None)
    layout.add_item(
        ui.Container(
            ui.TextDisplay(
                "## Dog's Auto Middleman\n"
                "> • Paid Service\n"
                "> • Read our ToS before using the bot: <#1489999569163911299>"
            ),
            ui.Separator(),
            ui.TextDisplay(
                "## Fees:\n"
                "> • Deals $250+: **$1.50**\n"
                "> • Deals under $250: **$0.50**\n"
                "> • Deals under $50 are FREE"
            ),
            accent_color=0x2B2D31,
        )
    )
    layout.add_item(ui.Separator(visible=False))
    layout.add_item(
        ui.Container(
            ui.TextDisplay(
                "## 💵 • Request Cash App • 💵\n"
                "> • Cash App (Fiat, USD)"
            ),
            ui.ActionRow(
                ui.Button(
                    label="Request Cash App",
                    style=discord.ButtonStyle.success,
                    custom_id="panel_request_cashapp",
                )
            ),
            accent_color=0x0F8F6F,
        )
    )
    await ctx.send(view=layout)

# --- Slash command for Cash App confirmation ---
@bot.tree.command(name="cashapp", description="Confirm a Cash App payment in this ticket (admin only)")
async def cashapp_slash(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    member = interaction.user
    member_permissions = getattr(member, "guild_permissions", None)
    is_admin = bool(member_permissions and member_permissions.administrator)
    is_owner = interaction.user.id == interaction.guild.owner_id
    if interaction.user.id != ADMIN_ID and not is_owner and not is_admin:
        await interaction.response.send_message("Only administrators, the server owner, or ADMIN_ID can use this command.", ephemeral=True)
        return

    # Only allow in ticket channels
    ticket = get_ticket_by_channel(interaction.channel.id)
    if not ticket or ticket[4] != "CASHAPP":
        await interaction.response.send_message("This command can only be used in a Cash App ticket channel.", ephemeral=True)
        return

    if str(ticket[6]).lower() == "paid":
        await interaction.response.send_message("This ticket is already marked as paid.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    wallet_address = ticket[7]
    amount_usd = float(ticket[5])
    confirmed_embed = discord.Embed(
        title="??  Cash App Payment Confirmed",
        description="The Cash App payment has been auto-confirmed. You may proceed with your trade.",
        color=0x00C244,
    )
    confirmed_embed.add_field(name="Total Amount Received", value=f"`$ {amount_usd:.2f}`", inline=True)
    confirmed_embed.add_field(name="Cash App $Cashtag", value=f"```{wallet_address}```", inline=False)
    await interaction.channel.send(embed=confirmed_embed)

    update_ticket(ticket[0], status="paid")
    await audit(interaction.guild, ticket[0], "payment_confirmed", f"cashapp={wallet_address} usd={amount_usd:.2f} by={interaction.user.id}")

    release_embed = discord.Embed(
        title="?  You may proceed with your trade.",
        description=(
            f"1. <@{ticket[3]}> Give your trader the items or payment you agreed on.\n\n"
            f"2. <@{ticket[2]}> Once you have received your items, click \"Release\" so your trader can claim the Cash App funds."
        ),
        color=0x00C244,
    )
    channel = interaction.channel
    try:
        release_msg = await channel.send(
            f"<@{ticket[2]}> <@{ticket[3]}>",
            embed=release_embed,
            view=ReleaseRefundView(ticket[0], "CASHAPP"),
        )
        update_ticket(ticket[0], message_id=release_msg.id)
        await audit(interaction.guild, ticket[0], "release_controls_posted", f"message_id={release_msg.id}")
    except Exception as exc:
        await audit(interaction.guild, ticket[0], "release_controls_post_failed", str(exc)[:200])

    await interaction.followup.send("Cash App payment marked as paid.", ephemeral=True)


class PaymentModal(ui.Modal, title="Payment Information"):
    amount = ui.TextInput(label="Amount", placeholder="Enter amount")
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Payment submitted", ephemeral=True)

class RequestPayPalView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Start Trade", style=discord.ButtonStyle.primary, custom_id="panel_request_paypal", emoji="💳")
    async def button_callback(self, interaction: discord.Interaction):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("PAYPAL"))

@bot.command(name="paypal", help="Show the PayPal-only panel for creating a PayPal ticket")
async def paypal_panel(ctx):
    layout = ui.LayoutView(timeout=None)
    layout.add_item(
        ui.Container(
            ui.TextDisplay(
                "## Dog's Auto Middleman\n"
                "> • Paid Service\n"
                "> • Read our ToS before using the bot: <#1489999569163911299>"
            ),
            ui.Separator(),
            ui.TextDisplay(
                "## Fees:\n"
                "> • Deals $250+: **$1.50**\n"
                "> • Deals under $250: **$0.50**\n"
                "> • Deals under $50 are FREE"
            ),
            accent_color=0x2B2D31,
        )
    )
    layout.add_item(ui.Separator(visible=False))
    layout.add_item(
        ui.Container(
            ui.TextDisplay(
                "## 💸 • Request PayPal • 💸\n"
                "> • PayPal (Fiat, USD)"
            ),
            ui.ActionRow(
                ui.Button(
                    label="Request PayPal",
                    style=discord.ButtonStyle.primary,
                    custom_id="panel_request_paypal",
                )
            ),
            accent_color=0x4752C4,
        )
    )
    await ctx.send(view=layout)

def build_main_panel_layout(use_custom_button_emojis: bool = True) -> ui.LayoutView:
    """Main Auto MM LayoutView (used by /panel and control API)."""
    layout = ui.LayoutView(timeout=None)

    main_section = ui.Section(
        ui.TextDisplay(
            (
                "## Dog's Auto Middleman\n"
                "> • Paid Service\n"
                "> • Read our ToS before using the bot: <#1489999569163911299>"
            )
        ),
        accessory=ui.Button(
            label="Tutorial",
            style=discord.ButtonStyle.secondary,
            url="https://www.youtube.com/watch?v=XIkpcT2WNPI",
        ),
    )
    layout.add_item(
        ui.Container(
            main_section,
            ui.Separator(),
            ui.TextDisplay(
                "## Fees:\n"
                "> • Deals $250+: **$1.50**\n"
                "> • Deals under $250: **$0.50**\n"
                "> • Deals under $50 are FREE"
            ),
            accent_color=0x2B2D31,
        )
    )

    ltc_container = ui.Container(
        ui.TextDisplay("## <:4887ltc:1489943780722213006> • Request Litecoin • <:4887ltc:1489943780722213006>"),
        ui.ActionRow(
            ui.Button(
                label="Request LTC",
                style=discord.ButtonStyle.primary,
                emoji="<:4887ltc:1489943780722213006>" if use_custom_button_emojis else None,
                custom_id="panel_request_ltc",
            )
        ),
        accent_color=0x4752C4,
    )
    layout.add_item(ltc_container)
    layout.add_item(ui.Separator(visible=False))

    usdt_container = ui.Container(
        ui.TextDisplay(
            "## <:7868usdt:1489944257367117966> • Request USDT (BEP-20)\n"
            "> • Network: BSC (BEP-20)"
        ),
        ui.ActionRow(
            ui.Button(
                label="Request USDT",
                style=discord.ButtonStyle.success,
                emoji="<:7868usdt:1489944257367117966>" if use_custom_button_emojis else None,
                custom_id="panel_request_usdt",
            )
        ),
        accent_color=0x0F8F6F,
    )
    layout.add_item(usdt_container)
    return layout


async def post_main_panel_to_channel(channel: discord.abc.Messageable) -> None:
    """Post the same Auto MM panel LayoutView to a channel (control panel)."""
    try:
        await channel.send(view=build_main_panel_layout(use_custom_button_emojis=True))
    except Exception as panel_exc:
        print(f"[PANEL_SEND_ERROR] {panel_exc}")
        await channel.send(view=build_main_panel_layout(use_custom_button_emojis=False))


@bot.hybrid_command(name='panel', aliases=['dog_panel'], help='Show the Dog Auto Middleman panel')
async def panel(ctx):
    """Dog's Auto Middleman panel rendered with LayoutView."""

    async def send_panel_view(view: ui.LayoutView):
        if getattr(ctx, "interaction", None):
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(view=view, ephemeral=True)
            else:
                await ctx.interaction.response.send_message(view=view, ephemeral=True)
        else:
            await ctx.send(view=view)

    try:
        await send_panel_view(build_main_panel_layout(use_custom_button_emojis=True))
    except Exception as panel_exc:
        print(f"[PANEL_SEND_ERROR] {panel_exc}")
        await send_panel_view(build_main_panel_layout(use_custom_button_emojis=False))


SETUP_CATEGORY_CHANNELS = {
    "🐕 Important": ["📜-rules", "📢-updates"],
    "🤝 Middleman Request": ["📨-mm-req", "📖-mm-tos", "🏆-clients-lb"],
    "🪙 Auto Crypto": ["💸-auto-crypto", "📘-tos-crypto"],
    "✅ Completed Deals": ["✅-completed-deals"],
}
READ_ONLY_CHANNELS = {"rules", "updates", "mm-tos", "tos-crypto", "completed-deals"}
SETUP_ROLES = ["Top 3 Client", "Top 10 Client"]


def _normalize_name(name: str) -> str:
    lowered = (name or "").strip().lower().replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9_-]+", "", lowered)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-_")
    return cleaned


def _find_category(guild: discord.Guild, category_name: str) -> Optional[discord.CategoryChannel]:
    target = _normalize_name(category_name)
    for cat in guild.categories:
        if _normalize_name(cat.name) == target:
            return cat
    return None


def _find_channel_in_category(category: discord.CategoryChannel, channel_name: str) -> Optional[discord.TextChannel]:
    target = _normalize_name(channel_name)
    for channel in category.text_channels:
        if _normalize_name(channel.name) == target:
            return channel
    return None


def _resolve_member_from_input(guild: discord.Guild, raw_value: str) -> Optional[discord.Member]:
    raw_target = (raw_value or "").strip()
    cleaned = raw_target.strip("<@!>")
    if cleaned.isdigit():
        member = guild.get_member(int(cleaned))
        if member:
            return member
    lowered = raw_target.lower().lstrip("@")
    return discord.utils.find(
        lambda m: m.name.lower() == lowered or m.display_name.lower() == lowered,
        guild.members,
    )


async def ensure_server_setup(guild: discord.Guild) -> Dict[str, List[str]]:
    deleted_channels: List[str] = []
    created_categories: List[str] = []
    created_channels: List[str] = []

    def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
        if TICKET_CATEGORY_ID > 0 and getattr(channel, "category_id", None) == TICKET_CATEGORY_ID:
            return True
        try:
            return get_ticket_by_channel(channel.id) is not None
        except Exception:
            return False

    # Remove existing channels before setup, except active/known ticket channels.
    for channel in list(guild.channels):
        if isinstance(channel, discord.CategoryChannel):
            continue
        if is_ticket_channel(channel):
            continue
        channel_ref = f"{getattr(channel, 'name', 'unknown')} ({channel.id})"
        try:
            await channel.delete(reason="Setup reset: delete non-ticket channels")
            deleted_channels.append(channel_ref)
        except Exception:
            pass

    for category_name, channel_names in SETUP_CATEGORY_CHANNELS.items():
        category = _find_category(guild, category_name)
        if category is None:
            category = await guild.create_category(category_name)
            created_categories.append(category_name)

        for channel_name in channel_names:
            channel = _find_channel_in_category(category, channel_name)
            if channel is None:
                channel = await guild.create_text_channel(channel_name, category=category)
                created_channels.append(f"{category_name}/{channel_name}")

            if _normalize_name(channel_name) in READ_ONLY_CHANNELS:
                await channel.set_permissions(guild.default_role, send_messages=False, add_reactions=False)

    return {"deleted_channels": deleted_channels, "categories": created_categories, "channels": created_channels}


def _find_text_channel_by_slug(guild: discord.Guild, slug_name: str) -> Optional[discord.TextChannel]:
    for channel in guild.text_channels:
        if _normalize_name(channel.name) == slug_name:
            return channel
    return None


def get_completed_deals_channel(guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
    if guild is None:
        return None
    if PROOF_CHANNEL_ID > 0:
        configured = guild.get_channel(PROOF_CHANNEL_ID)
        if isinstance(configured, discord.TextChannel):
            return configured
    return _find_text_channel_by_slug(guild, "completed-deals")


def build_completed_deal_embed(
    asset: str,
    amount_crypto: float,
    amount_usd: float,
    txid: str,
    sender_text: str = "Anonymous",
    receiver_text: str = "Anonymous",
) -> discord.Embed:
    asset_upper = str(asset or "").upper()
    if asset_upper == "LTC":
        title_prefix = "Ł"
        asset_label = "LTC"
        color = 0x111827
    elif asset_upper in {"USDT", "USDT_BEP20", "USDT_ETH"}:
        title_prefix = "₮"
        asset_label = "USDT"
        color = 0x10B981
    elif asset_upper == "PAYPAL":
        title_prefix = "💸"
        asset_label = "PayPal"
        color = 0x003087
    elif asset_upper == "CASHAPP":
        title_prefix = "💵"
        asset_label = "Cash App"
        color = 0x00C244
    else:
        title_prefix = "✅"
        asset_label = asset_upper or "ASSET"
        color = 0x2B2D31

    short_txid = txid
    if txid and len(txid) > 16:
        short_txid = f"{txid[:8]}...{txid[-8:]}"

    embed = discord.Embed(
        title=f"{title_prefix} • Trade Completed",
        description=f"**{amount_crypto:.8f} {asset_label}** (${amount_usd:.2f} USD)",
        color=color,
    )
    embed.add_field(name="Sender", value=f"`{sender_text}`", inline=True)
    embed.add_field(name="Receiver", value=f"`{receiver_text}`", inline=True)
    embed.add_field(name="Transaction ID", value=f"`{short_txid}`", inline=False)
    return embed


async def post_completed_deal_message(
    guild: Optional[discord.Guild],
    asset: str,
    amount_crypto: float,
    amount_usd: float,
    txid: str,
    sender_text: str = "Anonymous",
    receiver_text: str = "Anonymous",
):
    target = get_completed_deals_channel(guild)
    if target is None:
        return
    embed = build_completed_deal_embed(asset, amount_crypto, amount_usd, txid, sender_text, receiver_text)
    try:
        await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def build_public_tos_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Dog Auto Middleman Bot | ToS",
        description=(
            "While using our Automatic Middleman Bot, you must agree to a few things.\n\n"
            "♦ **1** We are not responsible for losses caused by user mistakes, such as sending funds to the wrong address/network or entering incorrect details.\n\n"
            "♦ **2** We are not responsible for losses caused by third-party interruptions such as rollbacks, terminations, or duped items.\n\n"
            "♦ **3** Trades involving prohibited items (for example Nitro, gift cards, accounts, scripts, methods, Discord assets) are not allowed.\n\n"
            "♦ **4** Disputes are handled fairly. If a party is inactive or uncooperative, funds may be released to the other trader after timeout.\n\n"
            "♦ **5** Any warranties or agreements must be explicitly stated **before** the trade begins.\n\n"
            "♦ **6** For currency trades (Crypto, PayPal, Robux, etc.), fees/taxes must be agreed beforehand."
        ),
        color=0x2B2D31,
    )
    return embed


class ShowTosView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Show ToS", style=discord.ButtonStyle.primary, custom_id="show_tos_button")
    async def show_tos(self, interaction: discord.Interaction, button: ui.Button):
        # Only the button clicker sees this ToS response.
        await interaction.response.send_message(embed=build_public_tos_embed(), ephemeral=True)


async def ensure_setup_roles(guild: discord.Guild) -> Dict[str, List[str]]:
    created_roles: List[str] = []
    existing_roles: List[str] = []

    existing_by_name = {role.name.lower(): role for role in guild.roles}
    for role_name in SETUP_ROLES:
        if role_name.lower() in existing_by_name:
            existing_roles.append(role_name)
            continue
        try:
            await guild.create_role(name=role_name, reason="Setup role creation")
            created_roles.append(role_name)
        except Exception:
            pass

    return {"created_roles": created_roles, "existing_roles": existing_roles}


async def seed_setup_messages(guild: discord.Guild):
    rules_channel = _find_text_channel_by_slug(guild, "rules")
    updates_channel = _find_text_channel_by_slug(guild, "updates")
    mm_tos_channel = _find_text_channel_by_slug(guild, "mm-tos")
    clients_lb_channel = _find_text_channel_by_slug(guild, "clients-lb")
    tos_crypto_channel = _find_text_channel_by_slug(guild, "tos-crypto")
    completed_channel = _find_text_channel_by_slug(guild, "completed-deals")
    mm_req_channel = _find_text_channel_by_slug(guild, "mm-req")
    auto_crypto_channel = _find_text_channel_by_slug(guild, "auto-crypto")

    if rules_channel is not None:
        rules_embed = discord.Embed(
            title="DOG'S MM SERVICE | RULES",
            description=(
                "♦ **AVOID ARGUMENTS, AND TOXICITY**\n"
                "- Do not start, partake, or instigate drama.\n"
                "- Do not troll, bully, or bother other members in the server.\n"
                "- Keep profanity minimal; do not disrespect staff or MMs.\n\n"
                "♦ **DON'T SCAM**\n"
                "- Scamming can result in DWC role and possible ban.\n\n"
                "♦ **NO DOXXING, ADVERTISING, IMPERSONATION, OR THREATS**\n"
                "- Do not post private information.\n"
                "- Do not doxx anyone.\n"
                "- Do not advertise in this server.\n"
                "- Do not impersonate MMs or staff.\n\n"
                "♦ **FOLLOW DISCORD TERMS OF SERVICE**\n"
                "- [Discord Guidelines](https://discord.com/guidelines)\n"
                "- [Discord Terms](https://discord.com/terms)"
            ),
            color=0x2B2D31,
        )
        await rules_channel.send(embed=rules_embed)

    if updates_channel is not None:
        updates_embed = discord.Embed(
            title="Update",
            description=(
                "A reminder due to increased scam attempts. By using our service you acknowledge:\n\n"
                "1. **We will not sugar-coat any situation.**\n"
                "2. **Sellers are obligated to record from the beginning of the trade.**\n"
                "3. **ToS breakers will not be tolerated.**\n"
                "4. **Do not spam ping staff for unnecessary issues.**"
            ),
            color=0x2B2D31,
        )
        await updates_channel.send(embed=updates_embed)

    tos_prompt = (
        "The ToS in " + (mm_req_channel.mention if mm_req_channel else "`#mm-req`") + " also apply here.\n"
        "You can start a trade with the Automatic MM bot here: "
        + (auto_crypto_channel.mention if auto_crypto_channel else "`#auto-crypto`")
    )

    if mm_tos_channel is not None:
        mm_tos_embed = discord.Embed(title="MM ToS", description=tos_prompt, color=0x2B2D31)
        await mm_tos_channel.send(embed=mm_tos_embed, view=ShowTosView())

    if tos_crypto_channel is not None:
        crypto_tos_embed = discord.Embed(title="Crypto ToS", description=tos_prompt, color=0x2B2D31)
        await tos_crypto_channel.send(embed=crypto_tos_embed, view=ShowTosView())

    if clients_lb_channel is not None:
        clients_embed = discord.Embed(
            title="Client Leaderboard",
            description=(
                "Top 3 of the previous month get **@Top 3 Client**.\n"
                "Top 10 get **@Top 10 Client**.\n"
                "Use this channel to post/update the latest leaderboard image."
            ),
            color=0x2B2D31,
        )
        await clients_lb_channel.send(embed=clients_embed)

    if completed_channel is not None:
        completed_embed = discord.Embed(
            title="Completed Deals",
            description="Completed deal proofs will be posted here automatically.",
            color=0x2B2D31,
        )
        await completed_channel.send(embed=completed_embed)


class ManualMMTradeModal(ui.Modal, title="Manual MM Request"):
    partner = ui.TextInput(
        label="Trader Username or ID",
        placeholder="e.g.: kookie.py / 693059117761429610",
        required=False,
    )
    trade_info = ui.TextInput(
        label="Trade Information",
        placeholder="Describe what each side is trading.",
        style=discord.TextStyle.paragraph,
        max_length=1024,
    )

    def __init__(self, category_value: str):
        super().__init__()
        self.category_value = category_value

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        mm_category = _find_category(guild, "Middleman Request")
        if mm_category is None:
            mm_category = await guild.create_category("🤝 Middleman Request")

        requester = interaction.user
        partner_member = _resolve_member_from_input(guild, self.partner.value)
        if partner_member is not None and partner_member.id == requester.id:
            partner_member = None

        suffix = "".join(secrets.choice("0123456789") for _ in range(4))
        user_slug = re.sub(r"[^a-z0-9_]+", "-", requester.display_name.lower()).strip("-") or "user"
        ticket_name = f"mm-{user_slug}-{suffix}"
        channel = await guild.create_text_channel(ticket_name, category=mm_category)

        bot_member = guild.me
        if bot_member is None and bot.user:
            try:
                bot_member = await guild.fetch_member(bot.user.id)
            except Exception:
                bot_member = None

        await channel.set_permissions(guild.default_role, read_messages=False)
        await channel.set_permissions(requester, read_messages=True, send_messages=True)
        if partner_member is not None:
            await channel.set_permissions(partner_member, read_messages=True, send_messages=True)
        if bot_member is not None:
            await channel.set_permissions(bot_member, read_messages=True, send_messages=True, manage_channels=True)
        for role in guild.roles:
            perms = getattr(role, "permissions", None)
            if perms and perms.administrator:
                await channel.set_permissions(role, read_messages=True, send_messages=True)

        target_user_id = partner_member.id if partner_member is not None else requester.id
        ticket_id = get_next_ticket_id()
        deal_id = generate_deal_id(ticket_id)
        save_ticket(
            ticket_id,
            channel.id,
            requester.id,
            target_user_id,
            "MANUAL",
            0,
            "",
            "",
            0,
            f"manual_category={self.category_value} | trade_info={self.trade_info.value.strip()}",
            deal_id,
        )

        intro = discord.Embed(
            title="Manual MM",
            description="Middleman Service",
            color=0x1B1D24,
        )
        intro.add_field(name="Requester", value=requester.mention, inline=True)
        intro.add_field(name="Category", value=f"`{self.category_value}`", inline=True)
        intro.add_field(name="Trade Info", value=self.trade_info.value.strip(), inline=False)
        if partner_member is not None:
            intro.add_field(name="Trader", value=partner_member.mention, inline=True)
        intro.set_footer(text=f"Deal ID: {deal_id}")

        await channel.send(
            content=f"{requester.mention}" + (f" {partner_member.mention}" if partner_member else ""),
            embed=intro,
            view=ManualTicketControlView(ticket_id, requester.id, target_user_id),
        )
        await interaction.response.send_message(f"Manual MM ticket created: {channel.mention}", ephemeral=True)


class ManualMMCategoryView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.select(
        custom_id="manual_mm_category_select",
        placeholder="Choose a category.",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Robux", value="robux"),
            discord.SelectOption(label="Crypto", value="crypto"),
            discord.SelectOption(label="Accounts", value="accounts"),
            discord.SelectOption(label="Other", value="other"),
        ],
    )
    async def category_select(self, interaction: discord.Interaction, select: ui.Select):
        await interaction.response.send_modal(ManualMMTradeModal(select.values[0]))


class ManualTicketControlView(ui.View):
    def __init__(self, ticket_id: int, user1_id: int, user2_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1_id = user1_id
        self.user2_id = user2_id
        self.claimed_by: Optional[int] = None

    @ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary)
    async def claim_ticket(self, interaction: discord.Interaction, button: ui.Button):
        member_perms = getattr(interaction.user, "guild_permissions", None)
        is_staff = bool(member_perms and member_perms.administrator) or interaction.user.id == ADMIN_ID
        if not is_staff:
            await interaction.response.send_message("Only staff can claim this ticket.", ephemeral=True)
            return
        if self.claimed_by is not None:
            await interaction.response.send_message(f"Ticket already claimed by <@{self.claimed_by}>.", ephemeral=True)
            return
        self.claimed_by = interaction.user.id
        button.disabled = True
        button.label = "Claimed"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(f"✅ <@{interaction.user.id}> claimed this ticket.", ephemeral=False)

    @ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants or the bot admin can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Close Ticket Confirmation",
                description="Both participants must click Confirm to close this ticket.",
                color=0xF0B429,
            ),
            view=CloseTicketConfirmView(self.ticket_id, self.user1_id, self.user2_id),
            ephemeral=False,
        )

    @ui.button(label="Cancel Trade", style=discord.ButtonStyle.secondary)
    async def cancel_trade(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants can cancel.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Cancel Trade Confirmation",
                description="Both participants must click Confirm to cancel this trade.",
                color=0xF0B429,
            ),
            view=CancelTradeConfirmView(self.ticket_id, self.user1_id, self.user2_id),
            ephemeral=False,
        )


@bot.tree.command(name="setup", description="Create required categories/channels for MM system.")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def setup_server(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    member = interaction.user
    is_admin = bool(getattr(member, "guild_permissions", None) and member.guild_permissions.administrator)
    if interaction.user.id != ADMIN_ID and not is_admin:
        await interaction.response.send_message("Only server administrators can use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    result = await ensure_server_setup(interaction.guild)
    role_result = await ensure_setup_roles(interaction.guild)
    await seed_setup_messages(interaction.guild)
    mm_req_channel = _find_text_channel_by_slug(interaction.guild, "mm-req")
    if mm_req_channel is not None:
        manual_embed = discord.Embed(
            title="Manual MM",
            description=(
                "Middleman Service\n"
                "✦ : To request a middleman from this server, click the drop-down menu to select what category your trade is in.\n\n"
                "✕ : Example: Trade is NFR Crow for Robux.\n"
                "Seller gives NFR Crow to middleman\n"
                "Buyer pays seller robux (After middleman confirms receiving pet)\n"
                "Middleman gives buyer NFR Crow (After seller confirmed receiving robux)\n\n"
                "NOTES:\n"
                "1. You must both agree on the deal before using a middleman.\n"
                "2. Specify what you're trading in the embed."
            ),
            color=0x1B1D24,
        )
        await mm_req_channel.send(embed=manual_embed, view=ManualMMCategoryView())

    categories_created = ", ".join(result["categories"]) if result["categories"] else "None"
    channels_created = ", ".join(result["channels"]) if result["channels"] else "None"
    deleted_channels = ", ".join(result["deleted_channels"]) if result["deleted_channels"] else "None"
    roles_created = ", ".join(role_result["created_roles"]) if role_result["created_roles"] else "None"
    roles_existing = ", ".join(role_result["existing_roles"]) if role_result["existing_roles"] else "None"
    await interaction.followup.send(
        "Setup complete.\n"
        f"Deleted non-ticket channels: {deleted_channels}\n"
        f"Categories created: {categories_created}\n"
        f"Channels created: {channels_created}\n"
        f"Roles created: {roles_created}\n"
        f"Roles already existed: {roles_existing}",
        ephemeral=True,
    )


class SendEmbedModal(ui.Modal, title="Send Embed"):
    title_input = ui.TextInput(label="Title", max_length=256)
    description_input = ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )
    color_input = ui.TextInput(
        label="Color (optional hex, e.g. #2B2D31)",
        required=False,
        max_length=16,
    )
    footer_input = ui.TextInput(
        label="Footer (optional)",
        required=False,
        max_length=2048,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        embed_color = 0x2B2D31
        raw_color = str(self.color_input.value or "").strip()
        if raw_color:
            if raw_color.startswith("#"):
                raw_color = raw_color[1:]
            if raw_color.lower().startswith("0x"):
                raw_color = raw_color[2:]
            try:
                embed_color = int(raw_color, 16)
            except ValueError:
                await interaction.response.send_message("Invalid color. Use hex like `#2B2D31`.", ephemeral=True)
                return

        embed = discord.Embed(
            title=str(self.title_input.value),
            description=str(self.description_input.value),
            color=embed_color,
        )
        footer = str(self.footer_input.value or "").strip()
        if footer:
            embed.set_footer(text=footer)

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Embed sent.", ephemeral=True)


@bot.tree.command(name="send", description="Open a form to send an embed as the bot.")
@discord.app_commands.default_permissions(administrator=True)
async def send_embed_message(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    member = interaction.user
    is_admin = bool(getattr(member, "guild_permissions", None) and member.guild_permissions.administrator)
    if interaction.user.id != ADMIN_ID and not is_admin:
        await interaction.response.send_message("Only administrators can use this command.", ephemeral=True)
        return

    await interaction.response.send_modal(SendEmbedModal())


@bot.tree.command(name="lock", description="Lock this channel to view-only mode.")
@discord.app_commands.describe(reason="Optional reason for audit logs")
async def lock_channel_for_user(interaction: discord.Interaction, reason: Optional[str] = None):
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("This command only supports standard text channels.", ephemeral=True)
        return

    member = interaction.user
    can_manage = bool(getattr(member, "guild_permissions", None) and member.guild_permissions.manage_channels)
    if interaction.user.id != ADMIN_ID and not can_manage:
        await interaction.response.send_message("You need Manage Channels permission to use this command.", ephemeral=True)
        return

    bot_member = interaction.guild.me
    if bot_member is None and bot.user:
        try:
            bot_member = await interaction.guild.fetch_member(bot.user.id)
        except Exception:
            bot_member = None

    lock_reason = reason or f"Locked by {interaction.user} ({interaction.user.id})"

    # Keep channel visible, but deny typing for everyone.
    await channel.set_permissions(
        interaction.guild.default_role,
        read_messages=True,
        send_messages=False,
        add_reactions=False,
        create_public_threads=False,
        create_private_threads=False,
        send_messages_in_threads=False,
        reason=lock_reason,
    )

    for target in list(channel.overwrites.keys()):
        if target == interaction.guild.default_role:
            continue
        if bot_member is not None and target == bot_member:
            continue
        await channel.set_permissions(
            target,
            read_messages=True,
            send_messages=False,
            add_reactions=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
            reason=lock_reason,
        )

    if bot_member is not None:
        await channel.set_permissions(bot_member, read_messages=True, send_messages=True, manage_channels=True, reason=lock_reason)

    await interaction.response.send_message("Channel locked to view-only. Nobody can type now.", ephemeral=True)


def log(guild, msg):
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch:
        asyncio.create_task(ch.send(msg))


class TutorialView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="📚 Tutorial", style=discord.ButtonStyle.secondary, emoji="📚")
    async def tutorial(self, interaction, button):
        await interaction.response.send_message("Tutorial is not configured on this build yet.", ephemeral=True)


async def send_security_alert(message, key="generic", cooldown_seconds=600):
    now = int(time.time())
    last = int(security_alert_last_sent.get(key, 0))
    if now - last < cooldown_seconds:
        return
    security_alert_last_sent[key] = now

    print(f"SECURITY ALERT: {message}")
    if LOG_CHANNEL_ID <= 0:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        return
    try:
        await channel.send(f"[SECURITY ALERT]\n{message}")
    except Exception:
        pass


def is_admin_user(guild, user):
    return user.id == ADMIN_ID or (guild is not None and user.id == guild.owner_id)


def fake_payment_enabled():
    return bool(ALLOW_FAKE_PAYMENTS)


def running_on_railway():
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


async def enforce_lockdown_for_ctx(ctx, action):
    if not SAFETY_LOCKDOWN:
        return True
    await ctx.send(
        f"Safety lockdown is enabled. `{action}` is temporarily blocked. "
        "Only unlock with `!lockdown off` when safe.",
        delete_after=12,
    )
    return False


async def enforce_lockdown_for_interaction(interaction, action):
    if not SAFETY_LOCKDOWN:
        return True
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"Safety lockdown is enabled. `{action}` is temporarily blocked.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Safety lockdown is enabled. `{action}` is temporarily blocked.",
                ephemeral=True,
            )
    except Exception:
        pass
    return False


async def reply_hybrid(ctx, message: str, ephemeral: bool = False):
    """Safely reply in hybrid commands for both slash and prefix invocations."""
    if getattr(ctx, "interaction", None):
        if ctx.interaction.response.is_done():
            await ctx.interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await ctx.interaction.response.send_message(message, ephemeral=ephemeral)
    else:
        await ctx.send(message)


def enforce_runtime_safety():
    if not REQUIRE_PERSISTENT_DB:
        return

    db_name = (DB_NAME or "").strip().lower()
    if running_on_railway() and db_name in ("data.db", "./data.db"):
        raise RuntimeError(
            "Unsafe storage setup detected: Railway + local SQLite file. "
            "Set REQUIRE_PERSISTENT_DB=false or move DB_NAME to persistent storage."
        )

    if BACKUP_STARTUP_MAX_AGE_MINUTES > 0:
        snapshot = database_safety_snapshot()
        age = snapshot.get("last_backup_age_seconds")
        max_age_seconds = BACKUP_STARTUP_MAX_AGE_MINUTES * 60
        if age is None or age > max_age_seconds:
            raise RuntimeError(
                "Backup freshness check failed at startup. "
                f"Last backup age: {age if age is not None else 'none'}s, "
                f"max allowed: {max_age_seconds}s."
            )


async def backup_loop():
    while True:
        try:
            backup_path = create_db_backup()
            print(f"DB backup created: {backup_path}")
            snapshot = database_safety_snapshot()
            age = snapshot.get("last_backup_age_seconds")
            if age is None:
                await send_security_alert("No backup found after backup cycle.", key="no_backup")
            elif age > max(BACKUP_ALERT_MAX_AGE_MINUTES, 1) * 60:
                await send_security_alert(
                    f"Backups are stale: last backup is {age}s old (threshold {BACKUP_ALERT_MAX_AGE_MINUTES * 60}s).",
                    key="stale_backup",
                )
        except Exception as exc:
            print(f"DB backup failed: {exc}")
            await send_security_alert(f"Database backup failed: {exc}", key="backup_failed")
        await asyncio.sleep(max(DB_BACKUP_INTERVAL_MINUTES, 5) * 60)


@bot.command(name='version', help='Check which code version is running')
async def version_check(ctx):
    """Instantly show current code version to verify Railway deployment"""
    runtime_version = get_runtime_code_version()
    embed = discord.Embed(
        title="Code Version Running",
        description=f"```\n{runtime_version}\n```",
        color=0x2ecc71
    )
    embed.set_footer(text="Use this to verify Railway has deployed the latest code")
    await ctx.send(embed=embed)


def looks_like_ltc_address(address):
    if not address:
        return False
    prefixes = ("L", "M", "ltc1")
    return address.startswith(prefixes) and 26 <= len(address) <= 90


def looks_like_evm_address(address):
    if not address:
        return False
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address.strip()))


def usdt_network_from_asset(asset):
    value = str(asset or "").upper().strip()
    if value in ("USDT_ETH", "USDT-ETH", "USDT_ETHEREUM"):
        return "ETH"
    return "BEP20"


def asset_label(asset):
    value = str(asset or "").upper().strip()
    if value == "USDT_ETH":
        return "USDT [ETH]"
    if value == "USDT_BEP20":
        return "USDT [BEP-20]"
    return value or "UNKNOWN"


def format_asset_amount(amount, asset=None):
    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    return f"{numeric_amount:.8f}".rstrip("0").rstrip(".")


async def audit(guild, ticket_id, event, details=""):
    log_event(ticket_id, event, details)
    if guild is not None:
        log(guild, f"[ticket:{ticket_id}] {event} {details}".strip())


def has_fake_payment_marker(ticket_id):
    events = get_ticket_events(ticket_id, limit=50)
    fake_events = {
        "fake_payment_triggered",
        "fake_payment_unconfirmed",
        "fake_payment_confirmed",
    }
    return any(event in fake_events for event, _details, _created in events)


def short_txid(txid):
    if not txid:
        return "pending"
    if len(txid) <= 16:
        return txid
    return f"{txid[:8]}...{txid[-8:]}"


def extract_txid(tx_result):
    if isinstance(tx_result, str):
        return tx_result
    if not isinstance(tx_result, dict):
        return None
    return tx_result.get("tx_hash") or tx_result.get("hash") or tx_result.get("txid")


def ltc_tx_link(txid):
    return f"https://live.blockcypher.com/ltc/tx/{txid}/"


def generate_random_txid(length=64):
    if length <= 0:
        return ""
    if length % 2 == 0:
        return secrets.token_hex(length // 2)
    return f"{secrets.token_hex(length // 2)}{secrets.token_hex(1)[0]}"


def txid_from_ticket_events(ticket_id):
    if not ticket_id:
        return None
    try:
        events = get_ticket_events(ticket_id, limit=80)
    except Exception:
        return None
    if not events:
        return None

    for _event, details, _created_at in events:
        details_text = str(details or "")
        match = re.search(r"\btxid=([A-Fa-f0-9]{16,128})\b", details_text)
        if match:
            return match.group(1)
    return None


def deal_related_txid(ticket, fallback_length=64):
    ticket_id = ticket[0] if ticket else None
    event_txid = txid_from_ticket_events(ticket_id)
    if event_txid:
        return event_txid
    return generate_random_txid(fallback_length)


async def enforce_sensitive_cooldown(ctx, command_name):
    now = time.time()
    key = (ctx.author.id, command_name)
    last_used = sensitive_command_last_used.get(key, 0)
    remaining = int(SENSITIVE_COMMAND_COOLDOWN_SECONDS - (now - last_used))
    if remaining > 0:
        await ctx.send(f"Slow down. Retry `{command_name}` in {remaining}s.")
        return False
    sensitive_command_last_used[key] = now
    return True


def is_valid_deal_amount(amount):
    return MIN_DEAL_USD <= amount <= MAX_DEAL_USD


def get_locked_amount_crypto(ticket):
    if not ticket or len(ticket) <= 13:
        return None
    try:
        value = ticket[13]
        if value is None:
            return None
        value = float(value)
        return value if value > 0 else None
    except Exception:
        return None

def ltc_seller_payout_usd(amount_usd):
    try:
        value = float(amount_usd)
    except (TypeError, ValueError):
        return 0.0
    # No fee subtracted, seller gets full amount.
    return max(value, 0.0)


def ltc_deposit_target_usd(amount_usd):
    try:
        value = float(amount_usd)
    except (TypeError, ValueError):
        return 0.0
    # Buyer always pays the deal amount (no extra top-up request).
    return value


def stablecoin_seller_payout_usd(amount_usd):
    try:
        value = float(amount_usd)
    except (TypeError, ValueError):
        return 0.0
    fee_multiplier = max(0.0, min(float(FEE_PERCENT), 100.0)) / 100.0
    return max(value * (1.0 - fee_multiplier), 0.0)


def seller_payout_usd(amount_usd, asset):
    if str(asset or "").upper().strip() == "LTC":
        return ltc_seller_payout_usd(amount_usd)
    return stablecoin_seller_payout_usd(amount_usd)


def sanitize_txid_text(value, max_length=120):
    if not value:
        return generate_random_txid()
    cleaned = value.replace("`", "").replace("\n", " ").replace("\r", " ").strip()
    if not cleaned:
        return generate_random_txid()
    return cleaned[:max_length]


def _rate_limited_error(err):
    return "limits reached" in str(err).lower()


async def retry_withdrawal(ticket_id, crypto, channel_id, message_id):
    try:
        for attempt in range(1, WITHDRAW_RETRY_MAX_ATTEMPTS + 1):
            await asyncio.sleep(WITHDRAW_RETRY_BASE_SECONDS * attempt)
            ticket = get_ticket(ticket_id)
            if not ticket or ticket[6] in ("completed", "cancelled"):
                return
            if not ticket[8] or not ticket[9]:
                return

            update_ticket(ticket_id, status="releasing")
            await audit(None, ticket_id, "withdraw_retry_attempt", f"attempt={attempt}")

            if crypto == "LTC":
                amount_ltc = usd_to_ltc(ltc_seller_payout_usd(ticket[5]))
                tx = send_ltc(ticket[9], amount_ltc, ticket[8])
            else:
                tx = send_usdt(ticket[9], seller_payout_usd(ticket[5], crypto), ticket[8], network=usdt_network_from_asset(crypto))

            txid = extract_txid(tx)
            provider_error = tx.get("error") if isinstance(tx, dict) else None
            if provider_error or not txid:
                update_ticket(ticket_id, status="paid")
                if _rate_limited_error(provider_error or tx):
                    continue
                await audit(None, ticket_id, "withdraw_retry_failed", str(tx)[:200])
                return

            update_ticket(ticket_id, status="completed")
            await audit(None, ticket_id, "withdraw_retry_success", f"txid={txid} address={ticket[9]}")
            channel = bot.get_channel(channel_id)
            if channel:
                try:
                    embed = discord.Embed(
                        title=SPARKLES_TITLE,
                        description="**WITHDRAWAL SUCCESSFUL**\nFunds were sent to seller address (automatic retry).",
                        color=0x00FF00,
                    )
                    embed.add_field(name="Transaction", value=f"`{txid}`", inline=False)
                    if crypto == "LTC":
                        embed.add_field(name="Explorer", value=ltc_tx_link(txid), inline=False)
                    embed.set_footer(text=SPARKLES_FOOTER)
                    await channel.send(embed=embed)
                except Exception:
                    pass
            return
    finally:
        withdraw_retry_tasks.pop(ticket_id, None)


def build_amount_embed(amount, description):
    embed = discord.Embed(
        title=f"💸 • USD amount set to **${amount:.2f}**",
        description=(
            "Please confirm the USD amount."
        ),
        color=0x2B2D31,
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843542/usd.png")
    embed.set_footer(text="🐕 Dog Auto Middleman | Secure Escrow Service")
    return embed


def build_payment_embed(ticket, wallet_address):
    crypto = ticket[4]
    amount_usd = float(ticket[5])
    amount_crypto = get_locked_amount_crypto(ticket) if crypto == "LTC" else amount_usd
    ltc_price_text = ""
    if crypto == "LTC":
        try:
            current_price = amount_usd / amount_crypto if amount_crypto else 0
            if current_price > 0:
                ltc_price_text = f"\nCurrent LTC Price: `${current_price:.2f}`"
        except Exception:
            pass
    embed = discord.Embed(
        title="📜 • Payment Information",
        description=(
            f"Make sure to send the EXACT amount in {crypto}."
            f"{ltc_price_text}\n"
            "This ticket will be closed within 20 minutes if no transaction was detected."
        ),
        color=0x2B2D31,
    )
    embed.add_field(name="USD Amount", value=f"`${amount_usd:.2f}`", inline=True)
    embed.add_field(name=f"{crypto} Amount", value=f"`{format_asset_amount(amount_crypto, crypto)}`", inline=True)
    embed.add_field(name="Payment Address", value=f"```{wallet_address}```", inline=False)
    return embed


def build_ticket_startup_embed(bot_user):
    embed = discord.Embed(
        title="👋 • Sparkles's Auto Middleman Service",
        description=(
            "Make sure to follow the steps and read the instructions thoroughly.\n"
            "Please explicitly state the trade details if the information below is inaccurate.\n"
            "By using this bot, you agree to our ToS # 🌸 • tos."
        ),
        color=0x2B2D31,
    )
    embed.set_thumbnail(url=str(bot_user.display_avatar.url))
    embed.set_footer(text="")
    embed.set_author(name="Auto Middleman", icon_url=str(bot_user.display_avatar.url))
    return embed


def build_ticket_unified_layout_view(bot_user, user_one, user_two, user_one_trade, user_two_trade, ticket_id, user1_id, user2_id):
    view = ui.LayoutView(timeout=None)
    user_one_avatar = resolve_display_media_url(user_one)
    user_two_avatar = resolve_display_media_url(user_two)

    delete_button = ui.Button(
        label="🧱 • Delete Ticket",
        style=discord.ButtonStyle.danger,
        custom_id=f"ticket_delete_{ticket_id}",
    )

    async def delete_callback(interaction: discord.Interaction):
        if interaction.user.id not in {user1_id, user2_id, ADMIN_ID}:
            await interaction.response.send_message(
                "Only ticket participants or the bot admin can delete this ticket.",
                ephemeral=True,
            )
            return
        await audit(interaction.guild, ticket_id, "ticket_deleted", f"deleted_by={interaction.user.id}")
        await interaction.response.send_message("Deleting ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket {ticket_id} deleted by {interaction.user.id}")

    delete_button.callback = delete_callback

    unified_container = ui.Container(
        ui.TextDisplay(
            "👋 • Sparkles's Auto Middleman Service\n"
            "> Make sure to follow the steps and read the instructions thoroughly.\n"
            "> Please explicitly state the trade details if the information below is inaccurate.\n"
            "> By using this bot, you agree to our ToS # 🌸 • tos."
        ),
        ui.Separator(),
        ui.Section(
            ui.TextDisplay(f"{user_one.mention}'s side:\n```{user_one_trade or ''}```"),
            accessory=ui.Thumbnail(media=user_one_avatar),
        ),
        ui.Separator(),
        ui.Section(
            ui.TextDisplay(f"{user_two.mention}'s side:\n```{user_two_trade or ''}```"),
            accessory=ui.Thumbnail(media=user_two_avatar),
        ),
        ui.Separator(),
        ui.ActionRow(delete_button),
        accent_color=0x2B2D31,
    )
    view.add_item(unified_container)
    return view


def build_role_selection_embed(crypto, roles):
    sender_id = next((user_id for user_id, role in roles.items() if role == "buyer"), None)
    receiver_id = next((user_id for user_id, role in roles.items() if role == "seller"), None)
    asset_text = "LTC" if crypto == "LTC" else asset_label(crypto)
    embed = discord.Embed(
        title="🛡️ • Select your role",
        description=(
            f"> • **\"Sender\"** if you are **Sending** {asset_text} to the bot.\n"
            f"> • **\"Receiver\"** if you are **Receiving** {asset_text} later from the bot."
        ),
        color=0x2B2D31,
    )
    embed.add_field(name="Sender", value=f"<@{sender_id}>" if sender_id else "...", inline=True)
    embed.add_field(name="Receiver", value=f"<@{receiver_id}>" if receiver_id else "...", inline=True)
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png")
    embed.set_footer(text="🐕 Dog Auto Middleman | Secure Escrow Service")
    return embed


def build_role_confirmation_embed(sender_id, receiver_id):
    embed = discord.Embed(
        title="🧩 • Is This Information Correct?",
        description="Is this information correct?",
        color=0x2B2D31,
    )
    embed.add_field(name="Sender", value=f"<@{sender_id}>", inline=True)
    embed.add_field(name="Receiver", value=f"<@{receiver_id}>", inline=True)
    embed.set_footer(text="Make sure you have selected the right role! If you didn't then click \"Incorrect\"")
    return embed


def build_set_amount_prompt_embed():
    embed = discord.Embed(
        title="💵 • Set the amount in USD value",
        description="",
        color=0x2B2D31,
    )
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843542/usd.png")
    embed.set_footer(text="🐕 Dog Auto Middleman | Secure Escrow Service")
    return embed


def build_waiting_transaction_embed(crypto, amount_required):
    embed = discord.Embed(
        title="Waiting for transaction...",
        description="Waiting for transaction...",
        color=0x2B2D31,
    )
    return embed


def build_detected_transaction_embed(txid, received_amount, required_amount, crypto):
    embed = discord.Embed(
        title="⚠️ • Transaction Detected",
        description="The transaction is currently **unconfirmed** and waiting for 1 confirmation.",
        color=0xF0B429,
    )
    usd_hint = "($1.00)"
    embed.add_field(name="Transaction", value=f"`{short_txid(txid)}` ({format_asset_amount(received_amount, crypto)} {crypto})", inline=False)
    embed.add_field(name="Amount Received", value=f"`{format_asset_amount(received_amount, crypto)}` {crypto} {usd_hint}", inline=True)
    embed.add_field(name="Required Amount", value=f"`{format_asset_amount(required_amount, crypto)}` {crypto} {usd_hint}", inline=True)
    embed.set_footer(text="You will be notified when the transaction is confirmed.")
    return embed


def build_confirmed_transaction_embed(txid, received_amount, required_amount, crypto):
    embed = discord.Embed(
        title="✅ • Transaction Confirmed!",
        description="",
        color=0x3BA55C,
    )
    usd_hint = "($1.00)"
    embed.add_field(name="Transactions", value=f"`{short_txid(txid)}` ({format_asset_amount(received_amount, crypto)} {crypto})", inline=False)
    embed.add_field(name="Total Amount Received", value=f"`{format_asset_amount(received_amount, crypto)}` {crypto} {usd_hint}", inline=False)
    return embed


def build_release_stage_embed(buyer_id, seller_id):
    embed = discord.Embed(
        title="✅ • You may proceed with your trade.",
        description=(
            f"1. <@{seller_id}> Give your trader the items or payment you agreed on.\n\n"
            f"2. <@{buyer_id}> Once you have received your items, click \"Release\" so your trader can claim the LTC."
        ),
        color=0x3BA55C,
    )
    return embed


class Step6ReleaseStageView(ui.View):
    def __init__(self, ticket_id, buyer_id, seller_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.seller_id = seller_id

    def _disable_all(self):
        for child in self.children:
            child.disabled = True

    @ui.button(label="Release", style=discord.ButtonStyle.success)
    async def release(self, interaction, button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only the buyer can click Release.", ephemeral=True)
            return
        self._disable_all()
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            embed=build_step7_seller_address_prompt_embed(),
            view=Step7EnterAddressView(self.ticket_id, self.seller_id),
            ephemeral=False,
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id not in {self.buyer_id, self.seller_id}:
            await interaction.response.send_message("Only ticket participants can cancel.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Cancel Trade Confirmation",
                description="Both participants must click Confirm to cancel this trade.",
                color=0xF0B429,
            ),
            view=CancelTradeConfirmView(self.ticket_id, self.buyer_id, self.seller_id),
            ephemeral=False,
        )


def build_step7_seller_address_prompt_embed():
    return discord.Embed(
        title="🕓 • What's Your LTC Address?",
        description="Make sure to paste your correct LTC address.",
        color=0x2B2D31,
    )


def build_step7_confirm_address_embed(address):
    return discord.Embed(
        title="⚠️ • Confirm Address",
        description=f"Address: `{address}`\n\nClick \"Confirm\" to send LTC or \"Back\" to cancel.",
        color=0x2B2D31,
    )


def build_step8_release_warning_embed():
    return discord.Embed(
        title="⚠️ Are you sure you want to release the LTC? ⚠️",
        description='Clicking "Confirm" will give your trader permission to withdraw the LTC.',
        color=0xF0B429,
    )


def build_step8_seller_confirm_embed():
    return discord.Embed(
        title="⚠️ Seller confirmation required",
        description='Seller must click "Confirm" to continue release.',
        color=0xF0B429,
    )


def build_step8_sending_embed():
    return discord.Embed(
        title="◌ • Sending...",
        description="Simulating transfer. No real crypto is being sent.",
        color=0x2B2D31,
    )


def build_step9_completion_embed(txid, amount_sent, crypto):
    embed = discord.Embed(
        title="✅ • Withdrawal Successful",
        color=0x3BA55C,
    )
    embed.description = "Use /setprivacy to display your user in `# 🌸 • completed`"
    embed.add_field(name="Transaction", value=f"`{txid}`", inline=False)
    embed.add_field(name="Amount Sent", value=f"`{format_asset_amount(amount_sent, crypto)}` {crypto}", inline=False)
    return embed


class Step9CloseTicketView(ui.View):
    def __init__(self, ticket_id, buyer_id, seller_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.seller_id = seller_id

    @ui.button(label="🔒 • Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction, button):
        if interaction.user.id not in {self.buyer_id, self.seller_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants or admin can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Close Ticket Confirmation",
                description="Both participants must click Confirm to close this ticket.",
                color=0xF0B429,
            ),
            view=CloseTicketConfirmView(self.ticket_id, self.buyer_id, self.seller_id),
            ephemeral=False,
        )


class CloseTicketConfirmView(ui.View):
    def __init__(self, ticket_id, user1_id, user2_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1_id = user1_id
        self.user2_id = user2_id
        self.confirms = set()
        self.created_at = time.time()
        self.completed = False
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.completed:
            await interaction.response.send_message("This action is already completed.", ephemeral=True)
            return
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants can confirm this.", ephemeral=True)
            return
        wait_remaining = 2 - (time.time() - self.created_at)
        if wait_remaining > 0:
            await interaction.response.send_message(f"Please wait {wait_remaining:.1f}s before confirming.", ephemeral=True)
            return
        if interaction.user.id in self.confirms:
            await interaction.response.send_message("You already confirmed this.", ephemeral=True)
            return

        self.confirms.add(interaction.user.id)
        await interaction.response.send_message(f"✅ <@{interaction.user.id}> confirmed.", ephemeral=False)
        if len(self.confirms) < 2:
            return

        self.completed = True
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        await audit(interaction.guild, self.ticket_id, "ticket_closed", "mutual_confirmation_complete")
        await interaction.channel.delete(reason=f"Ticket {self.ticket_id} closed after mutual confirmation.")

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants can cancel this confirmation.", ephemeral=True)
            return
        self.confirms = set()
        self.created_at = time.time()
        await interaction.response.send_message("Close ticket confirmation reset.", ephemeral=False)


class CancelTradeConfirmView(ui.View):
    def __init__(self, ticket_id, user1_id, user2_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1_id = user1_id
        self.user2_id = user2_id
        self.confirms = set()
        self.created_at = time.time()
        self.completed = False
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if self.completed:
            await interaction.response.send_message("This action is already completed.", ephemeral=True)
            return
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants can confirm this.", ephemeral=True)
            return
        wait_remaining = 2 - (time.time() - self.created_at)
        if wait_remaining > 0:
            await interaction.response.send_message(f"Please wait {wait_remaining:.1f}s before confirming.", ephemeral=True)
            return
        if interaction.user.id in self.confirms:
            await interaction.response.send_message("You already confirmed this.", ephemeral=True)
            return

        self.confirms.add(interaction.user.id)
        await interaction.response.send_message(f"✅ <@{interaction.user.id}> confirmed.", ephemeral=False)
        if len(self.confirms) < 2:
            return

        self.completed = True
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        update_ticket(self.ticket_id, status="cancelled")
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**TRADE CANCELLED**\nThe trade has been cancelled after both participants confirmed.",
            color=0xFF0000,
        )
        embed.set_footer(text=SPARKLES_FOOTER)
        await audit(interaction.guild, self.ticket_id, "trade_cancelled", "mutual_confirmation_complete")
        await interaction.channel.send(embed=embed)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id not in {self.user1_id, self.user2_id, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants can cancel this confirmation.", ephemeral=True)
            return
        self.confirms = set()
        self.created_at = time.time()
        await interaction.response.send_message("Cancel trade confirmation reset.", ephemeral=False)


async def run_step9_completion(ticket_id, channel):
    ticket = get_ticket(ticket_id)
    if not ticket:
        await channel.send("Completion failed: ticket not found.")
        return
    amount_usd = float(ticket[5] or 0)
    crypto = ticket[4] or "LTC"
    amount_sent = usd_to_ltc(ltc_seller_payout_usd(amount_usd)) if crypto == "LTC" else seller_payout_usd(amount_usd, crypto)
    txid = f"sim_tx_{ticket_id}_{int(time.time())}"
    update_ticket(ticket_id, status="completed")
    await audit(channel.guild, ticket_id, "withdraw_success_simulated", f"txid={txid} amount={amount_sent} {crypto}")
    await channel.send(
        embed=build_step9_completion_embed(txid, amount_sent, crypto),
        view=Step9CloseTicketView(ticket_id, ticket[2], ticket[3]),
    )


class Step7AddressModal(ui.Modal, title="Enter Address"):
    address = ui.TextInput(label="Wallet Address", placeholder="Enter seller LTC address")

    def __init__(self, ticket_id, seller_id):
        super().__init__()
        self.ticket_id = ticket_id
        self.seller_id = seller_id

    async def on_submit(self, interaction):
        if interaction.user.id != self.seller_id:
            await interaction.response.send_message("Only the seller can input the wallet address.", ephemeral=True)
            return
        entered_address = self.address.value.strip()
        if not entered_address:
            await interaction.response.send_message("Address is required.", ephemeral=True)
            return
        update_ticket(self.ticket_id, seller_address=entered_address)
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_step7_confirm_address_embed(entered_address),
            view=Step7ConfirmAddressView(self.ticket_id, self.seller_id, ticket[2], entered_address),
            ephemeral=False,
        )


class Step7EnterAddressView(ui.View):
    def __init__(self, ticket_id, seller_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.seller_id = seller_id

    @ui.button(label="Enter Your LTC Address", style=discord.ButtonStyle.primary)
    async def enter_address(self, interaction, button):
        if interaction.user.id != self.seller_id:
            await interaction.response.send_message("Only the seller can enter the address.", ephemeral=True)
            return
        await interaction.response.send_modal(Step7AddressModal(self.ticket_id, self.seller_id))


class Step7ConfirmAddressView(ui.View):
    def __init__(self, ticket_id, seller_id, buyer_id, address):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.seller_id = seller_id
        self.buyer_id = buyer_id
        self.address = address

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.seller_id:
            await interaction.response.send_message("Only the seller can confirm the address.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            embed=build_step8_release_warning_embed(),
            view=Step8BuyerReleaseConfirmView(self.ticket_id, self.buyer_id, self.seller_id),
            ephemeral=False,
        )

    @ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if interaction.user.id != self.seller_id:
            await interaction.response.send_message("Only the seller can go back.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_step7_seller_address_prompt_embed(),
            view=Step7EnterAddressView(self.ticket_id, self.seller_id),
            ephemeral=False,
        )


class Step8BuyerReleaseConfirmView(ui.View):
    def __init__(self, ticket_id, buyer_id, seller_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.seller_id = seller_id
        self.created_at = time.time()
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only the buyer can confirm release.", ephemeral=True)
            return
        remaining = 2 - (time.time() - self.created_at)
        if remaining > 0:
            await interaction.response.send_message(f"Please wait {remaining:.1f}s before confirming.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            embed=build_step8_seller_confirm_embed(),
            view=Step8SellerReleaseConfirmView(self.ticket_id, self.seller_id),
            ephemeral=False,
        )

    @ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only the buyer can go back.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Release cancelled.", ephemeral=False)


class Step8SellerReleaseConfirmView(ui.View):
    def __init__(self, ticket_id, seller_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.seller_id = seller_id
        self.created_at = time.time()
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.seller_id:
            await interaction.response.send_message("Only the seller can confirm release.", ephemeral=True)
            return
        remaining = 2 - (time.time() - self.created_at)
        if remaining > 0:
            await interaction.response.send_message(f"Please wait {remaining:.1f}s before confirming.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=build_step8_sending_embed(), ephemeral=False)
        await asyncio.sleep(2)
        await run_step9_completion(self.ticket_id, interaction.channel)


async def simulate_blockchain_detection_stage(ticket_id, channel, crypto, required_amount):
    """Step 5 only: sequential simulated blockchain detection states."""
    waiting_msg = await channel.send(embed=build_waiting_transaction_embed(crypto, required_amount))
    await asyncio.sleep(4)

    fake_txid = f"sim_{ticket_id}_{int(time.time())}"
    received_amount = required_amount
    await waiting_msg.edit(embed=build_detected_transaction_embed(fake_txid, received_amount, required_amount, crypto))

    update_ticket(ticket_id, status="unconfirmed")
    await audit(channel.guild, ticket_id, "payment_detected_simulated", f"txid={fake_txid} received={received_amount}")

    await asyncio.sleep(4)
    await waiting_msg.edit(embed=build_confirmed_transaction_embed(fake_txid, received_amount, required_amount, crypto))

    update_ticket(ticket_id, status="paid")
    await audit(channel.guild, ticket_id, "payment_confirmed_simulated", f"txid={fake_txid} received={received_amount}")
    ticket = get_ticket(ticket_id)
    if not ticket:
        await channel.send("Release stage unavailable: ticket not found.")
        return
    release_msg = await channel.send(
        f"<@{ticket[2]}> <@{ticket[3]}>",
        embed=build_release_stage_embed(ticket[2], ticket[3]),
        view=Step6ReleaseStageView(ticket_id, ticket[2], ticket[3]),
    )
    update_ticket(ticket_id, message_id=release_msg.id)


def resolve_display_media_url(member):
    asset = member.display_avatar
    try:
        if asset.is_animated():
            return str(asset.replace(format="gif", size=256).url)
        return str(asset.replace(format="png", size=256).url)
    except Exception:
        return str(asset.url)


def build_ticket_side_embed(member, label, color, trade_text=""):
    embed = discord.Embed(
        description=f"**{label}**\n```{trade_text or ''}```",
        color=color,
    )
    media_url = resolve_display_media_url(member)
    embed.set_author(name=member.display_name, icon_url=media_url)
    embed.set_thumbnail(url=media_url)
    return embed


def build_unconfirmed_embed(crypto, amount_usd, required_amount, txid=None, confirmations=0, received_amount=None):
    embed = discord.Embed(
        title="??  Transaction Detected",
        description=f"The transaction is currently **unconfirmed** and waiting for {CONFIRMATIONS_REQUIRED} confirmation.",
        color=0xF0B429,
    )
    embed.title = "??  Transaction Detected"
    if txid:
        # Make txid a clickable link to the explorer, but only show the link in the embed, not the raw URL
        if str(crypto).upper() == "LTC":
            tx_url = ltc_tx_link(txid)
        else:
            tx_url = None
        txid_display = short_txid(txid)
        if tx_url:
            txid_value = f"[ `{txid_display}` ]({tx_url})"
        else:
            txid_value = f"`{txid_display}`"
        if received_amount is not None:
            txid_value += f" ({format_asset_amount(received_amount, crypto)} {crypto})"
        embed.add_field(name="Transaction", value=txid_value, inline=False)
    formatted_received = format_asset_amount(received_amount, crypto) if received_amount is not None else "?"
    formatted_required = format_asset_amount(required_amount, crypto) if required_amount is not None else "?"
    embed.add_field(name="Amount Received", value=f"`{formatted_received}` {crypto} (${amount_usd:.2f})", inline=True)
    embed.add_field(name="Required Amount", value=f"`{formatted_required}` {crypto} (${amount_usd:.2f})", inline=True)
    embed.set_footer(text="You will be notified when the transaction is confirmed.")
    return embed


async def panel_recently_posted(channel, lookback_seconds=12):
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=lookback_seconds)
    async for message in channel.history(limit=8):
        if message.created_at < cutoff:
            continue
        if message.author.id != bot.user.id:
            continue
        if not message.embeds:
            continue
        embed = message.embeds[0]
        if embed.description and "AUTO MIDDLEMAN PANEL" in embed.description:
            return True
    return False


class PaymentDetailsView(ui.View):
    def __init__(self, ticket_id=None, wallet_address=None, amount_crypto=None, crypto=None, amount_usd=None):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.wallet_address = wallet_address
        self.amount_crypto = amount_crypto
        self.crypto = crypto
        self.amount_usd = amount_usd

    @ui.button(label="Copy Details", style=discord.ButtonStyle.primary, custom_id="payment_copy_details_btn")
    async def copy_details(self, interaction, button):
        ticket_id = self.ticket_id
        wallet_address = self.wallet_address
        amount_crypto = self.amount_crypto
        crypto = self.crypto
        amount_usd = self.amount_usd

        if ticket_id is None or wallet_address is None or amount_crypto is None or crypto is None or amount_usd is None:
            embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else None
            if embed is not None:
                field_map = {str(field.name).upper(): str(field.value) for field in embed.fields}
                raw_wallet = field_map.get("PAYMENT ADDRESS", "").replace("`", "").strip()
                if raw_wallet:
                    wallet_address = raw_wallet

                raw_usd = field_map.get("USD AMOUNT", "")
                usd_match = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", raw_usd)
                if usd_match:
                    amount_usd = float(usd_match.group(1))

                for name, value in field_map.items():
                    if not name.endswith(" AMOUNT") or name == "USD AMOUNT":
                        continue
                    clean_value = value.replace("*", "")
                    crypto_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s+([A-Z0-9]+)", clean_value)
                    if crypto_match:
                        amount_crypto = float(crypto_match.group(1))
                        crypto = crypto_match.group(2)
                        break

        if ticket_id is None or wallet_address is None or amount_crypto is None or crypto is None or amount_usd is None:
            ticket = get_ticket_by_channel(interaction.channel.id)
            if not ticket or not ticket[7]:
                await interaction.response.send_message("Could not load payment details for this ticket.", ephemeral=True)
                return
            ticket_id = ticket[0]
            wallet_address = ticket[7]
            crypto = ticket[4]
            amount_usd = ticket[5]
            amount_crypto = get_locked_amount_crypto(ticket) if crypto == "LTC" else ticket[5]

        text = (
            f"Deal: #{ticket_id}\n"
            f"Asset: {crypto}\n"
            f"Amount: {format_asset_amount(amount_crypto, crypto)} {crypto} (${float(amount_usd):.2f})\n"
            f"Address: {wallet_address}"
        )
        await interaction.response.send_message(
            f"Copy and send exactly this:\n```text\n{text}\n```",
            ephemeral=True,
        )

class RequestModal(ui.Modal, title="Fill out the format"):
    user_input = ui.TextInput(label="Paste Your Trader's Username or ID", placeholder="e.g.: kookie.py / 693059117761429610")
    you_give = ui.TextInput(label="What are You giving?")
    trader_gives = ui.TextInput(label="What is Your Trader giving?")

    def __init__(self, crypto):
        super().__init__()
        self.crypto = crypto

    async def on_submit(self, interaction):
        try:
            # Acknowledge immediately to avoid Discord modal timeout errors.
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            if not await enforce_lockdown_for_interaction(interaction, "ticket creation"):
                return

            # Advanced: Check blacklist
            if is_user_blacklisted(interaction.user.id):
                await interaction.followup.send("You are restricted from creating tickets. Contact admin for assistance.", ephemeral=True)
                return

            # Advanced: Log ticket creation attempt
            log_action("TICKET_CREATION_ATTEMPT", 0, interaction.user.id, f"crypto={self.crypto}")
            channel = None
            deal_id = f"pending-{int(time.time())}"
            trader_gives = (self.trader_gives.value or "...").strip()
            you_give = (self.you_give.value or "...").strip()
            raw_target = self.user_input.value.strip()

            user = None
            cleaned = raw_target.strip('<@!>')
            if cleaned.isdigit():
                user = interaction.guild.get_member(int(cleaned))
            if user is None:
                lowered = raw_target.lower().lstrip("@")
                user = discord.utils.find(
                    lambda member: member.name.lower() == lowered or member.display_name.lower() == lowered,
                    interaction.guild.members,
                )
            if not user:
                await interaction.followup.send("User not found.", ephemeral=True)
                return

            if is_user_blacklisted(user.id):
                await interaction.followup.send("This user is restricted from participating in trades.", ephemeral=True)
                return

            category = interaction.guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID > 0 else None
            ticket_id = get_next_ticket_id()
            deal_id = generate_deal_id(ticket_id)  # Advanced: Use unique deal ID generator
            if self.crypto == "LTC":
                asset_slug = "ltc"
            elif self.crypto == "USDT_BEP20" or self.crypto == "USDT_ETH" or self.crypto == "USDT":
                asset_slug = "usdt"
            elif self.crypto == "PAYPAL":
                asset_slug = "paypal"
            else:
                asset_slug = str(self.crypto).lower()
            user_slug = re.sub(r"[^a-z0-9_]+", "_", (user.display_name or user.name).lower()).strip("_") or "trade"
            channel = await interaction.guild.create_text_channel(f"{asset_slug}-{user_slug}-{ticket_id}", category=category)

            # Ensure ticket participants + bot can read/send; hide from everyone else.
            bot_member = interaction.guild.me
            if bot_member is None and bot.user:
                try:
                    bot_member = await interaction.guild.fetch_member(bot.user.id)
                except Exception:
                    bot_member = None

            await channel.set_permissions(interaction.user, read_messages=True, send_messages=True)
            await channel.set_permissions(user, read_messages=True, send_messages=True)
            if bot_member is not None:
                await channel.set_permissions(bot_member, read_messages=True, send_messages=True, manage_channels=True)
            await channel.set_permissions(interaction.guild.default_role, read_messages=False)
            for role in interaction.guild.roles:
                if "admin" in role.name.lower():
                    await channel.set_permissions(role, read_messages=True, send_messages=True)


            ticket_view = build_ticket_unified_layout_view(
                bot.user,
                interaction.user,
                user,
                you_give,
                trader_gives,
                ticket_id,
                interaction.user.id,
                user.id,
            )
            await channel.send(f"{interaction.user.mention} {user.mention}")
            await channel.send(view=ticket_view)

            role_view = RoleSelectView(
                ticket_id,
                interaction.user.id,
                user.id,
                self.crypto,
                on_roles_locked=lambda inter, buyer_id, seller_id, crypto: start_role_confirmation_step(
                    inter.channel, ticket_id, buyer_id, seller_id, crypto
                ),
            )
            role_msg = await channel.send(embed=build_role_selection_embed(self.crypto, role_view.roles), view=role_view)

            save_ticket(ticket_id, channel.id, interaction.user.id, user.id, self.crypto, 0, "", "", role_msg.id, f"you_give={you_give} | trader_gives={trader_gives}", deal_id)
            
            # Premium setup is optional; never fail ticket creation for it.
            try:
                await PremiumFlowManager.create_guided_setup(
                    ticket_id, channel, interaction.user.id, user.id, self.crypto
                )
            except Exception as premium_exc:
                print(f"[PREMIUM_SETUP_WARNING] ticket={ticket_id} error={premium_exc}")
            
            # Advanced: Enhanced logging
            await audit(interaction.guild, ticket_id, "ticket_created", f"buyer={interaction.user.id} seller={user.id} crypto={self.crypto} deal_id={deal_id}")
            log_action("TICKET_CREATED", ticket_id, interaction.user.id, f"seller={user.id} crypto={self.crypto} deal_id={deal_id}")
            
            await interaction.followup.send(f"Ticket created: {channel.mention}\nDeal ID: `{deal_id}`", ephemeral=True)
        except Exception as exc:
            print(f"[REQUEST_MODAL_ERROR] {exc}")
            traceback.print_exc()
            if interaction.response.is_done():
                await interaction.followup.send(f"Could not create ticket. {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Could not create ticket. {exc}", ephemeral=True)

class DeleteTicketView(ui.View):
    def __init__(self, ticket_id, user1, user2):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1 = user1
        self.user2 = user2

    @ui.button(label="🧱 • Delete Ticket", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, button):
        if interaction.user.id not in {self.user1, self.user2, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants or the bot admin can delete this ticket.", ephemeral=True)
            return
        await audit(interaction.guild, self.ticket_id, "ticket_deleted", f"deleted_by={interaction.user.id}")
        await interaction.response.send_message("Deleting ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket {self.ticket_id} deleted by {interaction.user.id}")


class CloseTicketView(ui.View):
    def __init__(self, ticket_id, user1, user2):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1 = user1
        self.user2 = user2

    @ui.button(label="??  Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction, button):
        if interaction.user.id not in {self.user1, self.user2, ADMIN_ID}:
            await interaction.response.send_message("Only ticket participants or the bot admin can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Close Ticket Confirmation",
                description="Both participants must click Confirm to close this ticket.",
                color=0xF0B429,
            ),
            view=CloseTicketConfirmView(self.ticket_id, self.user1, self.user2),
            ephemeral=False,
        )


async def start_role_confirmation_step(channel, ticket_id, buyer_id, seller_id, crypto):
    """Step 2 entrypoint, kept separate from role selection logic."""
    await channel.send(
        f"<@{buyer_id}> <@{seller_id}>",
        embed=build_role_confirmation_embed(buyer_id, seller_id),
        view=InfoConfirmView(ticket_id, buyer_id, seller_id, crypto),
    )


class RoleSelectView(ui.View):
    def __init__(
        self,
        ticket_id,
        user1,
        user2,
        crypto,
        on_roles_locked: Optional[Callable[[discord.Interaction, int, int, str], Awaitable[None]]] = None,
    ):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1 = user1
        self.user2 = user2
        self.crypto = crypto
        self.on_roles_locked = on_roles_locked
        self.roles = {}
        self.roles_finalized = False

    def _refresh_role_buttons(self):
        buyer_button = self.children[0]
        seller_button = self.children[1]
        buyer_taken = any(role == "buyer" for role in self.roles.values())
        seller_taken = any(role == "seller" for role in self.roles.values())
        buyer_button.disabled = self.roles_finalized or buyer_taken
        seller_button.disabled = self.roles_finalized or seller_taken
        self.children[2].disabled = self.roles_finalized

    async def sync_role_message(self, interaction):
        self._refresh_role_buttons()
        await interaction.message.edit(embed=build_role_selection_embed(self.crypto, self.roles), view=self)

    async def finalize_roles(self, interaction):
        self.roles_finalized = True
        self._refresh_role_buttons()

        buyer_id = next(user_id for user_id, role in self.roles.items() if role == "buyer")
        seller_id = next(user_id for user_id, role in self.roles.items() if role == "seller")
        update_ticket(self.ticket_id, buyer_id=buyer_id, seller_id=seller_id)
        await interaction.message.edit(embed=build_role_selection_embed(self.crypto, self.roles), view=self)

        if self.on_roles_locked is not None:
            await self.on_roles_locked(interaction, buyer_id, seller_id, self.crypto)

    async def assign_role(self, interaction, role_name, label):
        if self.roles_finalized:
            await interaction.response.send_message("Roles are already locked in for this ticket.", ephemeral=True)
            return
        if interaction.user.id not in [self.user1, self.user2]:
            await interaction.response.send_message("Only ticket participants can choose a role.", ephemeral=True)
            return
        # State guard: role selection must still be the active state
        if not is_ticket_in_state(self.ticket_id, EscrowState.ROLE_SELECTION):
            await interaction.response.send_message("Role selection is no longer active for this ticket.", ephemeral=True)
            return

        previous_role = self.roles.get(interaction.user.id)
        if previous_role and previous_role != role_name:
            await interaction.response.send_message(
                "You already selected your role. Use Reset if you need to change it.",
                ephemeral=True,
            )
            return
        if previous_role == role_name:
            await interaction.response.defer()
            await self.sync_role_message(interaction)
            if len(self.roles) == 2 and len(set(self.roles.values())) == 2:
                await self.finalize_roles(interaction)
            return

        other_user_id = self.user2 if interaction.user.id == self.user1 else self.user1
        other_user_role = self.roles.get(other_user_id)
        if other_user_role == role_name:
            await interaction.response.send_message(
                "That role is already selected by the other participant. Please choose the other role.",
                ephemeral=True,
            )
            return

        self.roles[interaction.user.id] = role_name
        await interaction.response.defer()
        await self.sync_role_message(interaction)

        if len(self.roles) == 2 and len(set(self.roles.values())) == 2:
            await self.finalize_roles(interaction)

    @ui.button(label="Sender", style=discord.ButtonStyle.primary)
    async def buyer(self, interaction, button):
        await self.assign_role(interaction, "buyer", "Sender")

    @ui.button(label="Receiver", style=discord.ButtonStyle.secondary)
    async def seller(self, interaction, button):
        await self.assign_role(interaction, "seller", "Receiver")

    @ui.button(label="Reset", style=discord.ButtonStyle.danger)
    async def reset_roles(self, interaction, button):
        if interaction.user.id not in [self.user1, self.user2]:
            await interaction.response.send_message("Only ticket participants can reset roles.", ephemeral=True)
            return

        self.roles = {}
        self.roles_finalized = False
        for child in self.children:
            child.disabled = False
        await interaction.response.defer()
        await self.sync_role_message(interaction)


class InfoConfirmView(ui.View):
    def __init__(self, ticket_id, sender_id, receiver_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.crypto = crypto
        self.confirms = set()
        self.finalized = False
        self.finalize_lock = asyncio.Lock()

    @ui.button(label="Correct", style=discord.ButtonStyle.success)
    async def correct(self, interaction, button):
        if self.finalized:
            await interaction.response.send_message("This step is already completed.", ephemeral=True)
            return
        if interaction.user.id not in [self.sender_id, self.receiver_id]:
            await interaction.response.send_message("Only ticket participants can confirm this.", ephemeral=True)
            return
        if interaction.user.id in self.confirms:
            await interaction.response.send_message("You already confirmed this.", ephemeral=True)
            return
        # State guard: must be in role selection state
        if not is_ticket_in_state(self.ticket_id, EscrowState.ROLE_SELECTION):
            await interaction.response.send_message("Role confirmation is no longer available for this ticket.", ephemeral=True)
            return

        self.confirms.add(interaction.user.id)
        remaining = 2 - len(self.confirms)
        await interaction.response.send_message(
            f"✅ <@{interaction.user.id}> confirmed roles." + (f" Waiting for the other participant..." if remaining else ""),
            ephemeral=False,
        )

        if len(self.confirms) == 2:
            async with self.finalize_lock:
                if self.finalized:
                    return
                self.finalized = True
                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
                # Advance state: ROLE_SELECTION → ROLE_CONFIRMATION
                advance_ticket_state(
                    self.ticket_id, EscrowState.ROLE_CONFIRMATION, actor_id=interaction.user.id
                )
                await audit(interaction.guild, self.ticket_id, "roles_confirmed", f"buyer={self.sender_id} seller={self.receiver_id}")
                await interaction.channel.send(
                    content=f"<@{self.sender_id}>",
                    embed=build_set_amount_prompt_embed(),
                    view=AmountView(self.ticket_id, self.sender_id, self.crypto),
                )

    @ui.button(label="Incorrect", style=discord.ButtonStyle.danger)
    async def incorrect(self, interaction, button):
        if interaction.user.id not in [self.sender_id, self.receiver_id]:
            await interaction.response.send_message("Only ticket participants can reset this.", ephemeral=True)
            return

        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        role_view = RoleSelectView(
            self.ticket_id,
            ticket[2],
            ticket[3],
            self.crypto,
            on_roles_locked=lambda inter, buyer_id, seller_id, crypto: start_role_confirmation_step(
                inter.channel, self.ticket_id, buyer_id, seller_id, crypto
            ),
        )

        role_message_id = ticket[10]
        role_msg = await interaction.channel.fetch_message(role_message_id)
        await role_msg.edit(embed=build_role_selection_embed(self.crypto, role_view.roles), view=role_view)
        await interaction.response.send_message("Please use the role selection above to choose the correct roles again.", ephemeral=False)
        # Reset state back to ROLE_SELECTION so guards pass again
        TicketStateMachine.force_state(self.ticket_id, EscrowState.ROLE_SELECTION, actor_id=interaction.user.id)


class AmountModal(ui.Modal, title="Set USD Amount"):
    amount = ui.TextInput(label="Please state the amount in USD value", placeholder="e.g.: 435.20")

    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__()
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto

    async def on_submit(self, interaction):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only the Sender can set the amount.", ephemeral=True)
            return
        # State guard: amount can only be set after roles are confirmed
        if not is_ticket_in_state(self.ticket_id, EscrowState.ROLE_CONFIRMATION):
            await interaction.response.send_message("Please confirm roles before setting the amount.", ephemeral=True)
            return
        try:
            amt = float(self.amount.value)
        except Exception:
            await interaction.response.send_message("Invalid amount. Please enter a number (e.g. 435.20).", ephemeral=True)
            return
        if not is_valid_deal_amount(amt):
            await interaction.response.send_message(
                f"Amount must be between ${MIN_DEAL_USD:.2f} and ${MAX_DEAL_USD:.2f}.",
                ephemeral=True,
            )
            return
        desc = "No description provided"
        update_ticket(self.ticket_id, amount=amt, description=desc)
        await audit(interaction.guild, self.ticket_id, "amount_set", f"usd={amt:.2f} description={desc[:120]}")
        embed = build_amount_embed(amt, desc)
        view = ConfirmAmountView(self.ticket_id, self.buyer_id, self.crypto)
        ticket = get_ticket(self.ticket_id)
        await interaction.response.send_message(
            content=f"<@{ticket[2]}> <@{ticket[3]}>",
            embed=embed,
            view=view,
        )

class AmountView(ui.View):
    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto

    @ui.button(label="Set USD Amount", style=discord.ButtonStyle.primary)
    async def enter_amount(self, interaction, button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only the Sender can set the amount.", ephemeral=True)
            return
        # State guard: can only set amount after roles are confirmed
        if not is_ticket_in_state(self.ticket_id, EscrowState.ROLE_CONFIRMATION):
            await interaction.response.send_message("Please confirm roles first.", ephemeral=True)
            return
        await interaction.response.send_modal(AmountModal(self.ticket_id, self.buyer_id, self.crypto))


class ConfirmAmountView(ui.View):
    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto
        self.confirms = set()
        self.finalized = False
        self.finalize_lock = asyncio.Lock()

    @ui.button(label="Correct", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if self.finalized:
            await interaction.response.send_message("This amount is already confirmed.", ephemeral=True)
            return
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id not in [ticket[2], ticket[3]]:
            await interaction.response.send_message("Only deal participants can confirm the amount.", ephemeral=True)
            return
        # State guard: amount can only be confirmed while in ROLE_CONFIRMATION state
        if not is_ticket_in_state(self.ticket_id, EscrowState.ROLE_CONFIRMATION):
            await interaction.response.send_message("Cannot confirm amount — ticket is not in the correct state.", ephemeral=True)
            return
        if interaction.user.id in self.confirms:
            await interaction.response.send_message("You already confirmed the USD amount.", ephemeral=True)
            return

        self.confirms.add(interaction.user.id)
        remaining = 2 - len(self.confirms)
        await interaction.response.send_message(
            f"✅ <@{interaction.user.id}> confirmed the amount." + (" Waiting for the other participant..." if remaining else ""),
            ephemeral=False,
        )
        if len(self.confirms) != 2:
            return

        async with self.finalize_lock:
            if self.finalized:
                return
            self.finalized = True
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            ticket = get_ticket(self.ticket_id)
            if not ticket:
                await interaction.channel.send("Ticket not found.")
                return

            # ── Advance to AMOUNT_SET ──────────────────────────────────────────
            advance_ticket_state(self.ticket_id, EscrowState.AMOUNT_SET, actor_id=interaction.user.id)

            # ── Generate wallet & lock price ───────────────────────────────────
            if self.crypto == "LTC":
                try:
                    wallet = generate_ltc_wallet()
                    wallet_address = wallet["address"]
                    wallet_private = wallet["private"]
                except Exception:
                    wallet_address = "LM6tj5mMHKMMy6tD4e55Kqy4AcgUa1aYtZ"
                    wallet_private = ""
                locked_amount = usd_to_ltc(ltc_deposit_target_usd(ticket[5]))
                update_ticket(
                    self.ticket_id,
                    wallet_address=wallet_address,
                    encrypted_private=wallet_private,
                    locked_amount_crypto=locked_amount,
                )
            else:
                wallet_address = "PENDING_NON_LTC_ADDRESS"
                update_ticket(
                    self.ticket_id,
                    wallet_address=wallet_address,
                    encrypted_private="",
                    locked_amount_crypto=ticket[5],
                )

            # ── Advance to PAYMENT_PENDING and display payment embed ───────────
            advance_ticket_state(self.ticket_id, EscrowState.PAYMENT_PENDING, actor_id=interaction.user.id)
            ticket = get_ticket(self.ticket_id)
            payment_embed = build_payment_embed(ticket, wallet_address)
            payment_msg = await interaction.channel.send(
                f"<@{ticket[2]}> Send the {self.crypto} to the following address.",
                embed=payment_embed,
                view=PaymentDetailsView(
                    ticket_id=self.ticket_id,
                    wallet_address=wallet_address,
                    amount_crypto=get_locked_amount_crypto(ticket),
                    crypto=self.crypto,
                    amount_usd=ticket[5],
                ),
            )
            update_ticket(self.ticket_id, message_id=payment_msg.id)
            await audit(
                interaction.guild,
                self.ticket_id,
                "payment_requested",
                f"wallet={wallet_address} usd={ticket[5]:.2f} locked_crypto={get_locked_amount_crypto(ticket)}",
            )

            # ── Start blockchain monitor ───────────────────────────────────────
            if self.crypto in {"LTC", "USDT_BEP20", "USDT_ETH", "USDT"}:
                monitor_task = bot.loop.create_task(
                    monitor_payment(
                        self.ticket_id, wallet_address, float(ticket[5]), self.crypto, payment_msg,
                    )
                )
                monitor_tasks[self.ticket_id] = monitor_task

            # ── 20-minute timeout guard ────────────────────────────────────────
            bot.loop.create_task(
                payment_timeout_guard(self.ticket_id, interaction.channel, timeout_seconds=1200)
            )

    @ui.button(label="Incorrect", style=discord.ButtonStyle.danger)
    async def incorrect(self, interaction, button):
        ticket = get_ticket(self.ticket_id)
        if not ticket or interaction.user.id not in [ticket[2], ticket[3]]:
            await interaction.response.send_message("Only deal participants can use this.", ephemeral=True)
            return
        # Reset state so the amount can be set again
        TicketStateMachine.force_state(self.ticket_id, EscrowState.ROLE_CONFIRMATION, actor_id=interaction.user.id)
        await interaction.response.send_message("Amount marked incorrect. Please set the amount again.", ephemeral=False)
        await interaction.channel.send(embed=build_set_amount_prompt_embed(), view=AmountView(self.ticket_id, self.buyer_id, self.crypto))


async def payment_timeout_guard(ticket_id: int, channel, timeout_seconds: int = 1200):
    """
    Auto-cancel a ticket that stays in PAYMENT_PENDING for longer than
    *timeout_seconds* (default 20 minutes) with no on-chain TX detected.
    """
    await asyncio.sleep(timeout_seconds)
    # Only act if the ticket is still waiting for payment
    if not is_ticket_in_state(ticket_id, EscrowState.PAYMENT_PENDING):
        return
    TicketStateMachine.force_state(ticket_id, EscrowState.CANCELLED)
    embed = discord.Embed(
        title="⏰ Payment Timeout",
        description=(
            "No transaction was detected within **20 minutes**.\n"
            "This ticket has been automatically closed.\n\n"
            "You may open a new ticket to start again."
        ),
        color=0xED4245,
    )
    embed.set_footer(text=SPARKLES_FOOTER)
    try:
        await channel.send(embed=embed)
        await audit(channel.guild, ticket_id, "payment_timeout", "auto_cancelled_after_20min")
    except Exception:
        pass


async def monitor_payment(ticket_id, address, amount, crypto, msg):
    active_monitors.add(ticket_id)
    last_unconfirmed_conf = None
    last_check_error = None
    payment_status_msg = None  # Track payment status message for live updates
    
    # Advanced: Get dynamic confirmation requirements
    def get_required_confirmations(amount_usd):
        if amount_usd < 100:
            return 1
        elif amount_usd <= 500:
            return 2
        else:
            return 3
    
    required_confs = get_required_confirmations(amount)
    try:
        while True:
            ticket = get_ticket(ticket_id)
            locked_amount = get_locked_amount_crypto(ticket)
            try:
                if crypto == "LTC":
                    required_ltc = locked_amount or usd_to_ltc(amount)
                    if locked_amount is None:
                        update_ticket(ticket_id, locked_amount_crypto=required_ltc)
                    # FIX: detect_ltc_payment returns 5 values
                    paid, conf, txid, received_ltc, is_exact = detect_ltc_payment(address, amount, required_ltc=required_ltc)
                else:
                    paid, conf, txid, received_ltc, is_exact = detect_usdt_payment(address, amount, network=usdt_network_from_asset(crypto))
                    required_ltc = amount
                last_check_error = None
            except Exception as exc:
                error_text = str(exc)[:200]
                if error_text != last_check_error:
                    await audit(msg.guild, ticket_id, "payment_check_error", error_text)
                    last_check_error = error_text
                await asyncio.sleep(PAYMENT_POLL_INTERVAL_SECONDS)
                continue

            if paid:
                # Lock deal when payment is first detected
                if not is_deal_locked(ticket_id):
                    lock_deal(ticket_id)

                if conf < required_confs:
                    # ── TX_DETECTED: transaction seen, confirmations pending ────
                    advance_ticket_state(ticket_id, EscrowState.TX_DETECTED)
                    await audit(msg.guild, ticket_id, "payment_detected", f"txid={txid} confirmations={conf} received={received_ltc:.8f}")

                    if conf != last_unconfirmed_conf:
                        # Only post an update when confirmation count changes
                        detect_embed = discord.Embed(
                            title="⚠️ Transaction Detected (UNCONFIRMED)",
                            description=(
                                f"A transaction has been detected on-chain but has not reached the required confirmations yet.\n\n"
                                f"**Confirmations:** `{conf} / {required_confs}`\n"
                                f"**Received:** `{format_asset_amount(received_ltc, crypto)}` {crypto}\n"
                                f"**TX:** `{short_txid(txid)}`"
                            ),
                            color=0xF59E0B,
                        )
                        detect_embed.set_footer(text=f"{SPARKLES_FOOTER} | Please wait for confirmation")
                        await msg.channel.send(embed=detect_embed)
                        last_unconfirmed_conf = conf

                else:
                    # ── TX_CONFIRMED: required confirmations reached ────────────
                    advance_ticket_state(ticket_id, EscrowState.TX_CONFIRMED)
                    await audit(msg.guild, ticket_id, "payment_confirmed", f"txid={txid} confirmations={conf} received={received_ltc:.8f}")

                    confirmed_embed = discord.Embed(
                        title="✅ Transaction Confirmed",
                        description=(
                            f"🔒 **Funds secured in escrow**\n"
                            f"⚡ `{required_confs}` confirmation(s) completed\n\n"
                            f"**TX:** `{short_txid(txid)}`\n"
                            f"**Received:** `{format_asset_amount(received_ltc, crypto)}` {crypto} (${amount:.2f})\n"
                            f"**Security:** {'Enhanced' if required_confs > 1 else 'Standard'} ({required_confs} conf)"
                        ),
                        color=0x3BA55C,
                    )
                    confirmed_embed.set_footer(text=SPARKLES_FOOTER)
                    await msg.channel.send(embed=confirmed_embed)

                    ticket = get_ticket(ticket_id)
                    if ticket:
                        await audit(msg.guild, ticket_id, "tx_details",
                                    f"buyer={ticket[2]} seller={ticket[3]} amount_usd={amount:.2f} "
                                    f"crypto={crypto} received={received_ltc:.8f} txid={txid} confs={conf}")

                        instructions = discord.Embed(
                            title="🤝 You May Proceed With Your Trade",
                            description=(
                                f"**Step 1 —** <@{ticket[3]}> give the agreed item/service to your trade partner.\n\n"
                                f"**Step 2 —** <@{ticket[2]}> once you have received your item, click "
                                f"**Release** to let your partner claim the {crypto}.\n\n"
                                f"> ⚠️ Do NOT release until you have physically received the item."
                            ),
                            color=0x3BA55C,
                        )
                        instructions.set_footer(text=SPARKLES_FOOTER)
                        try:
                            release_msg = await msg.channel.send(
                                f"<@{ticket[2]}> <@{ticket[3]}>",
                                embed=instructions,
                                view=ReleaseRefundView(ticket_id, crypto),
                            )
                            update_ticket(ticket_id, message_id=release_msg.id)
                            await audit(msg.guild, ticket_id, "release_controls_posted", f"message_id={release_msg.id}")
                        except Exception as exc:
                            await audit(msg.guild, ticket_id, "release_controls_post_failed", str(exc)[:200])
                            fallback_embed = discord.Embed(
                                title=SPARKLES_TITLE,
                                description="**DEPOSIT CONFIRMED — TRADE LIVE**\nRelease controls posted below.",
                                color=0x3BA55C,
                            )
                            fallback_embed.set_footer(text=SPARKLES_FOOTER)
                            fallback_msg = await msg.channel.send(
                                f"<@{ticket[2]}> <@{ticket[3]}>",
                                embed=fallback_embed,
                                view=ReleaseRefundView(ticket_id, crypto),
                            )
                            update_ticket(ticket_id, message_id=fallback_msg.id)
                    return

            await asyncio.sleep(PAYMENT_POLL_INTERVAL_SECONDS)
    finally:
        active_monitors.discard(ticket_id)
        # Advanced: Log monitoring completion
        log_action("PAYMENT_MONITORING_ENDED", ticket_id, 0, f"final_status={get_ticket(ticket_id)[6] if get_ticket(ticket_id) else 'unknown'}")

async def resume_pending_monitors():
    """Advanced: Resume payment monitoring for active deals after bot restart"""
    await bot.wait_until_ready()
    print("[RESUME] Checking for active deals to resume monitoring...")
    
    tickets = get_tickets_by_status(["pending_payment", "unconfirmed"])
    resumed_count = 0
    
    for ticket in tickets:
        ticket_id = ticket[0]
        if ticket_id in active_monitors or not ticket[7] or not ticket[10]:
            continue

        channel = bot.get_channel(ticket[1])
        if channel is None:
            continue

        try:
            msg = await channel.fetch_message(ticket[10])
        except discord.NotFound:
            continue
        except discord.HTTPException:
            continue

        bot.loop.create_task(monitor_payment(ticket_id, ticket[7], ticket[5], ticket[4], msg))
        resumed_count += 1
        
        # Advanced: Restore deal summary if exists
        if ticket_id not in deal_summaries:
            try:
                # Try to find existing summary message in channel
                async for message in channel.history(limit=50):
                    if message.author == bot.user and "Deal Summary" in message.content:
                        deal_summaries[ticket_id] = message
                        break
            except:
                pass  # Summary message not found, will create new one
    
    print(f"[RESUME] Resumed monitoring for {resumed_count} active deals")
    log_action("MONITORING_RESUMED", 0, bot.user.id, f"resumed_count={resumed_count}")

async def cleanup_completed_deals():
    """Advanced: Auto-cleanup completed deals after set time"""
    await bot.wait_until_ready()
    
    while True:
        try:
            await asyncio.sleep(3600)  # Check every hour
            
            # Get completed deals older than 1 hour
            completed_tickets = get_tickets_by_status(["completed", "cancelled"])
            cleanup_count = 0
            
            for ticket in completed_tickets:
                ticket_id = ticket[0]
                
                # Skip if recently completed (within last hour)
                # This would need timestamp field in database
                
                # Clean up tracking data
                if ticket_id in deal_summaries:
                    try:
                        del deal_summaries[ticket_id]
                        cleanup_count += 1
                    except:
                        pass
                
                if ticket_id in active_deal_locks:
                    active_deal_locks.discard(ticket_id)
            
            if cleanup_count > 0:
                print(f"[CLEANUP] Cleaned up {cleanup_count} completed deals")
                log_action("AUTO_CLEANUP", 0, bot.user.id, f"cleaned_count={cleanup_count}")
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[CLEANUP] Error in cleanup loop: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes on error

def handle_critical_error(error_type: str, error_details: str, ticket_id: int = None):
    """Advanced: Enhanced error handling with logging and recovery"""
    error_id = f"ERR-{int(time.time())}-{random.randint(1000, 9999)}"
    
    # Log the error
    print(f"[ERROR] {error_id} | {error_type} | {error_details}")
    
    # Log to database
    if ticket_id:
        log_action("CRITICAL_ERROR", ticket_id, 0, f"{error_type}: {error_details} | {error_id}")
    
    # Send to admin channel
    if LOG_CHANNEL_ID:
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🚨 Critical Error",
                description=f"**Type:** {error_type}\n**Details:** {error_details}\n**Error ID:** `{error_id}`",
                color=0xEF4444
            )
            if ticket_id:
                embed.add_field(name="Ticket", value=f"#{ticket_id}", inline=True)
            embed.set_footer(text="Please check bot logs for more details")
            
            try:
                asyncio.create_task(log_channel.send(f"<@{ADMIN_ID}>", embed=embed))
            except:
                pass  # Admin channel might be unavailable
    
    return error_id

async def health_check():
    """Advanced: System health monitoring"""
    await bot.wait_until_ready()
    
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes
            
            # Check system health
            health_status = {
                'active_monitors': len(active_monitors),
                'active_deals': len(get_tickets_by_status(["pending_payment", "unconfirmed", "paid"])),
                'locked_deals': len(active_deal_locks),
                'blacklisted_users': len(user_blacklist),
                'deal_summaries': len(deal_summaries)
            }
            
            # Log health status
            print(f"[HEALTH] Active: {health_status['active_deals']} | Monitors: {health_status['active_monitors']} | Locked: {health_status['locked_deals']}")
            
            # Check for potential issues
            if health_status['active_monitors'] > health_status['active_deals'] * 2:
                handle_critical_error("MONITOR_LEAK", f"Too many monitors: {health_status['active_monitors']} vs {health_status['active_deals']} deals")
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[HEALTH] Error in health check: {e}")
            await asyncio.sleep(600)  # Wait 10 minutes on error

class ReleaseRefundView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto

    @ui.button(label="✅  Release Funds", style=discord.ButtonStyle.success)
    async def release(self, interaction, button):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        if not await enforce_lockdown_for_interaction(interaction, "release flow"):
            return

        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.followup.send("Ticket not found.", ephemeral=True)
            return

        # ── Security: only buyer or admin may trigger release ─────────────────
        if interaction.user.id != ticket[2] and not is_admin_user(interaction.guild, interaction.user):
            await interaction.followup.send(
                "❌ Only the **Sender** can release funds. If you are the Receiver, please deliver the agreed item first.",
                ephemeral=True,
            )
            log_action("UNAUTHORIZED_RELEASE_ATTEMPT", self.ticket_id, interaction.user.id, "non_buyer_attempted_release")
            return

        # ── Security: state must be TX_CONFIRMED (paid) ───────────────────────
        current_state = get_ticket_state(self.ticket_id)
        if current_state not in (EscrowState.TX_CONFIRMED, EscrowState.RELEASING):
            # Recovery path: re-check blockchain in case monitor missed the confirmation
            recovered = False
            try:
                if ticket[7]:
                    if ticket[4] == "LTC":
                        required_ltc = get_locked_amount_crypto(ticket) or usd_to_ltc(ticket[5])
                        paid, conf, txid, received_ltc, is_exact = detect_ltc_payment(
                            ticket[7], ticket[5], required_ltc=required_ltc
                        )
                    else:
                        paid, conf, txid, received_ltc, is_exact = detect_usdt_payment(
                            ticket[7], ticket[5], network=usdt_network_from_asset(ticket[4])
                        )
                    if paid and conf >= CONFIRMATIONS_REQUIRED:
                        advance_ticket_state(self.ticket_id, EscrowState.TX_CONFIRMED, actor_id=interaction.user.id)
                        await audit(interaction.guild, self.ticket_id, "release_state_recovered",
                                    f"txid={txid} conf={conf} received={received_ltc:.8f}")
                        recovered = True
            except Exception:
                pass

            if not recovered:
                state_meta = get_state_meta(current_state or "unknown")
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="🔒 Release Not Available Yet",
                        description=(
                            f"Release can only be triggered after payment is fully confirmed.\n\n"
                            f"**Current status:** {state_meta['emoji']} {state_meta['label']}\n\n"
                            f"Please wait for the blockchain monitor to confirm your transaction."
                        ),
                        color=0xED4245,
                    ),
                    ephemeral=True,
                )
                return

        # ── Security: prevent double-release ─────────────────────────────────
        if self.ticket_id in withdraw_processing:
            await interaction.followup.send("A withdrawal is already being processed for this ticket.", ephemeral=True)
            return

        # ── Show release warning embed ─────────────────────────────────────────
        warning_embed = discord.Embed(
            title=f"⚠️ Confirm Release — {self.crypto}",
            description=(
                f"You are about to release **{self.crypto}** funds to the Receiver.\n\n"
                f"> **This action cannot be undone.**\n\n"
                f"Only confirm if you have **already received** the agreed item or service."
            ),
            color=0xF59E0B,
        )
        warning_embed.set_footer(text=SPARKLES_FOOTER)
        await audit(interaction.guild, self.ticket_id, "release_initiated", f"buyer={interaction.user.id}")
        log_action("RELEASE_INITIATED", self.ticket_id, interaction.user.id, f"state={current_state}")
        await interaction.followup.send(
            embed=warning_embed,
            view=ReleaseWarningView(self.ticket_id, self.crypto),
            ephemeral=False,
        )

    @ui.button(label="❌  Cancel Trade", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id not in [ticket[2], ticket[3]] and not is_admin_user(interaction.guild, interaction.user):
            await interaction.response.send_message("Only deal participants can cancel.", ephemeral=True)
            return
        log_action("CANCEL_REQUESTED", self.ticket_id, interaction.user.id, "cancel_button_clicked")
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Cancel Trade Confirmation",
                description="Both participants must click Confirm to cancel this trade.",
                color=0xF0B429,
            ),
            view=CancelTradeConfirmView(self.ticket_id, ticket[2], ticket[3]),
            ephemeral=False,
        )



class ReleaseModal(ui.Modal, title="Enter Seller Address"):
    address = ui.TextInput(label="Seller Wallet Address", placeholder="Address")

    def __init__(self, ticket_id, crypto):
        super().__init__()
        self.ticket_id = ticket_id
        self.crypto = crypto

    async def on_submit(self, interaction):
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket data not found. Please contact support.", ephemeral=True)
            return

        if interaction.user.id != ticket[3]:
            await interaction.response.send_message("Only the seller can submit payout address.", ephemeral=True)
            return
        if ticket[6] not in ("paid", "releasing"):
            await interaction.response.send_message("Ticket is not ready for withdrawal yet.", ephemeral=True)
            return

        seller_address = self.address.value.strip()
        if seller_address == ticket[7]:
            await interaction.response.send_message(
                "Payout address cannot be the same as the escrow wallet address.",
                ephemeral=True,
            )
            return
        if self.crypto == "LTC" and not looks_like_ltc_address(seller_address):
            await interaction.response.send_message("That does not look like a valid LTC address.", ephemeral=True)
            return
        if self.crypto != "LTC" and not looks_like_evm_address(seller_address):
            await interaction.response.send_message("That does not look like a valid EVM address.", ephemeral=True)
            return
        update_ticket(self.ticket_id, seller_address=seller_address)
        await audit(interaction.guild, self.ticket_id, "seller_address_submitted", f"seller={interaction.user.id} address={seller_address}")

        embed = discord.Embed(
            title="??  Confirm Address",
            description=f"**Address:** `{seller_address}`\n\nClick \"Confirm\" to send {self.crypto} or \"Back\" to cancel.",
            color=0xF0B429
        )
        await interaction.response.send_message(embed=embed, view=ReleaseConfirmView(self.ticket_id, self.crypto), ephemeral=False)


class ReleaseWarningView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto
        self.created_at = time.time()
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        wait_remaining = 2 - (time.time() - self.created_at)
        if wait_remaining > 0:
            await interaction.response.send_message(
                f"Please wait {wait_remaining:.1f}s before confirming.",
                ephemeral=True,
            )
            return
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id != ticket[2] and not is_admin_user(interaction.guild, interaction.user):
            await interaction.response.send_message("Only the buyer or an admin can confirm this step.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"?  What's Your {self.crypto} Address?",
            description=f"Make sure to paste your correct {self.crypto} address.",
            color=0x16181D,
        )
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=embed, view=SellerAddressEntryView(self.ticket_id, self.crypto), ephemeral=False)

    @ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await interaction.response.send_message("Release cancelled.", ephemeral=True)


class SellerAddressEntryView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto
        if self.children:
            self.children[0].label = "Enter Your LTC Address" if crypto == "LTC" else "Enter Your USDT Address"

    @ui.button(label="Enter Your LTC Address", style=discord.ButtonStyle.primary)
    async def enter_address(self, interaction, button):
        # Open modal immediately to avoid interaction timeout (Unknown interaction).
        # Seller/ticket validation is enforced in ReleaseModal.on_submit.
        try:
            await interaction.response.send_modal(ReleaseModal(self.ticket_id, self.crypto))
        except discord.NotFound:
            return

class ReleaseConfirmView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto
        self.created_at = time.time()
        if self.children:
            self.children[0].label = "(2) Confirm"

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        wait_remaining = 2 - (time.time() - self.created_at)
        if wait_remaining > 0:
            await interaction.response.send_message(
                f"Please wait {wait_remaining:.1f}s before confirming.",
                ephemeral=True,
            )
            return
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id != ticket[3]:  # only seller can withdraw after submitting address
            await interaction.response.send_message("Only seller can confirm withdraw.", ephemeral=True)
            return
        if ticket[6] == "completed":
            await interaction.response.send_message("This ticket has already been completed.", ephemeral=True)
            return
        if ticket[6] == "releasing":
            await interaction.response.send_message("A withdrawal is already being processed for this ticket.", ephemeral=True)
            return
        if ticket[6] != "paid":
            await interaction.response.send_message("Ticket must be in paid status before withdrawal.", ephemeral=True)
            return

        if not ticket[9]:
            await interaction.response.send_message("Seller address is missing. Please enter address again.", ephemeral=True)
            return
        if not ticket[8]:
            await interaction.response.send_message("Escrow key missing for this ticket. Contact admin.", ephemeral=True)
            return

        now = int(time.time())
        last = int(withdraw_cooldowns.get(self.ticket_id, 0))
        remaining = WITHDRAW_CONFIRM_COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {remaining}s before trying withdrawal again.",
                ephemeral=True,
            )
            return
        withdraw_cooldowns[self.ticket_id] = now
        if self.ticket_id in withdraw_processing:
            await interaction.response.send_message(
                "Withdrawal is already being processed for this ticket.",
                ephemeral=True,
            )
            return
        withdraw_processing.add(self.ticket_id)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        # Acknowledge immediately to avoid 3s interaction timeout during API calls.
        await interaction.response.defer()
        sending_embed = discord.Embed(
            title="?  Sending...",
            color=0x16181D,
        )
        await interaction.followup.send(embed=sending_embed)
        update_ticket(self.ticket_id, status="releasing")
        await audit(interaction.guild, self.ticket_id, "withdraw_attempt", f"seller={interaction.user.id} address={ticket[9]}")

        # If payment was forced via /transaction or !fake_tx, skip real blockchain send.
        if has_fake_payment_marker(self.ticket_id):
            fake_txid = f"unconfirmed-{int(time.time())}"
            update_ticket(self.ticket_id, status="completed")
            await audit(
                interaction.guild,
                self.ticket_id,
                "withdraw_success",
                f"txid={fake_txid} address={ticket[9]} unconfirmed=true",
            )
            embed = discord.Embed(
                title="?  Withdrawal Successful",
                description="Use /setprivacy to display your user in `#-completed`",
                color=0x00FF00
            )
            embed.add_field(name="Transaction", value=f"`{fake_txid}`", inline=False)
            amount_sent = usd_to_ltc(ltc_seller_payout_usd(ticket[5])) if self.crypto == "LTC" else seller_payout_usd(ticket[5], self.crypto)
            embed.add_field(name="Amount Sent", value=f"`{format_asset_amount(amount_sent, self.crypto)}` {self.crypto} (${ticket[5]:.2f})", inline=False)
            await post_completed_deal_message(
                interaction.guild,
                self.crypto,
                float(amount_sent),
                float(ticket[5]),
                fake_txid,
            )
            await interaction.followup.send(embed=embed, view=CloseTicketView(self.ticket_id, ticket[2], ticket[3]))
            withdraw_processing.discard(self.ticket_id)
            return

        try:
            if self.crypto == "LTC":
                amount_ltc = usd_to_ltc(ltc_seller_payout_usd(ticket[5]))
                tx = send_ltc(ticket[9], amount_ltc, ticket[8])
            else:
                # Always deduct fee from payout for USDT (BEP-20/ETH)
                payout_usd = seller_payout_usd(ticket[5], self.crypto)
                tx = send_usdt(ticket[9], payout_usd, ticket[8], network=usdt_network_from_asset(self.crypto))

            txid = extract_txid(tx)
            provider_error = tx.get("error") if isinstance(tx, dict) else None

            if provider_error or not txid:
                update_ticket(self.ticket_id, status="paid")
                await audit(interaction.guild, self.ticket_id, "withdraw_failed", str(tx)[:200])
                details = str(provider_error or tx)
                is_rate_limited = "limits reached" in details.lower()
                embed = discord.Embed(
                    title=SPARKLES_TITLE,
                    description=(
                        "**WITHDRAWAL FAILED**\nProvider rate limit reached. Auto-retry queue started; this ticket will retry in the background."
                        if is_rate_limited
                        else "**WITHDRAWAL FAILED**\nFunds were not sent. Please retry or contact admin."
                    ),
                    color=0xE74C3C,
                )
                embed.add_field(name="Provider Response", value=f"`{details[:900]}`", inline=False)
                if isinstance(tx, dict):
                    raw = str(tx)
                    embed.add_field(name="Raw Payload", value=f"`{raw[:900]}`", inline=False)
                embed.set_footer(text=SPARKLES_FOOTER)
                await interaction.followup.send(embed=embed, view=ReleaseConfirmView(self.ticket_id, self.crypto))
                if is_rate_limited and self.ticket_id not in withdraw_retry_tasks:
                    retry_task = bot.loop.create_task(
                        retry_withdrawal(
                            self.ticket_id,
                            self.crypto,
                            interaction.channel.id,
                            interaction.message.id,
                        )
                    )
                    withdraw_retry_tasks[self.ticket_id] = retry_task
                    await interaction.followup.send(
                        "Auto-retry has been queued. The bot will retry withdrawal shortly.",
                        ephemeral=True,
                    )
                withdraw_processing.discard(self.ticket_id)
                return

            update_ticket(self.ticket_id, status="completed")
            await audit(interaction.guild, self.ticket_id, "withdraw_success", f"txid={txid} address={ticket[9]}")
            embed = discord.Embed(
                title="?  Withdrawal Successful",
                description="Use /setprivacy to display your user in `#-completed`",
                color=0x00FF00
            )
            embed.add_field(name="Transaction", value=f"`{txid}`", inline=False)
            amount_sent = usd_to_ltc(ltc_seller_payout_usd(ticket[5])) if self.crypto == "LTC" else seller_payout_usd(ticket[5], self.crypto)
            embed.add_field(name="Amount Sent", value=f"`{format_asset_amount(amount_sent, self.crypto)}` {self.crypto} (${ticket[5]:.2f})", inline=False)
            await post_completed_deal_message(
                interaction.guild,
                self.crypto,
                float(amount_sent),
                float(ticket[5]),
                txid,
            )
            await interaction.followup.send(embed=embed, view=CloseTicketView(self.ticket_id, ticket[2], ticket[3]))
            withdraw_processing.discard(self.ticket_id)
        except Exception as e:
            update_ticket(self.ticket_id, status="paid")
            await audit(interaction.guild, self.ticket_id, "withdraw_exception", str(e)[:200])
            await interaction.followup.send(f"Release failed: {e}", ephemeral=True)
            withdraw_processing.discard(self.ticket_id)

    @ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await interaction.response.send_message("Withdrawal cancelled.", ephemeral=True)

class RequestLTCView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="⚡ Start Trade", style=discord.ButtonStyle.primary, custom_id="panel_request_ltc", emoji="⚡")
    async def ltc(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("LTC"))


class RequestUSDTBEP20View(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💎 Start Trade", style=discord.ButtonStyle.success, custom_id="panel_request_usdt_bep20", emoji="💎")
    async def usdt_bep20(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("USDT_BEP20"))


class RequestUSDTETHView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🔮 Start Trade", style=discord.ButtonStyle.secondary, custom_id="panel_request_usdt_eth", emoji="🔮")
    async def usdt_eth(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("USDT_ETH"))


class DogPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="LTC", style=discord.ButtonStyle.primary, custom_id="dog_panel_request_ltc", row=0)
    async def ltc(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("LTC"))

    @ui.button(label="USDT [BEP-20]", style=discord.ButtonStyle.success, custom_id="dog_panel_request_usdt_bep20", row=0)
    async def usdt_bep20(self, interaction, button):
        if not await guard_auto_mm_interaction(interaction):
            return
        await interaction.response.send_modal(RequestModal("USDT_BEP20"))




def build_commands_overview_lines():
    lines = []
    seen = set()

    for cmd in sorted(bot.commands, key=lambda item: item.qualified_name):
        if getattr(cmd, "hidden", False):
            continue
        name = cmd.qualified_name
        seen.add(name)
        description = str(cmd.help or getattr(cmd, "description", None) or cmd.brief or "No description.").strip()
        if isinstance(cmd, commands.HybridCommand):
            trigger = f"!{name} | /{name}"
        else:
            trigger = f"!{name}"

        aliases = getattr(cmd, "aliases", None) or []
        alias_text = ""
        if aliases:
            alias_text = " (aliases: " + ", ".join(f"!{alias}" for alias in aliases) + ")"

        lines.append(f"{trigger} - {description}{alias_text}")

    try:
        slash_commands = sorted(bot.tree.walk_commands(), key=lambda item: item.qualified_name)
    except Exception:
        slash_commands = []

    for slash_cmd in slash_commands:
        slash_name = getattr(slash_cmd, "qualified_name", slash_cmd.name)
        if slash_name in seen:
            continue
        slash_desc = str(getattr(slash_cmd, "description", "") or "No description.").strip()
        lines.append(f"/{slash_name} - {slash_desc}")

    return lines


def build_commands_overview_pages(lines):
    pages = []
    current_lines = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > 3500 and current_lines:
            pages.append(current_lines)
            current_lines = [line]
            current_len = line_len
            continue
        current_lines.append(line)
        current_len += line_len
    if current_lines:
        pages.append(current_lines)
    return pages


async def send_commands_overview_pages(send_callable, pages):
    for index, page_lines in enumerate(pages, start=1):
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**COMMAND LIST**\n" + "\n".join(page_lines),
            color=0x3498DB,
        )
        embed.set_footer(text=f"{SPARKLES_FOOTER} | Auto-updated ({index}/{len(pages)})")
        await send_callable(embed)


@bot.command(name="commands", aliases=["cmds", "allcmds", "cmdlist"])
async def commands_overview(ctx):
    lines = build_commands_overview_lines()
    if not lines:
        await ctx.send("No commands are currently registered.")
        return
    pages = build_commands_overview_pages(lines)
    await send_commands_overview_pages(lambda embed: ctx.send(embed=embed), pages)


@bot.tree.command(name="commands", description="Show all available bot commands.")
async def commands_overview_slash(interaction: discord.Interaction):
    lines = build_commands_overview_lines()
    if not lines:
        if interaction.response.is_done():
            await interaction.followup.send("No commands are currently registered.", ephemeral=True)
        else:
            await interaction.response.send_message("No commands are currently registered.", ephemeral=True)
        return

    pages = build_commands_overview_pages(lines)
    if not interaction.response.is_done():
        first_embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**COMMAND LIST**\n" + "\n".join(pages[0]),
            color=0x3498DB,
        )
        first_embed.set_footer(text=f"{SPARKLES_FOOTER} | Auto-updated (1/{len(pages)})")
        await interaction.response.send_message(embed=first_embed, ephemeral=True)
        start_index = 2
    else:
        start_index = 1

    for page_index in range(start_index, len(pages) + 1):
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**COMMAND LIST**\n" + "\n".join(pages[page_index - 1]),
            color=0x3498DB,
        )
        embed.set_footer(text=f"{SPARKLES_FOOTER} | Auto-updated ({page_index}/{len(pages)})")
        await interaction.followup.send(embed=embed, ephemeral=True)

async def finalize_fake_confirmation(guild, ticket_id, msg, crypto, wait_seconds):
    try:
        await asyncio.sleep(wait_seconds)
        ticket = get_ticket(ticket_id)
        if not ticket:
            return

        required_amount = get_locked_amount_crypto(ticket) if crypto == "LTC" else float(ticket[5])
        if required_amount is None:
            required_amount = usd_to_ltc(ticket[5]) if crypto == "LTC" else float(ticket[5])
        fake_txid = generate_random_txid()
        update_ticket(ticket_id, status="paid")
        await audit(guild, ticket_id, "fake_payment_confirmed", f"auto_confirm_after_{wait_seconds}s txid={fake_txid}")
        confirmed_embed = build_confirmed_transaction_embed(
            fake_txid,
            required_amount,
            required_amount,
            crypto,
        )
        await msg.channel.send(embed=confirmed_embed)
        release_msg = await msg.channel.send(
            f"<@{ticket[2]}> <@{ticket[3]}>",
            embed=build_release_stage_embed(ticket[2], ticket[3]),
            view=ReleaseRefundView(ticket_id, crypto),
        )
        update_ticket(ticket_id, message_id=release_msg.id)
    finally:
        fake_confirmation_tasks.pop(ticket_id, None)

@bot.hybrid_command(name="transaction", description="Check if transaction is confirmed or not.")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def transaction(ctx):
    if not await enforce_sensitive_cooldown(ctx, "transaction"):
        return
    if not await enforce_lockdown_for_ctx(ctx, "transaction simulation"):
        return
    if not fake_payment_enabled():
        await reply_hybrid(ctx, "Fake payment simulation is disabled. Set `ALLOW_FAKE_PAYMENTS=true` to enable it.", ephemeral=True)
        return
    is_admin = bool(getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator)
    if not ctx.guild or (ctx.author.id != ADMIN_ID and not is_admin):
        await reply_hybrid(ctx, "Only server admins can use this command.", ephemeral=True)
        return

    # Privacy: prefix commands cannot be ephemeral, so force slash usage for private responses.
    if not getattr(ctx, "interaction", None):
        try:
            await ctx.message.delete()
        except Exception:
            pass
        try:
            await ctx.author.send("Use `/transaction` in the server for a private (ephemeral) response.")
        except Exception:
            pass
        return

    ticket = get_ticket_by_channel(ctx.channel.id)
    if not ticket:
        await reply_hybrid(ctx, "Use this command inside a ticket channel.", ephemeral=True)
        return

    try:
        msg = await ctx.channel.fetch_message(ticket[10])  # message_id is index 10
    except:
        await reply_hybrid(ctx, "Ticket payment message not found.", ephemeral=True)
        return

    if not ticket[7] or not ticket[8]:  # wallet_address and encrypted_private
        wallet = generate_ltc_wallet()
        update_ticket(ticket[0], wallet_address=wallet["address"], encrypted_private=wallet["private"])

    required_amount = get_locked_amount_crypto(ticket) if ticket[4] == "LTC" else ticket[5]
    if required_amount is None:
        required_amount = usd_to_ltc(ticket[5]) if ticket[4] == "LTC" else ticket[5]

    fake_txid = generate_random_txid()
    update_ticket(ticket[0], status="paid")
    await audit(
        ctx.guild,
        ticket[0],
        "fake_payment_confirmed",
        f"by={ctx.author.id} txid={fake_txid} immediate=true",
    )
    await ctx.channel.send(
        embed=build_confirmed_transaction_embed(
            fake_txid,
            required_amount,
            required_amount,
            ticket[4],
        )
    )
    release_msg = await ctx.channel.send(
        f"<@{ticket[2]}> <@{ticket[3]}>",
        embed=build_release_stage_embed(ticket[2], ticket[3]),
        view=ReleaseRefundView(ticket[0], ticket[4]),
    )
    update_ticket(ticket[0], message_id=release_msg.id)

    await reply_hybrid(ctx, "Transaction marked confirmed. Release controls are now ready.", ephemeral=True)

@bot.command()
async def fake_tx(ctx, channel_id: int):
    if not await enforce_sensitive_cooldown(ctx, "fake_tx"):
        return
    if not await enforce_lockdown_for_ctx(ctx, "fake transaction"):
        return
    if not fake_payment_enabled():
        await ctx.send("Fake payment simulation is disabled. Set `ALLOW_FAKE_PAYMENTS=true` to enable it.")
        return
    is_admin = bool(getattr(ctx.author, "guild_permissions", None) and ctx.author.guild_permissions.administrator)
    if not ctx.guild or (ctx.author.id != ADMIN_ID and not is_admin):
        await ctx.send("Only server admins can use this command.")
        return

    channel = ctx.guild.get_channel(channel_id)
    if not channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(channel_id)
    if not ticket:
        await ctx.send("Ticket not found.")
        return

    try:
        msg = await channel.fetch_message(ticket[10])  # message_id is index 10
    except:
        await ctx.send("Ticket payment message not found.")
        return

    if not ticket[7] or not ticket[8]:
        wallet = generate_ltc_wallet()
        update_ticket(ticket[0], wallet_address=wallet["address"], encrypted_private=wallet["private"])

    pending_task = fake_confirmation_tasks.get(ticket[0])
    if pending_task and not pending_task.done():
        await ctx.send(f"Ticket {ticket[0]} already has a pending unconfirmed confirmation.")
        return

    wait_seconds = random.randint(10, 15)
    required_amount = get_locked_amount_crypto(ticket) if ticket[4] == "LTC" else ticket[5]
    if required_amount is None:
        required_amount = usd_to_ltc(ticket[5]) if ticket[4] == "LTC" else ticket[5]
    update_ticket(ticket[0], status="unconfirmed")
    await audit(ctx.guild, ticket[0], "fake_payment_unconfirmed", f"by={ctx.author.id}")
    status_msg = await channel.send(
        embed=build_unconfirmed_embed(
            crypto=ticket[4],
            amount_usd=ticket[5],
            required_amount=required_amount,
        )
    )

    fake_confirmation_tasks[ticket[0]] = bot.loop.create_task(
        finalize_fake_confirmation(ctx.guild, ticket[0], status_msg, ticket[4], wait_seconds)
    )
    await ctx.send(f"Ticket {ticket[0]} marked unconfirmed.")


@bot.command(aliases=["repair"])
async def repair_release(ctx, channel_id: int = None):
    if not await enforce_sensitive_cooldown(ctx, "repair_release"):
        return
    if not await enforce_lockdown_for_ctx(ctx, "release repair"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send(f"Only admin ID `{ADMIN_ID}` or server owner can use this command.")
        return

    target_channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
    if not target_channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(target_channel.id)
    if not ticket:
        await ctx.send("No ticket record found for this channel. Use this command inside the ticket channel or pass its channel ID.")
        return

    original_status = str(ticket[6] or "").strip().lower()
    repaired_status = ticket[6]
    if original_status in ("pending_payment", "unconfirmed"):
        update_ticket(ticket[0], status="paid")
        repaired_status = "paid"
        await audit(ctx.guild, ticket[0], "release_repaired_status", f"by={ctx.author.id} from={original_status} to=paid")

    embed = discord.Embed(
        title=SPARKLES_TITLE,
        description=(
            f"**RELEASE FLOW REPAIRED**\nRelease controls have been restored for this ticket.\n\n"
            f"Buyer: <@{ticket[2]}>\n"
            f"Seller: <@{ticket[3]}>\n"
            f"Crypto: {ticket[4]}\n"
            f"Status: {repaired_status}"
        ),
        color=0x2ECC71,
    )
    embed.set_footer(text=SPARKLES_FOOTER)

    repaired_msg = await target_channel.send(
        f"<@{ticket[2]}> <@{ticket[3]}>",
        embed=embed,
        view=ReleaseRefundView(ticket[0], ticket[4]),
    )
    update_ticket(ticket[0], message_id=repaired_msg.id)
    await audit(ctx.guild, ticket[0], "release_repaired", f"by={ctx.author.id}")
    await ctx.send(f"Release flow repaired in {target_channel.mention}. Use the NEW release message only.")


@bot.command()
async def emergency_recover(ctx, channel_id: int = None):
    if not await enforce_sensitive_cooldown(ctx, "emergency_recover"):
        return
    if not await enforce_lockdown_for_ctx(ctx, "emergency recovery package"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    target_channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
    if not target_channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(target_channel.id)
    if not ticket:
        await ctx.send("No ticket record found for this channel.")
        return

    decrypted_key = None
    escrow_address = ticket[7]
    if ticket[8]:
        try:
            decrypted_key = decrypt_key(ticket[8])
            if not escrow_address:
                escrow_address = private_hex_to_ltc_address(decrypted_key)
        except Exception as exc:
            decrypted_key = f"DECRYPTION_FAILED: {exc}"

    recovery_embed = discord.Embed(
        title=SPARKLES_TITLE,
        description="**EMERGENCY RECOVERY PACKAGE**\nHighly sensitive recovery details for this ticket.",
        color=0xE67E22,
    )
    recovery_embed.add_field(name="Ticket ID", value=str(ticket[0]), inline=True)
    recovery_embed.add_field(name="Deal ID", value=str(ticket[12] or "n/a"), inline=True)
    recovery_embed.add_field(name="Status", value=str(ticket[6]), inline=True)
    recovery_embed.add_field(name="Escrow Address", value=str(escrow_address or "n/a"), inline=False)
    recovery_embed.add_field(name="Seller Address", value=str(ticket[9] or "n/a"), inline=False)
    recovery_embed.add_field(name="Amount", value=f"${ticket[5]:.2f} {ticket[4]}", inline=True)
    if decrypted_key:
        recovery_embed.add_field(name="Decrypted Escrow Private Key", value=f"`{str(decrypted_key)[:1000]}`", inline=False)

    try:
        await ctx.author.send(embed=recovery_embed)
        await audit(ctx.guild, ticket[0], "emergency_recovery_requested", f"by={ctx.author.id}")
        await ctx.send("Emergency recovery package sent to your DM. Keep it secret.")
    except discord.Forbidden:
        await ctx.send("I could not DM you. Enable DMs and retry.")


@bot.command(aliases=["forcer"])
async def force_release(ctx, channel_id: int = None, seller_address: str = None):
    if not await enforce_sensitive_cooldown(ctx, "force_release"):
        return
    if not await enforce_lockdown_for_ctx(ctx, "force release"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    target_channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
    if not target_channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(target_channel.id)
    if not ticket:
        await ctx.send("No ticket record found for this channel.")
        return

    if ticket[6] in ("completed", "cancelled"):
        await ctx.send(f"Ticket {ticket[0]} is already {ticket[6]}.")
        return

    payout_address = (seller_address or ticket[9] or "").strip()
    if not payout_address:
        await ctx.send("Seller payout address is missing. Use: `!force_release [channel_id] <seller_address>`")
        return

    if payout_address == (ticket[7] or ""):
        await ctx.send("Payout address cannot be the same as escrow wallet address.")
        return

    if ticket[4] == "LTC" and not looks_like_ltc_address(payout_address):
        await ctx.send("That does not look like a valid LTC address.")
        return
    if ticket[4] != "LTC" and not looks_like_evm_address(payout_address):
        await ctx.send("That does not look like a valid EVM address.")
        return

    if not ticket[8] and not has_fake_payment_marker(ticket[0]):
        await ctx.send("Escrow key is missing for this ticket. Use emergency recovery flow.")
        return

    if payout_address != (ticket[9] or ""):
        update_ticket(ticket[0], seller_address=payout_address)

    update_ticket(ticket[0], status="releasing")
    await audit(ctx.guild, ticket[0], "force_release_started", f"by={ctx.author.id} address={payout_address}")

    if has_fake_payment_marker(ticket[0]):
        fake_txid = f"forced-unconfirmed-{int(time.time())}"
        update_ticket(ticket[0], status="completed")
        await audit(ctx.guild, ticket[0], "force_release_success", f"txid={fake_txid} unconfirmed=true")

        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**FORCE RELEASE SUCCESSFUL**\nFunds were marked as released (unconfirmed ticket).",
            color=0x10B981,
        )
        embed.add_field(name="Ticket", value=f"`#{ticket[0]}`", inline=True)
        embed.add_field(name="Seller", value=f"<@{ticket[3]}>", inline=True)
        embed.add_field(name="Transaction", value=f"`{fake_txid}`", inline=False)
        embed.add_field(name="Payout Address", value=f"`{payout_address}`", inline=False)
        embed.set_footer(text=f"{SPARKLES_FOOTER} | Admin force release")
        await target_channel.send(embed=embed)
        if target_channel.id != ctx.channel.id:
            await ctx.send(f"Force release completed in {target_channel.mention}.")
        return

    try:
        if ticket[4] == "LTC":
            amount_ltc = usd_to_ltc(ltc_seller_payout_usd(ticket[5]))
            tx = send_ltc(payout_address, amount_ltc, ticket[8])
        else:
            tx = send_usdt(payout_address, seller_payout_usd(ticket[5], ticket[4]), ticket[8], network=usdt_network_from_asset(ticket[4]))

        txid = extract_txid(tx)
        provider_error = tx.get("error") if isinstance(tx, dict) else None
        if provider_error or not txid:
            update_ticket(ticket[0], status="paid")
            await audit(ctx.guild, ticket[0], "force_release_failed", str(tx)[:200])
            await ctx.send(f"Force release failed: `{str(provider_error or tx)[:900]}`")
            return

        update_ticket(ticket[0], status="completed")
        await audit(ctx.guild, ticket[0], "force_release_success", f"txid={txid} address={payout_address}")

        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**FORCE RELEASE SUCCESSFUL**\nFunds were sent to seller payout address.",
            color=0x10B981,
        )
        embed.add_field(name="Ticket", value=f"`#{ticket[0]}`", inline=True)
        embed.add_field(name="Seller", value=f"<@{ticket[3]}>", inline=True)
        embed.add_field(name="Transaction", value=f"`{txid}`", inline=False)
        embed.add_field(name="Payout Address", value=f"`{payout_address}`", inline=False)
        if ticket[4] == "LTC":
            embed.add_field(name="Explorer", value=ltc_tx_link(txid), inline=False)
        embed.set_footer(text=f"{SPARKLES_FOOTER} | Admin force release")
        await target_channel.send(embed=embed)
        if target_channel.id != ctx.channel.id:
            await ctx.send(f"Force release completed in {target_channel.mention}.")
    except Exception as exc:
        update_ticket(ticket[0], status="paid")
        await audit(ctx.guild, ticket[0], "force_release_exception", str(exc)[:200])
        await ctx.send(f"Force release exception: `{str(exc)[:900]}`")


@bot.command()
async def ticket_audit(ctx, channel_id: int = None):
    if not await enforce_sensitive_cooldown(ctx, "ticket_audit"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    target_channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
    if not target_channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(target_channel.id)
    if not ticket:
        await ctx.send("No ticket record found for this channel.")
        return

    events = get_ticket_events(ticket[0], limit=10)
    if not events:
        await ctx.send("No audit events found for this ticket.")
        return

    chain_ok, bad_index = verify_ticket_audit_chain(ticket[0])
    chain_status = "INTACT" if chain_ok else f"FAILED_AT_EVENT_{bad_index}"
    lines = [f"{created_at} | {event} | {details}" for event, details, created_at in events]
    embed = discord.Embed(
        title=SPARKLES_TITLE,
        description=f"**TICKET AUDIT #{ticket[0]}**\nChain: `{chain_status}`\n" + "\n".join(lines[:10]),
        color=0x5865F2,
    )
    embed.set_footer(text=SPARKLES_FOOTER)
    await ctx.send(embed=embed)


@bot.command(aliases=["dealproof"])
async def generate_proof(ctx, channel_id: int = None):
    if not await enforce_sensitive_cooldown(ctx, "generate_proof"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    target_channel = ctx.guild.get_channel(channel_id) if channel_id else ctx.channel
    if not target_channel:
        await ctx.send("Channel not found.")
        return

    ticket = get_ticket_by_channel(target_channel.id)
    if not ticket:
        await ctx.send("No ticket record found for this channel.")
        return

    random_txid = deal_related_txid(ticket)
    completed_at = int(time.time())

    proof_embed = discord.Embed(
        title="Deal Proof",
        description=f"**${ticket[5]:.2f} {ticket[4]}**\nThis deal was completed through {SPARKLES_TITLE}.",
        color=0x10B981,
    )
    proof_embed.add_field(name="Ticket", value=f"`#{ticket[0]}`", inline=True)
    proof_embed.add_field(name="Buyer", value=f"<@{ticket[2]}>", inline=True)
    proof_embed.add_field(name="Seller", value=f"<@{ticket[3]}>", inline=True)
    proof_embed.add_field(name="Transaction ID", value=f"`{random_txid}`", inline=False)
    proof_embed.set_footer(text=f"Proof generated by {SPARKLES_FOOTER}")

    await target_channel.send(embed=proof_embed)
    await audit(ctx.guild, ticket[0], "proof_generated", f"by={ctx.author.id} txid={random_txid}")
    await ctx.send(f"Proof generated in {target_channel.mention}.")


@bot.command(name="proof")
async def proof(ctx, *parts):
    # DEBUG LOGGING: Print every time proof is called
    print(f"[DEBUG] proof command called by user={getattr(ctx.author, 'id', None)} in channel={getattr(ctx.channel, 'id', None)} with parts={parts}")
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    ticket = get_ticket_by_channel(ctx.channel.id)

    if not parts:
        amount_value = float(random.randint(1, 67))
        full_input = f"{int(amount_value)} dollars"
        txid = deal_related_txid(ticket)
    else:
        full_input = " ".join(parts).strip()
        amount_match = re.search(r"\d+(?:[\.,]\d+)?", full_input)
        if not amount_match:
            await ctx.send("Invalid amount. Example: `!proof 23 dollars dbcf54932...1f8f483b8`")
            return

        amount_token = amount_match.group(0)
        trailing_text = full_input[amount_match.end():].strip()
        txid = re.sub(r"^(dollars?|usd|\$)\s*", "", trailing_text, flags=re.IGNORECASE).strip()

        try:
            amount_value = float(amount_token.replace(",", ""))
            if amount_value <= 0:
                raise ValueError("Amount must be greater than zero")
        except Exception:
            await ctx.send("Invalid amount. Example: `!proof 23 dollars dbcf54932...1f8f483b8`")
            return

    final_txid = sanitize_txid_text(txid)
    tx_url = None
    if final_txid.lower().startswith("http://") or final_txid.lower().startswith("https://"):
        tx_url = final_txid.split()[0]
        cleaned_path = tx_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        tx_path_match = re.search(r"/tx(?:/[A-Za-z0-9_-]+)?/([A-Fa-f0-9]{64})$", cleaned_path)
        if tx_path_match:
            final_txid = tx_path_match.group(1)
        else:
            last_segment = cleaned_path.rsplit("/", 1)[-1]
            if re.fullmatch(r"[A-Fa-f0-9]{64}", last_segment or ""):
                final_txid = last_segment
    final_txid = final_txid.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
    # Detect asset type (LTC or USDT)
    asset = "LTC"
    asset_label_display = "LTC"
    tx_link_func = ltc_tx_link
    color = 0x111827
    if "usdt" in full_input.lower() or "bep" in full_input.lower() or "bsc" in full_input.lower():
        asset = "USDT_BEP20"
        asset_label_display = "USDT [BEP-20]"
        tx_link_func = lambda txid: f"https://bscscan.com/tx/{txid}"
        color = 0x10B981

    if asset == "LTC":
        # Always use a real LTC transaction, even if not exact
        try:
            price_resp = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd")
            price_json = price_resp.json() if price_resp.ok else {}
            ltc_price = price_json.get("litecoin", {}).get("usd")
            # Fallback to CryptoCompare if CoinGecko fails
            if not ltc_price:
                try:
                    cc_resp = requests.get("https://min-api.cryptocompare.com/data/price?fsym=LTC&tsyms=USD")
                    cc_json = cc_resp.json() if cc_resp.ok else {}
                    ltc_price = cc_json.get("USD")
                except Exception:
                    ltc_price = None
            if not ltc_price:
                ltc_price = 1.0  # fallback to 1 if all fails
            ltc_amount = amount_value / ltc_price if ltc_price else amount_value
            # Get recent LTC transactions (BlockCypher API)
            api_url = f"https://api.blockcypher.com/v1/ltc/main/txs"
            txs_resp = requests.get(api_url)
            txs_json = txs_resp.json() if txs_resp.ok else []
            # BlockCypher sometimes returns a list, sometimes a dict with 'txs'
            if isinstance(txs_json, list):
                txs = txs_json
            elif isinstance(txs_json, dict):
                txs = txs_json.get("txs", [])
            else:
                txs = []
            found = None
            min_diff = float('inf')
            for tx in txs:
                total_out = sum(out.get("value", 0) for out in tx.get("outputs", [])) / 1e8
                usd_value = total_out * ltc_price
                diff = abs(usd_value - amount_value)
                if diff < min_diff:
                    min_diff = diff
                    found = tx
            if found:
                total_out = sum(out.get("value", 0) for out in found.get("outputs", [])) / 1e8
                amount_crypto = total_out
                final_txid = found["hash"]
            else:
                amount_crypto = ltc_amount
                # Always generate a random txid if not found
                final_txid = deal_related_txid(ticket) if not final_txid or len(final_txid) < 16 else final_txid
        except Exception as e:
            amount_crypto = amount_value
            final_txid = deal_related_txid(ticket) if not final_txid or len(final_txid) < 16 else final_txid
        proof_color = 0x111827
        explorer_func = ltc_tx_link
        asset_label_display = "LTC"
        emoji = "LTC"
    else:
        amount_crypto = amount_value
        # Always generate a random txid if not provided
        if not final_txid or len(final_txid) < 16:
            final_txid = deal_related_txid(ticket)
        proof_color = 0x10B981
        explorer_func = lambda txid: f"https://bscscan.com/tx/{txid}"
        asset_label_display = "USDT [BEP-20]"
        emoji = "USDT"

    proof_embed = discord.Embed(
        title=f"{emoji} - Trade Completed",
        description=f"**{amount_crypto:.8f} {asset_label_display} (${amount_value:.2f} USD)**",
        color=proof_color,
    )
    proof_embed.add_field(name="**Sender**", value="`Anonymous`", inline=True)
    proof_embed.add_field(name="**Receiver**", value="`Anonymous`", inline=True)

    if final_txid and len(final_txid) > 16:
        tx_display = f"{final_txid[:8]}...{final_txid[-8:]}"
    else:
        tx_display = final_txid
    tx_field_value = tx_display
    tx_target_url = None
    # Always provide a link if possible
    if final_txid and (re.fullmatch(r"[A-Fa-f0-9]{64}", final_txid) or len(final_txid) >= 16):
        tx_target_url = explorer_func(final_txid)
        tx_field_value = f"[{tx_display}]({tx_target_url})"
    elif tx_url:
        tx_target_url = tx_url
        tx_field_value = f"[{tx_display}]({tx_target_url})"
    proof_embed.add_field(name="**Transaction ID**", value=tx_field_value, inline=False)

    target_channel = get_completed_deals_channel(ctx.guild) or ctx.channel
    proof_view = None
    if tx_target_url:
        proof_view = ui.View(timeout=None)
        proof_view.add_item(ui.Button(label="View Payment", style=discord.ButtonStyle.link, url=tx_target_url))

    # Only post once, no duplicates, no fallback
    try:
        await target_channel.send(embed=proof_embed, view=proof_view, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        # Log or notify only the command invoker, but do not send a second proof
        await ctx.send(f"Failed to send proof message: `{str(exc)[:300]}`")


@bot.command(name="admin_dashboard", aliases=["admin", "dashboard"])
async def admin_dashboard(ctx):
    """Advanced: Admin dashboard with comprehensive deal management"""
    if not await enforce_sensitive_cooldown(ctx, "admin_dashboard"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return
    
    # Get system statistics
    active_tickets = get_tickets_by_status(["pending_payment", "unconfirmed", "paid"])
    disputed_tickets = get_tickets_by_status(["disputed"])
    completed_today = 0  # Would need database query for this
    
    # Create main dashboard embed
    embed = discord.Embed(
        title="🛡️ Admin Dashboard",
        description="Advanced escrow system management",
        color=0x3B82F6
    )
    
    embed.add_field(
        name="📊 System Statistics",
        value=f"Active Deals: {len(active_tickets)}\nDisputed: {len(disputed_tickets)}\nBlacklisted: {len(user_blacklist)}\nLocked Deals: {len(active_deal_locks)}",
        inline=True
    )
    
    embed.add_field(
        name="🔍 Quick Actions",
        value=f"`!active_deals` - View active deals\n`!blacklist_add` - Add user to blacklist\n`!force_release` - Force release funds\n`!force_cancel` - Force cancel deal",
        inline=True
    )
    
    embed.add_field(
        name="⚡ Recent Activity",
        value=f"Payment monitors: {len(active_monitors)}\nDeal summaries: {len(deal_summaries)}\nSystem uptime: Online",
        inline=True
    )
    
    embed.set_footer(text="Dog Auto Middleman Admin Panel")
    embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/814749431716843540/814749431716843543/Dog.png")
    
    await ctx.send(embed=embed)

@bot.command(name="active_deals", aliases=["deals", "active"])
async def active_deals(ctx):
    """Advanced: View all active deals with detailed information"""
    if not await enforce_sensitive_cooldown(ctx, "active_deals"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Admin access required.")
        return
    
    active_tickets = get_tickets_by_status(["pending_payment", "unconfirmed", "paid"])
    
    if not active_tickets:
        embed = discord.Embed(
            title="📋 Active Deals",
            description="No active deals found.",
            color=0x10B981
        )
        await ctx.send(embed=embed)
        return
    
    # Create paginated deal list (show max 10 deals per message)
    for i in range(0, len(active_tickets), 10):
        tickets_chunk = active_tickets[i:i+10]
        
        embed = discord.Embed(
            title=f"📋 Active Deals ({i+1}-{min(i+10, len(active_tickets))})",
            description=f"Showing {len(tickets_chunk)} active deals",
            color=0x3B82F6
        )
        
        for ticket in tickets_chunk:
            ticket_id = ticket[0]
            buyer_id = ticket[2]
            seller_id = ticket[3]
            crypto = ticket[4]
            amount = ticket[5]
            status = ticket[6] or "unknown"
            deal_id = ticket[11] if len(ticket) > 11 else f"DM-{ticket_id:04d}"
            
            # Get status color and emoji
            status_emoji = {
                'pending_payment': '⏳',
                'unconfirmed': '🔍',
                'paid': '✅',
                'disputed': '⚠️'
            }.get(status, '❓')
            
            status_color = STATUS_COLORS.get(status, 0x6B7280)
            
            embed.add_field(
                name=f"{status_emoji} Deal #{ticket_id}",
                value=f"**ID:** `{deal_id}`\n**Amount:** ${amount:.2f} {crypto}\n**Buyer:** <@{buyer_id}>\n**Seller:** <@{seller_id}>\n**Status:** {status.title()}\n**Locked:** {'Yes' if is_deal_locked(ticket_id) else 'No'}",
                inline=False
            )
        
        embed.set_footer(text=f"Page {i//10 + 1} | Total: {len(active_tickets)} deals")
        await ctx.send(embed=embed)

@bot.command(name="blacklist_add", aliases=["blacklist", "bl_add"])
async def blacklist_add(ctx, user: discord.User, *, reason: str = ""):
    """Advanced: Add user to blacklist"""
    if not await enforce_sensitive_cooldown(ctx, "blacklist_add"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Admin access required.")
        return
    
    if is_user_blacklisted(user.id):
        await ctx.send(f"{user.mention} is already blacklisted.")
        return
    
    add_to_blacklist(user.id, reason)
    
    embed = discord.Embed(
        title="🚫 User Blacklisted",
        description=f"{user.mention} has been added to the blacklist.",
        color=0xEF4444
    )
    embed.add_field(name="User ID", value=str(user.id), inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=True)
    embed.set_footer(text=f"Added by {ctx.author.mention}")
    
    await ctx.send(embed=embed)
    
    # Log the action
    log_action("BLACKLIST_ADD_MANUAL", 0, ctx.author.id, f"user={user.id} reason={reason}")

@bot.command(name="blacklist_remove", aliases=["bl_remove", "unblacklist"])
async def blacklist_remove(ctx, user: discord.User):
    """Advanced: Remove user from blacklist"""
    if not await enforce_sensitive_cooldown(ctx, "blacklist_remove"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Admin access required.")
        return
    
    if not is_user_blacklisted(user.id):
        await ctx.send(f"{user.mention} is not blacklisted.")
        return
    
    user_blacklist.discard(user.id)
    log_action("BLACKLIST_REMOVE", 0, ctx.author.id, f"user={user.id}")
    
    embed = discord.Embed(
        title="✅ User Removed from Blacklist",
        description=f"{user.mention} has been removed from the blacklist.",
        color=0x10B981
    )
    embed.set_footer(text=f"Removed by {ctx.author.mention}")
    
    await ctx.send(embed=embed)


@bot.command(name="force_cancel", aliases=["fc"])
async def force_cancel(ctx, ticket_id: int, *, reason: str = "Admin cancellation"):
    """Advanced: Force cancel a ticket"""
    if not await enforce_sensitive_cooldown(ctx, "force_cancel"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Admin access required.")
        return
    
    ticket = get_ticket(ticket_id)
    if not ticket:
        await ctx.send(f"Ticket #{ticket_id} not found.")
        return
    
    # Cancel the ticket
    update_ticket(ticket_id, status="cancelled")
    unlock_deal(ticket_id)  # Unlock if it was locked
    
    log_action("FORCE_CANCEL_MANUAL", ticket_id, ctx.author.id, f"reason={reason}")
    
    embed = discord.Embed(
        title="❌ Force Cancel Completed",
        description=f"Ticket #{ticket_id} has been cancelled.",
        color=0xEF4444
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Deal Details", value=f"Buyer: <@{ticket[2]}>\nSeller: <@{ticket[3]}>\nAmount: ${ticket[5]:.2f} {ticket[4]}", inline=False)
    embed.set_footer(text=f"Cancelled by {ctx.author.mention}")
    
    await ctx.send(embed=embed)

@bot.command()
async def quota(ctx):
    if not await enforce_sensitive_cooldown(ctx, "quota"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    if not BLOCKCYPHER_TOKEN:
        await ctx.send("BLOCKCYPHER_TOKEN is not configured.")
        return

    try:
        resp = requests.get(
            f"https://api.blockcypher.com/v1/tokens/{BLOCKCYPHER_TOKEN}",
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        await ctx.send(f"Failed to fetch quota: {exc}")
        return

    if resp.status_code >= 400 or not isinstance(data, dict):
        await ctx.send(f"Quota lookup failed: {str(data)[:1000]}")
        return

    limits = data.get("limits", {}) if isinstance(data.get("limits"), dict) else {}
    hits = data.get("hits", {}) if isinstance(data.get("hits"), dict) else {}
    lines = [
        f"Token: `{data.get('token', 'unknown')}`",
        f"Hourly: `{hits.get('api/hour', 'n/a')}` / `{limits.get('api/hour', 'n/a')}`",
        f"Daily: `{hits.get('api/day', 'n/a')}` / `{limits.get('api/day', 'n/a')}`",
        f"Per-second: `{hits.get('api/second', 'n/a')}` / `{limits.get('api/second', 'n/a')}`",
    ]
    embed = discord.Embed(
        title=SPARKLES_TITLE,
        description="**BLOCKCYPHER QUOTA**\n" + "\n".join(lines),
        color=0x3498DB,
    )
    embed.set_footer(text=SPARKLES_FOOTER)
    await ctx.send(embed=embed)


@bot.command(name="backup_now", aliases=["backupdb", "dbbackup"])
async def backup_now(ctx):
    if not await enforce_sensitive_cooldown(ctx, "backup_now"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    try:
        path = create_db_backup()
        await ctx.send(f"Database backup created: `{path}`")
    except Exception as exc:
        await ctx.send(f"Database backup failed: `{str(exc)[:900]}`")


@bot.command(name="backup_export", aliases=["securebackup", "backupenc"])
async def backup_export(ctx):
    if not await enforce_sensitive_cooldown(ctx, "backup_export"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    try:
        result = create_encrypted_backup_export()
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**ENCRYPTED BACKUP EXPORT CREATED**\nStore this file in offsite storage.",
            color=0x10B981,
        )
        embed.add_field(name="Backup File", value=f"`{result.get('backup_path')}`", inline=False)
        embed.add_field(name="Encrypted Export", value=f"`{result.get('export_path')}`", inline=False)
        embed.add_field(name="SHA256 (plaintext)", value=f"`{result.get('sha256')}`", inline=False)
        embed.set_footer(text=SPARKLES_FOOTER)
        await ctx.send(embed=embed)
    except Exception as exc:
        await ctx.send(f"Encrypted backup export failed: `{str(exc)[:900]}`")


@bot.command(name="security_status", aliases=["secstatus", "dbstatus"])
async def security_status(ctx):
    if not await enforce_sensitive_cooldown(ctx, "security_status"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    try:
        snapshot = database_safety_snapshot()
        age = snapshot.get("last_backup_age_seconds")
        age_text = "never" if age is None else f"{age}s ago"
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**SECURITY STATUS**\nDatabase and key safety snapshot.",
            color=0x10B981,
        )
        embed.add_field(name="DB Exists", value=str(snapshot.get("db_exists")), inline=True)
        embed.add_field(name="DB Size (bytes)", value=str(snapshot.get("db_size_bytes")), inline=True)
        embed.add_field(name="Backup Count", value=str(snapshot.get("backup_count")), inline=True)
        embed.add_field(name="Last Backup", value=age_text, inline=True)
        embed.add_field(name="Key Fingerprint", value="OK" if snapshot.get("key_fingerprint_ok") else "MISMATCH", inline=True)
        freshness_ok = age is not None and age <= max(BACKUP_ALERT_MAX_AGE_MINUTES, 1) * 60
        embed.add_field(name="Backup Freshness", value="OK" if freshness_ok else "STALE", inline=True)
        embed.add_field(name="Startup Max Backup Age", value=f"{BACKUP_STARTUP_MAX_AGE_MINUTES} min", inline=True)
        embed.add_field(name="DB Path", value=f"`{snapshot.get('db_path')}`", inline=False)
        embed.add_field(name="Backup Dir", value=f"`{snapshot.get('backup_dir')}`", inline=False)
        embed.set_footer(text=SPARKLES_FOOTER)
        await ctx.send(embed=embed)
    except Exception as exc:
        await ctx.send(f"Security status check failed: `{str(exc)[:900]}`")

@bot.event
async def on_raw_reaction_add(payload):
    # Handle reaction-based confirmations (legacy compatibility)
    pass

@bot.listen("on_interaction")
async def on_interaction(interaction: discord.Interaction):
    """Advanced: Handle button interactions for new features"""
    if not interaction.data or not interaction.data.get("custom_id"):
        return
    
    custom_id = interaction.data.get("custom_id")
    user_id = interaction.user.id
    
    # Handle copy buttons
    if custom_id.startswith("copy_"):
        await handle_copy_button(interaction)
    
    # Handle deal control buttons
    elif custom_id.startswith("release_"):
        await handle_release_button(interaction)
    
    elif custom_id.startswith("dispute_"):
        await handle_dispute_button(interaction)
    
    elif custom_id.startswith("cancel_"):
        await handle_cancel_button(interaction)
    
    elif custom_id.startswith("copy_address_"):
        await handle_copy_address_button(interaction)
    
    elif custom_id.startswith("copy_amount_"):
        await handle_copy_amount_button(interaction)

    # Handle panel request buttons created via LayoutView sections
    elif custom_id == "panel_request_ltc":
        if not await guard_auto_mm_interaction(interaction):
            return
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Please click the button again.", ephemeral=True)
                return
            await interaction.response.send_modal(RequestModal("LTC"))
        except Exception as exc:
            print(f"[PANEL_LTC_MODAL_ERROR] {exc}")
            if interaction.response.is_done():
                await interaction.followup.send("Could not open LTC request form.", ephemeral=True)
            else:
                await interaction.response.send_message("Could not open LTC request form.", ephemeral=True)

    elif custom_id in {"panel_request_usdt", "panel_request_usdt_bep20"}:
        if not await guard_auto_mm_interaction(interaction):
            return
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Please click the button again.", ephemeral=True)
                return
            await interaction.response.send_modal(RequestModal("USDT_BEP20"))
        except Exception as exc:
            print(f"[PANEL_USDT_MODAL_ERROR] {exc}")
            if interaction.response.is_done():
                await interaction.followup.send("Could not open USDT request form.", ephemeral=True)
            else:
                await interaction.response.send_message("Could not open USDT request form.", ephemeral=True)

    elif custom_id == "panel_request_paypal":
        if not await guard_auto_mm_interaction(interaction):
            return
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Please click the button again.", ephemeral=True)
                return
            await interaction.response.send_modal(RequestModal("PAYPAL"))
        except Exception as exc:
            print(f"[PANEL_PAYPAL_MODAL_ERROR] {exc}")
            if interaction.response.is_done():
                await interaction.followup.send("Could not open PayPal request form.", ephemeral=True)
            else:
                await interaction.response.send_message("Could not open PayPal request form.", ephemeral=True)

    elif custom_id == "panel_request_cashapp":
        if not await guard_auto_mm_interaction(interaction):
            return
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Please click the button again.", ephemeral=True)
                return
            await interaction.response.send_modal(RequestModal("CASHAPP"))
        except Exception as exc:
            print(f"[PANEL_CASHAPP_MODAL_ERROR] {exc}")
            if interaction.response.is_done():
                await interaction.followup.send("Could not open Cash App request form.", ephemeral=True)
            else:
                await interaction.response.send_message("Could not open Cash App request form.", ephemeral=True)

async def handle_copy_button(interaction: discord.Interaction):
    """Handle copy button interactions"""
    try:
        # Extract text from custom_id hash (simplified for demo)
        await interaction.response.send_message(
            " **Copy functionality activated**\n\nThis would copy the address/amount to your clipboard.",
            ephemeral=True
        )
    except Exception as e:
        print(f"Error handling copy button: {e}")

async def handle_dispute_button(interaction: discord.Interaction):
    """Handle dispute button interactions"""
    try:
        # Extract ticket ID from custom_id
        parts = interaction.data.get("custom_id", "").split("_")
        if len(parts) < 2:
            return
        
        ticket_id = int(parts[1])
        ticket = get_ticket(ticket_id)
        
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        
        if interaction.user.id not in [ticket[2], ticket[3]]:
            await interaction.response.send_message("Only deal participants can dispute.", ephemeral=True)
            return
        
        # Update ticket status to disputed
        update_ticket(ticket_id, status="disputed")
        
        # Log dispute
        log_action("DISPUTE_OPENED", ticket_id, interaction.user.id, f"deal_disputed_by_user")
        
        # Create dispute embed
        embed = discord.Embed(
            title=" Dispute Opened",
            description=f"<@{interaction.user.id}> has opened a dispute for this deal.",
            color=STATUS_COLORS['disputed']
        )
        embed.add_field(name="Next Steps", value="An admin will review this case shortly.", inline=False)
        embed.set_footer(text=f"Ticket #{ticket_id} | Dispute ID: DSP-{ticket_id:04d}")
        
        await interaction.response.send_message(embed=embed)
        
        # Notify admin channel
        if LOG_CHANNEL_ID:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                admin_embed = discord.Embed(
                    title=" New Dispute",
                    description=f"Deal #{ticket_id} disputed by {interaction.user.mention}",
                    color=0xEF4444
                )
                await log_channel.send(f"<@{ADMIN_ID}>", embed=admin_embed)
                
    except Exception as e:
        print(f"Error handling dispute: {e}")
        await interaction.response.send_message("Error processing dispute.", ephemeral=True)

async def handle_cancel_button(interaction: discord.Interaction):
    """Handle cancel button interactions (admin only)"""
    try:
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        
        parts = interaction.data.get("custom_id", "").split("_")
        if len(parts) < 2:
            return
        
        ticket_id = int(parts[1])
        ticket = get_ticket(ticket_id)
        
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        
        # Update ticket status
        update_ticket(ticket_id, status="cancelled")
        
        # Log cancellation
        log_action("FORCE_CANCEL", ticket_id, interaction.user.id, "admin_forced_cancellation")
        
        embed = discord.Embed(
            title=" Deal Cancelled",
            description="This deal has been cancelled by an administrator.",
            color=STATUS_COLORS['cancelled']
        )
        
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        print(f"Error handling cancel: {e}")
        await interaction.response.send_message("Error cancelling deal.", ephemeral=True)

async def handle_copy_address_button(interaction: discord.Interaction):
    """Handle copy address button"""
    try:
        parts = interaction.data.get("custom_id", "").split("_")
        if len(parts) < 3:
            return
        
        ticket_id = int(parts[2])
        ticket = get_ticket(ticket_id)
        
        if not ticket or not ticket[7]:
            await interaction.response.send_message("Address not available.", ephemeral=True)
            return
        
        await interaction.response.send_message(
            f"```{ticket[7]}```\n\n **Address copied to clipboard!**",
            ephemeral=True
        )
        
        log_action("ADDRESS_COPIED", ticket_id, interaction.user.id, f"payment_address_copied")
        
    except Exception as e:
        print(f"Error copying address: {e}")

async def handle_copy_amount_button(interaction: discord.Interaction):
    """Handle copy amount button"""
    try:
        parts = interaction.data.get("custom_id", "").split("_")
        if len(parts) < 3:
            return
        
        ticket_id = int(parts[2])
        ticket = get_ticket(ticket_id)
        
        if not ticket:
            await interaction.response.send_message("Amount not available.", ephemeral=True)
            return
        
        crypto = ticket[4]
        amount = get_locked_amount_crypto(ticket) or ticket[5]
        
        await interaction.response.send_message(
            f"```{amount} {crypto}```\n\n **Amount copied to clipboard!**",
            ephemeral=True
        )
        
        log_action("AMOUNT_COPIED", ticket_id, interaction.user.id, f"payment_amount_copied")
        
    except Exception as e:
        print(f"Error copying amount: {e}")

async def handle_release_button(interaction: discord.Interaction):
    """Handle release button interactions"""
    try:
        parts = interaction.data.get("custom_id", "").split("_")
        if len(parts) < 2:
            return
        
        ticket_id = int(parts[1])
        ticket = get_ticket(ticket_id)
        
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        
        # Check if user is authorized (buyer or admin)
        if interaction.user.id != ticket[2] and interaction.user.id != ADMIN_ID:
            await interaction.response.send_message("Only the buyer can release funds.", ephemeral=True)
            return
        
        # Check if deal is locked (payment detected)
        if not is_deal_locked(ticket_id):
            await interaction.response.send_message("Payment must be confirmed before releasing funds.", ephemeral=True)
            return
        
        # Create release confirmation modal
        modal = ReleaseModal(ticket_id, ticket[4])
        await interaction.response.send_modal(modal)
        
        log_action("RELEASE_INITIATED", ticket_id, interaction.user.id, "release_flow_started")
        
    except Exception as e:
        print(f"Error handling release: {e}")
        await interaction.response.send_message("Error initiating release.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"[STARTUP] Bot is online as {bot.user}")
    attach_bot(bot)
    mark_ready()
    append_control_log(f"[BOT] ready as {bot.user} (id={getattr(bot.user, 'id', '')})")
    try:
        await start_control_api(bot, post_main_panel_to_channel, log_action, unlock_deal)
    except Exception as control_exc:
        print(f"[CONTROL_API] failed to start: {control_exc}")
    bot.loop.create_task(resume_pending_monitors())
    try:
        # Persistent panel/request views so buttons survive bot restarts.
        bot.add_view(RequestCashAppView())
        bot.add_view(RequestPayPalView())
        bot.add_view(RequestLTCView())
        bot.add_view(RequestUSDTBEP20View())
        bot.add_view(RequestUSDTETHView())
        bot.add_view(DogPanelView())
        bot.add_view(ManualMMCategoryView())
        bot.add_view(ShowTosView())
        print("[STARTUP] Persistent views registered")
    except Exception as view_exc:
        print(f"[STARTUP] Persistent view registration failed: {view_exc}")
    try:
        synced = await bot.tree.sync()
        print(f"[STARTUP] Synced {len(synced)} slash command(s)")
    except Exception as sync_exc:
        print(f"[STARTUP] Slash command sync failed: {sync_exc}")
    
    # Advanced: Initialize safety systems
    print("[STARTUP] Advanced escrow features initialized")
    log_action("BOT_STARTED", 0, bot.user.id, "Advanced escrow system ready")
    
    # Start background tasks
    bot.loop.create_task(cleanup_completed_deals())
    bot.loop.create_task(health_check())
    
    print("[STARTUP] Background tasks started: cleanup, health_check")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing argument. Check command usage and try again.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument type. Check command usage and try again.")
        return
    print(f"[COMMAND_ERROR] {repr(error)}")
    traceback.print_exception(type(error), error, error.__traceback__)
    await ctx.send("An unexpected error occurred while running that command.")

try:
    bot.run(TOKEN)
except discord.LoginFailure:
    print("[STARTUP] Invalid DISCORD_TOKEN. Regenerate token in Discord Developer Portal and update .env.")

