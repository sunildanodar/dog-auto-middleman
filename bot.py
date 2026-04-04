@bot.command(name='sparkles_panel', help='Show Sparkles-style panel with all options')
async def sparkles_panel(ctx):
    embed = discord.Embed(
        title="✨ Sparkles's Auto Middleman ✨",
        description=(
            "**__Paid Service__**\n"
            "\n"
            "**• Read our ToS before using the bot:** <#TOS_CHANNEL_ID>\n"
            "**• The ToS in <#MM_TOS_CHANNEL_ID> also apply here.**\n"
            "\n"
            "───────────────────────────────"
        ),
        color=0x23272A
    )
    embed.set_footer(text="Tutorial", icon_url="https://cdn.discordapp.com/emojis/1171577373022332998.png?v=1")

    # Panel for LTC
    ltc_embed = discord.Embed(
        title="<:ltc:1171577373022332998>  **Request Litecoin**  <:ltc:1171577373022332998>",
        color=0x23272A
    )
    ltc_embed.add_field(name="\u200b", value="[  **Request LTC**  ]", inline=False)

    # Panel for USDT BEP-20
    usdt_bep20_embed = discord.Embed(
        title="<:usdt:1171577373022332998>  **Request USDT [BEP-20]**  <:usdt:1171577373022332998>",
        description="**Network:** BSC (BEP-20)",
        color=0x10B981
    )
    usdt_bep20_embed.add_field(name="\u200b", value="[  **Request USDT [BEP-20]**  ]", inline=False)

    # Panel for USDT ETH
    usdt_eth_embed = discord.Embed(
        title="<:usdt:1171577373022332998>  **Request USDT [ETH]**  <:usdt:1171577373022332998>",
        description="**Network:** Ethereum",
        color=0x6366F1
    )
    usdt_eth_embed.add_field(name="\u200b", value="[  **Request USDT [ETH]**  ]", inline=False)

    await ctx.send(embed=embed)
    await ctx.send(embed=ltc_embed, view=RequestLTCView())
    await ctx.send(embed=usdt_bep20_embed, view=RequestUSDTBEP20View())
    await ctx.send(embed=usdt_eth_embed, view=RequestUSDTETHView())
import discord
import asyncio
import time
import datetime
import json
import random
import string
import re
import secrets
import requests
import os
from discord.ext import commands
from discord import ui
from config import TOKEN, LOG_CHANNEL_ID, PROOF_CHANNEL_ID, TICKET_CATEGORY_ID, ADMIN_ID, CONFIRMATIONS_REQUIRED, BLOCKCYPHER_TOKEN, CODE_VERSION, DB_BACKUP_INTERVAL_MINUTES, REQUIRE_PERSISTENT_DB, DB_NAME, BACKUP_ALERT_MAX_AGE_MINUTES, BACKUP_STARTUP_MAX_AGE_MINUTES, PAYMENT_POLL_INTERVAL_SECONDS, LTC_NETWORK_FEE_USD, FEE_PERCENT
from crypto import generate_ltc_wallet, generate_bep20_wallet, detect_ltc_payment, detect_usdt_payment, send_ltc, send_usdt, sweep_ltc_to_master, sweep_usdt_to_master, usd_to_ltc, decrypt_key, private_hex_to_ltc_address
from database import init, save_ticket, update_ticket, get_ticket, get_ticket_by_channel, get_next_ticket_id, get_tickets_by_status, log_event, get_ticket_events, verify_ticket_audit_chain, create_db_backup, database_safety_snapshot, create_encrypted_backup_export

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
init()
active_monitors = set()
slash_synced = False
withdraw_cooldowns = {}
withdraw_retry_tasks = {}

PAYMENT_POLL_INTERVAL_SECONDS = max(PAYMENT_POLL_INTERVAL_SECONDS, 10)
WITHDRAW_CONFIRM_COOLDOWN_SECONDS = 180
WITHDRAW_RETRY_BASE_SECONDS = 180
WITHDRAW_RETRY_MAX_ATTEMPTS = 5
SPARKLES_TITLE = "DOG AUTO MIDDLEMAN"
SPARKLES_FOOTER = "Dog Escrow"
SENSITIVE_COMMAND_COOLDOWN_SECONDS = 8
MIN_DEAL_USD = 0.1
MAX_DEAL_USD = 50000.0
sensitive_command_last_used = {}
withdraw_processing = set()
fake_confirmation_tasks = {}
payment_view_registered = False
backup_task_started = False
security_alert_last_sent = {}

def log(guild, msg):
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch:
        asyncio.create_task(ch.send(msg))


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
        await channel.send(f"⚠️ SECURITY ALERT\n{message}")
    except Exception:
        pass


def is_admin_user(guild, user):
    return user.id == ADMIN_ID or (guild is not None and user.id == guild.owner_id)


def running_on_railway():
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


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
    embed = discord.Embed(
        title="🔍 Code Version Running",
        description=f"```\n{CODE_VERSION}\n```",
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
    # Apply the same payout fee policy for every LTC deal size.
    return max(value - LTC_NETWORK_FEE_USD, 0.0)


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
        title=SPARKLES_TITLE,
        description="**DEAL DETAILS LOCKED**\nBoth parties should review this before payment.",
        color=0x111827,
    )
    embed.add_field(name="USD AMOUNT", value=f"**${amount:.2f}**", inline=True)
    embed.add_field(name="DESCRIPTION", value=description, inline=False)
    embed.add_field(name="STATUS", value="Waiting for buyer and seller confirmation", inline=False)
    embed.set_footer(text=SPARKLES_FOOTER)
    return embed


def build_payment_embed(ticket, wallet_address):
    locked_amount = get_locked_amount_crypto(ticket)
    amount_ltc = locked_amount if (ticket[4] == "LTC" and locked_amount) else (usd_to_ltc(ticket[5]) if ticket[4] == "LTC" else ticket[5])
    pay_exact_usd = ltc_deposit_target_usd(ticket[5]) if ticket[4] == "LTC" else ticket[5]
    seller_receive_usd = seller_payout_usd(ticket[5], ticket[4])
    embed = discord.Embed(
        title=SPARKLES_TITLE,
        description=(
            "**DEPOSIT STAGE - SECURE ESCROW**\n"
            "Send the exact amount shown below to activate trade protection."
        ),
        color=0x0F172A,
    )
    embed.add_field(name="DEAL ID", value=f"`{ticket[12] or 'pending'}`", inline=False)
    embed.add_field(name="DEAL DESCRIPTION", value=ticket[11] or "No description provided", inline=False)
    embed.add_field(name="PAY EXACTLY (USD)", value=f"**${pay_exact_usd:.2f}**", inline=True)
    embed.add_field(name=f"PAY EXACTLY ({asset_label(ticket[4])})", value=f"**{amount_ltc:.8f} {asset_label(ticket[4])}**", inline=True)
    # Show seller receives for all assets if fee applies
    if seller_receive_usd < pay_exact_usd:
        embed.add_field(name="SELLER RECEIVES (USD)", value=f"**${seller_receive_usd:.2f}**", inline=True)
    embed.add_field(name="ESCROW WALLET", value=f"`{wallet_address}`", inline=False)
    embed.add_field(
        name="IMPORTANT",
        value="- Never send directly to seller.\n- Use `Copy Details` button below.\n- Release only after buyer confirms delivery.",
        inline=False,
    )
    embed.set_footer(text="Dog Escrow | Auto-monitoring enabled | Confirmations update automatically")
    return embed


def build_unconfirmed_embed(crypto, amount_usd, required_amount, txid="simulated", confirmations=0, received_amount=None):
    display_received = required_amount if received_amount is None else received_amount
    embed = discord.Embed(
        title=SPARKLES_TITLE,
        description=(
            "**PAYMENT DETECTED (UNCONFIRMED)**\n"
            f"Waiting for blockchain confirmations: **{confirmations}/{CONFIRMATIONS_REQUIRED}**."
        ),
        color=0xB45309,
    )
    embed.add_field(name="TRANSACTION", value=f"`{short_txid(txid)}`", inline=False)
    embed.add_field(name="RECEIVED", value=f"{display_received:.8f} {crypto} (${amount_usd:.2f})", inline=True)
    embed.add_field(name="REQUIRED", value=f"{required_amount:.8f} {crypto} (${amount_usd:.2f})", inline=True)
    embed.set_footer(text="Dog Escrow | Awaiting confirmations")
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
                raw_deal = field_map.get("DEAL ID", "").replace("`", "").strip()
                if raw_deal:
                    ticket_id = raw_deal

                raw_wallet = field_map.get("ESCROW WALLET", "").replace("`", "").strip()
                if raw_wallet:
                    wallet_address = raw_wallet

                raw_usd = field_map.get("PAY EXACTLY (USD)", "")
                usd_match = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", raw_usd)
                if usd_match:
                    amount_usd = float(usd_match.group(1))

                for name, value in field_map.items():
                    if not name.startswith("PAY EXACTLY (") or name == "PAY EXACTLY (USD)":
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
            f"Amount: {float(amount_crypto):.8f} {crypto} (${float(amount_usd):.2f})\n"
            f"Address: {wallet_address}"
        )
        await interaction.response.send_message(
            f"Copy and send exactly this:\n```text\n{text}\n```",
            ephemeral=True,
        )

class RequestModal(ui.Modal, title="Request Middleman Service"):
    user_input = ui.TextInput(label="Enter User ID or @mention", placeholder="123456789 or @user")

    def __init__(self, crypto):
        super().__init__()
        self.crypto = crypto

    async def on_submit(self, interaction):
        channel = None
        deal_id = f"pending-{int(time.time())}"
        try:
            user_id = int(self.user_input.value.strip('<@!>'))
            user = interaction.guild.get_member(user_id)
            if not user:
                await interaction.response.send_message("User not found.", ephemeral=True)
                return
        except Exception:
            await interaction.response.send_message("Invalid user ID.", ephemeral=True)
            return

        # Acknowledge quickly so Discord does not show "Something went wrong"
        # while channel/permission operations are in progress.
        await interaction.response.defer(ephemeral=True)

        try:
            category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
            ticket_id = get_next_ticket_id()
            deal_id = f"{ticket_id}-{int(time.time())}"
            channel = await interaction.guild.create_text_channel(f"ticket-{ticket_id}", category=category)

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

            embed = discord.Embed(
                title=SPARKLES_TITLE,
                description=(
                    "**PREMIUM ESCROW TICKET OPENED**\n"
                    "Secure middleman workflow for high-trust trades."
                ),
                color=0x111827
            )
            embed.add_field(name="BUYER", value=f"<@{interaction.user.id}>", inline=True)
            embed.add_field(name="SELLER", value=f"<@{user.id}>", inline=True)
            embed.add_field(name="DEAL ID", value=f"`{deal_id}`", inline=False)
            embed.add_field(name="ASSET", value=self.crypto, inline=True)
            embed.add_field(name="STATUS", value="Awaiting role selection", inline=True)
            embed.add_field(
                name="SECURITY NOTES",
                value=(
                    "- Never send directly to seller.\n"
                    "- Only release after delivery is verified.\n"
                    "- Use bot buttons in this ticket only."
                ),
                inline=False,
            )
            embed.set_footer(text="Dog Auto Middleman")

            view = RoleSelectView(ticket_id, interaction.user.id, user.id, self.crypto)
            msg = await channel.send(embed=embed, view=view)

            save_ticket(ticket_id, channel.id, interaction.user.id, user.id, self.crypto, 0, "", "", msg.id, "", deal_id)
            await audit(interaction.guild, ticket_id, "ticket_created", f"buyer={interaction.user.id} seller={user.id} crypto={self.crypto} deal_id={deal_id}")
            await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)
        except Exception as exc:
            if channel is not None:
                try:
                    await channel.delete(reason="Ticket setup failed during modal submit")
                except Exception:
                    pass
            await interaction.followup.send(f"Could not create ticket. {exc}", ephemeral=True)

class RoleSelectView(ui.View):
    def __init__(self, ticket_id, user1, user2, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.user1 = user1
        self.user2 = user2
        self.crypto = crypto
        self.roles = {}
        self.role_confirms = set()
        self.roles_finalized = False

    async def check_confirm(self, interaction):
        if len(self.roles) == 2:
            buyer_id = [k for k, v in self.roles.items() if v == "buyer"][0]
            seller_id = [k for k, v in self.roles.items() if v == "seller"][0]
            preview = discord.Embed(
                title=SPARKLES_TITLE,
                description=(
                    "**ROLE SELECTION COMPLETE**\n"
                    f"Buyer: <@{buyer_id}>\n"
                    f"Seller: <@{seller_id}>"
                ),
                color=0x111827,
            )
            preview.set_footer(text="Click Confirm Roles to continue, or Reset Roles to re-pick.")
            await interaction.channel.send(embed=preview)

    @ui.button(label="Buyer", style=discord.ButtonStyle.primary)
    async def buyer(self, interaction, button):
        if interaction.user.id not in [self.user1, self.user2]:
            return
        if interaction.user.id in self.roles:
            await interaction.response.send_message("You have already selected a role.", ephemeral=True)
            return
        self.roles[interaction.user.id] = "buyer"
        await interaction.response.send_message(f":white_check_mark: <@{interaction.user.id}> selected **Buyer**.", ephemeral=False)
        await self.check_confirm(interaction)

    @ui.button(label="Seller", style=discord.ButtonStyle.secondary)
    async def seller(self, interaction, button):
        if interaction.user.id not in [self.user1, self.user2]:
            return
        if interaction.user.id in self.roles:
            await interaction.response.send_message("You already selected a role.", ephemeral=True)
            return
        self.roles[interaction.user.id] = "seller"
        await interaction.response.send_message(f":white_check_mark: <@{interaction.user.id}> selected **Seller**.", ephemeral=False)
        await self.check_confirm(interaction)

    @ui.button(label="Confirm Roles", style=discord.ButtonStyle.success)
    async def confirm_roles(self, interaction, button):
        if self.roles_finalized:
            await interaction.response.send_message("Roles already confirmed for this ticket.", ephemeral=True)
            return
        if interaction.user.id not in [self.user1, self.user2]:
            await interaction.response.send_message("Only ticket participants can confirm roles.", ephemeral=True)
            return
        if len(self.roles) != 2:
            await interaction.response.send_message("Both users must select roles first.", ephemeral=False)
            return
        if interaction.user.id not in self.roles:
            await interaction.response.send_message("You must pick Buyer or Seller before confirming.", ephemeral=True)
            return

        self.role_confirms.add(interaction.user.id)
        if len(self.role_confirms) < 2:
            await interaction.response.send_message(
                f"✅ <@{interaction.user.id}> confirmed roles ({len(self.role_confirms)}/2).",
                ephemeral=False,
            )
            return

        self.roles_finalized = True
        buyer_id = [k for k, v in self.roles.items() if v == "buyer"][0]
        seller_id = [k for k, v in self.roles.items() if v == "seller"][0]
        update_ticket(self.ticket_id, buyer_id=buyer_id, seller_id=seller_id)
        await audit(interaction.guild, self.ticket_id, "roles_confirmed", f"buyer={buyer_id} seller={seller_id}")
        await interaction.channel.send(f":white_check_mark: Roles confirmed for ticket {self.ticket_id}! Buyer: <@{buyer_id}> | Seller: <@{seller_id}>")
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**ROLES CONFIRMED**\nBuyer can now enter the deal amount.",
            color=0x0F172A
        )
        await interaction.channel.send(embed=embed, view=AmountView(self.ticket_id, buyer_id, self.crypto))
        await interaction.response.defer()

    @ui.button(label="Reset Roles", style=discord.ButtonStyle.danger)
    async def reset_roles(self, interaction, button):
        if interaction.user.id not in [self.user1, self.user2]:
            await interaction.response.send_message("Only ticket participants can reset roles.", ephemeral=True)
            return

        self.roles = {}
        self.role_confirms = set()
        self.roles_finalized = False
        await interaction.response.send_message(
            f"Role selection was reset by <@{interaction.user.id}>. Please choose roles again.",
            ephemeral=False,
        )

class AmountModal(ui.Modal, title="Enter Deal Details"):
    amount = ui.TextInput(label="Amount in USD", placeholder="100.00")
    description = ui.TextInput(label="Deal Description (Optional)", placeholder="What are you trading?", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__()
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto

    async def on_submit(self, interaction):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Only buyer can enter details.", ephemeral=True)
            return
        try:
            amt = float(self.amount.value)
        except:
            await interaction.response.send_message("Invalid amount.", ephemeral=True)
            return
        if not is_valid_deal_amount(amt):
            await interaction.response.send_message(
                f"Amount must be between ${MIN_DEAL_USD:.2f} and ${MAX_DEAL_USD:.2f}.",
                ephemeral=True,
            )
            return
        desc = self.description.value or "No description provided"
        update_ticket(self.ticket_id, amount=amt, description=desc)
        await audit(interaction.guild, self.ticket_id, "amount_set", f"usd={amt:.2f} description={desc[:120]}")
        embed = build_amount_embed(amt, desc)
        view = ConfirmAmountView(self.ticket_id, self.buyer_id, self.crypto)
        await interaction.response.send_message(embed=embed, view=view)

class AmountView(ui.View):
    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto

    @ui.button(label="Enter Amount", style=discord.ButtonStyle.primary, emoji="💵")
    async def enter_amount(self, interaction, button):
        if interaction.user.id != self.buyer_id:
            return
        await interaction.response.send_modal(AmountModal(self.ticket_id, self.buyer_id, self.crypto))

class ConfirmAmountView(ui.View):
    def __init__(self, ticket_id, buyer_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.buyer_id = buyer_id
        self.crypto = crypto
        self.confirms = set()

    @ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction, button):
        ticket = get_ticket(self.ticket_id)
        if interaction.user.id not in [ticket[2], ticket[3]]:  # buyer, seller
            return
        self.confirms.add(interaction.user.id)
        await interaction.response.send_message(f"✅ <@{interaction.user.id}> confirmed the USD amount ({len(self.confirms)}/2).", ephemeral=False)
        if len(self.confirms) == 2:
            ticket = get_ticket(self.ticket_id)
            if not ticket:
                return
            if self.crypto == "LTC":
                wallet = generate_ltc_wallet()
            else:
                wallet = generate_bep20_wallet()
            locked_amount = usd_to_ltc(ltc_deposit_target_usd(ticket[5])) if self.crypto == "LTC" else ticket[5]
            update_ticket(
                self.ticket_id,
                wallet_address=wallet["address"],
                encrypted_private=wallet["private"],
                status="pending_payment",
                locked_amount_crypto=locked_amount,
            )
            ticket = get_ticket(self.ticket_id)
            embed = build_payment_embed(ticket, wallet["address"])
            amount_crypto = get_locked_amount_crypto(ticket) if self.crypto == "LTC" else ticket[5]
            payment_msg = await interaction.channel.send(
                embed=embed,
                view=PaymentDetailsView(
                    self.ticket_id,
                    wallet["address"],
                    amount_crypto,
                    self.crypto,
                    ticket[5],
                ),
            )
            update_ticket(self.ticket_id, message_id=payment_msg.id)
            await audit(interaction.guild, self.ticket_id, "payment_requested", f"wallet={wallet['address']} usd={ticket[5]:.2f}")
            bot.loop.create_task(monitor_payment(self.ticket_id, wallet["address"], ticket[5], self.crypto, payment_msg))

async def monitor_payment(ticket_id, address, amount, crypto, msg):
    active_monitors.add(ticket_id)
    last_unconfirmed_conf = None
    try:
        while True:
            ticket = get_ticket(ticket_id)
            locked_amount = get_locked_amount_crypto(ticket)
            if crypto == "LTC":
                required_ltc = locked_amount or usd_to_ltc(amount)
                if locked_amount is None:
                    update_ticket(ticket_id, locked_amount_crypto=required_ltc)
                paid, conf, txid, received_ltc = detect_ltc_payment(address, amount, required_ltc=required_ltc)
            else:
                paid, conf, txid, received_ltc = detect_usdt_payment(address, amount, network=usdt_network_from_asset(crypto))
                required_ltc = amount

            if paid:
                if conf < CONFIRMATIONS_REQUIRED:
                    update_ticket(ticket_id, status="unconfirmed")
                    await audit(msg.guild, ticket_id, "payment_detected", f"txid={txid} confirmations={conf} received={received_ltc:.8f}")
                    embed = build_unconfirmed_embed(
                        crypto=crypto,
                        amount_usd=amount,
                        required_amount=required_ltc,
                        txid=txid,
                        confirmations=conf,
                        received_amount=received_ltc,
                    )
                    if last_unconfirmed_conf != conf:
                        await msg.channel.send(embed=embed)
                        last_unconfirmed_conf = conf

                else:
                    update_ticket(ticket_id, status="paid")
                    await audit(msg.guild, ticket_id, "payment_confirmed", f"txid={txid} confirmations={conf} received={received_ltc:.8f}")
                    embed = discord.Embed(
                        title=SPARKLES_TITLE,
                        description="**PAYMENT CONFIRMED**\nDeposit verified successfully. Release controls are now ready.",
                        color=0x10B981,
                    )
                    embed.add_field(name="TRANSACTION", value=f"`{short_txid(txid)}`", inline=False)
                    embed.add_field(name="TOTAL RECEIVED", value=f"{received_ltc:.8f} {crypto} (${amount:.2f})", inline=False)
                    embed.set_footer(text="Dog Escrow | Confirm delivery before releasing")
                    await msg.channel.send(embed=embed)

                    ticket = get_ticket(ticket_id)
                    if ticket:
                        instructions = discord.Embed(
                            title=SPARKLES_TITLE,
                            description=(
                                "**DEPOSIT CONFIRMED - TRADE LIVE**\n\n"
                                f"1. <@{ticket[3]}> Deliver the product/payment agreed in this deal.\n\n"
                                f"2. <@{ticket[2]}> After receiving everything, click release so seller can withdraw {crypto}."
                            ),
                            color=0x10B981,
                        )
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
                                description="**DEPOSIT CONFIRMED - TRADE LIVE**\nRelease controls were re-posted in a new message.",
                                color=0x10B981,
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

async def resume_pending_monitors():
    await bot.wait_until_ready()
    tickets = get_tickets_by_status(["pending_payment", "unconfirmed"])
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

class ReleaseRefundView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto

    @ui.button(label="Release", style=discord.ButtonStyle.success, emoji="🚀")
    async def release(self, interaction, button):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.followup.send("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id != ticket[2] and not is_admin_user(interaction.guild, interaction.user):  # buyer or admin starts release flow
            await interaction.followup.send("Only the buyer or an admin can start release.", ephemeral=True)
            return

        ticket_status = str(ticket[6] or "").strip().lower()
        if ticket_status not in ("paid", "releasing"):
            current_paid = False
            current_conf = 0
            current_txid = None
            current_received = 0.0
            try:
                if ticket[7]:
                    if ticket[4] == "LTC":
                        required_ltc = get_locked_amount_crypto(ticket) or usd_to_ltc(ticket[5])
                        current_paid, current_conf, current_txid, current_received = detect_ltc_payment(
                            ticket[7],
                            ticket[5],
                            required_ltc=required_ltc,
                        )
                    else:
                        current_paid, current_conf, current_txid, current_received = detect_usdt_payment(
                            ticket[7],
                            ticket[5],
                            network=usdt_network_from_asset(ticket[4]),
                        )
            except Exception:
                current_paid = False

            if current_paid and current_conf >= CONFIRMATIONS_REQUIRED:
                update_ticket(self.ticket_id, status="paid")
                await audit(
                    interaction.guild,
                    self.ticket_id,
                    "release_status_recovered",
                    f"from={ticket_status} txid={current_txid} conf={current_conf} received={current_received}",
                )
                ticket = get_ticket(self.ticket_id)
            else:
                conf_text = f" ({current_conf}/{CONFIRMATIONS_REQUIRED} confirmations)" if current_paid else ""
                await interaction.followup.send(
                    f"Release can only start after payment is confirmed{conf_text}.",
                    ephemeral=True,
                )
                return

        warning = discord.Embed(
            title=SPARKLES_TITLE,
            description="**RELEASE CONFIRMATION**\nClick Confirm to let seller submit payout address and continue withdrawal.",
            color=0xF0B429,
        )
        warning.set_footer(text=SPARKLES_FOOTER)
        await audit(interaction.guild, self.ticket_id, "release_started", f"buyer={interaction.user.id}")
        await interaction.followup.send(embed=warning, view=ReleaseWarningView(self.ticket_id, self.crypto), ephemeral=False)
    @ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction, button):
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id not in [ticket[2], ticket[3]]:
            await interaction.response.send_message("Only buyer or seller can cancel.", ephemeral=True)
            return
        update_ticket(self.ticket_id, status="cancelled")
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**TRADE CANCELLED**\nThe trade has been cancelled by one of the participants.",
            color=0xFF0000
        )
        embed.set_footer(text=SPARKLES_FOOTER)
        await interaction.response.send_message(embed=embed)

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
            title=SPARKLES_TITLE,
            description=f"**CONFIRM PAYOUT ADDRESS**\nAddress: `{seller_address}`\n\nClick Confirm to withdraw funds or Back to cancel.",
            color=0x00FF00
        )
        embed.set_footer(text=SPARKLES_FOOTER)
        await interaction.response.send_message(embed=embed, view=ReleaseConfirmView(self.ticket_id, self.crypto), ephemeral=False)


class ReleaseWarningView(ui.View):
    def __init__(self, ticket_id, crypto):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.crypto = crypto

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        ticket = get_ticket(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Ticket not found.", ephemeral=True)
            return
        if interaction.user.id != ticket[2] and not is_admin_user(interaction.guild, interaction.user):
            await interaction.response.send_message("Only the buyer or an admin can confirm this step.", ephemeral=True)
            return

        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description=f"Seller <@{ticket[3]}>, enter your payout address to continue.",
            color=0x2b2d31,
        )
        embed.set_footer(text=SPARKLES_FOOTER)
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

    @ui.button(label="Enter Your LTC Address", style=discord.ButtonStyle.primary, emoji="📥")
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

    @ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
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

        # Acknowledge immediately to avoid 3s interaction timeout during API calls.
        await interaction.response.defer()
        update_ticket(self.ticket_id, status="releasing")
        await audit(interaction.guild, self.ticket_id, "withdraw_attempt", f"seller={interaction.user.id} address={ticket[9]}")

        # If payment was forced via /transaction or !fake_tx, skip real blockchain send.
        if has_fake_payment_marker(self.ticket_id):
            fake_txid = f"simulated-{int(time.time())}"
            update_ticket(self.ticket_id, status="completed")
            await audit(
                interaction.guild,
                self.ticket_id,
                "withdraw_success",
                f"txid={fake_txid} address={ticket[9]} simulated=true",
            )
            embed = discord.Embed(
                title=SPARKLES_TITLE,
                description="**WITHDRAWAL SUCCESSFUL**\nPayment was sent to seller address.",
                color=0x00FF00
            )
            embed.add_field(name="Transaction", value=f"`{fake_txid}`", inline=False)
            embed.add_field(name="Mode", value="Simulated transfer (/transaction)", inline=False)
            embed.set_footer(text=SPARKLES_FOOTER)
            await interaction.followup.send(embed=embed)
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
                title=SPARKLES_TITLE,
                description="**WITHDRAWAL SUCCESSFUL**\nFunds were sent to seller address.",
                color=0x00FF00
            )
            embed.add_field(name="Transaction", value=f"`{txid}`", inline=False)
            if self.crypto == "LTC":
                embed.add_field(name="Explorer", value=ltc_tx_link(txid), inline=False)
            embed.set_footer(text=SPARKLES_FOOTER)
            await interaction.followup.send(embed=embed)
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

    @ui.button(label="Request LTC", style=discord.ButtonStyle.primary, emoji="🪙", custom_id="panel_request_ltc")
    async def ltc(self, interaction, button):
        await interaction.response.send_modal(RequestModal("LTC"))


class RequestUSDTBEP20View(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Request USDT [BEP-20]", style=discord.ButtonStyle.success, emoji="💎", custom_id="panel_request_usdt_bep20")
    async def usdt_bep20(self, interaction, button):
        await interaction.response.send_modal(RequestModal("USDT_BEP20"))


class RequestUSDTETHView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Request USDT [ETH]", style=discord.ButtonStyle.secondary, emoji="💠", custom_id="panel_request_usdt_eth")
    async def usdt_eth(self, interaction, button):
        await interaction.response.send_modal(RequestModal("USDT_ETH"))


class SparklesPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="LTC", style=discord.ButtonStyle.primary, emoji="🪙", custom_id="sparkles_panel_request_ltc", row=0)
    async def ltc(self, interaction, button):
        await interaction.response.send_modal(RequestModal("LTC"))

    @ui.button(label="USDT [BEP-20]", style=discord.ButtonStyle.success, emoji="💎", custom_id="sparkles_panel_request_usdt_bep20", row=0)
    async def usdt_bep20(self, interaction, button):
        await interaction.response.send_modal(RequestModal("USDT_BEP20"))

    @ui.button(label="USDT [ETH]", style=discord.ButtonStyle.secondary, emoji="💠", custom_id="sparkles_panel_request_usdt_eth", row=0)
    async def usdt_eth(self, interaction, button):
        await interaction.response.send_modal(RequestModal("USDT_ETH"))


@bot.command()
async def panel(ctx):
    await asyncio.sleep(random.uniform(0.25, 0.9))
    if await panel_recently_posted(ctx.channel):
        return

    main_embed = discord.Embed(
        title="SPARKLES AUTO MIDDLEMAN",
        description=(
            "**AUTO MIDDLEMAN PANEL**\n\n"
            "**PREMIUM ESCROW FOR CRYPTO DEALS**\n"
            "Clean flow. Fast setup. Secure release.\n\n"
            "**AVAILABLE NETWORKS**\n"
            "**LTC** - Litecoin escrow deals\n"
            "**USDT [BEP-20]** - USDT on BNB Smart Chain\n"
            "**USDT [ETH]** - USDT on Ethereum\n\n"
            "**HOW IT WORKS**\n"
            "Buyer and seller confirm terms, fund escrow, then release safely through the bot."
        ),
        color=0x111827,
    )
    main_embed.add_field(name="LTC", value="`Fast Litecoin middleman deals`", inline=True)
    main_embed.add_field(name="USDT [BEP-20]", value="`Best for BNB Smart Chain trades`", inline=True)
    main_embed.add_field(name="USDT [ETH]", value="`ERC-20 escrow on Ethereum`", inline=True)
    main_embed.add_field(name="Open A Deal", value="Use the buttons below in this order: `LTC`, `BEP-20`, `USDT ETH`.", inline=False)
    main_embed.set_footer(text="Sparkles Auto Middleman")

    await ctx.send(embed=main_embed, view=SparklesPanelView())


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

        update_ticket(ticket_id, status="paid")
        await audit(guild, ticket_id, "fake_payment_confirmed", f"auto_confirm_after_{wait_seconds}s")
        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**PAYMENT CONFIRMED**\nDeposit verified successfully. Release controls are now ready.",
            color=0x10B981,
        )
        embed.set_footer(text="Dog Escrow | Confirm delivery before releasing")
        release_msg = await msg.channel.send(embed=embed, view=ReleaseRefundView(ticket_id, crypto))
        update_ticket(ticket_id, message_id=release_msg.id)
    finally:
        fake_confirmation_tasks.pop(ticket_id, None)

@bot.hybrid_command(name="transaction", description="Check if transaction is confirmed or not.")
async def transaction(ctx):
    if not await enforce_sensitive_cooldown(ctx, "transaction"):
        return
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        if ctx.interaction:
            await ctx.interaction.response.send_message("Only server admins can use this command.", ephemeral=True)
        else:
            await ctx.send("Only server admins can use this command.")
        return

    ticket = get_ticket_by_channel(ctx.channel.id)
    if not ticket:
        if ctx.interaction:
            await ctx.interaction.response.send_message("Use this command inside a ticket channel.", ephemeral=True)
        else:
            await ctx.send("Use this command inside a ticket channel.")
        return

    try:
        msg = await ctx.channel.fetch_message(ticket[10])  # message_id is index 10
    except:
        if ctx.interaction:
            await ctx.interaction.response.send_message("Ticket payment message not found.", ephemeral=True)
        else:
            await ctx.send("Ticket payment message not found.")
        return

    if not ticket[7] or not ticket[8]:  # wallet_address and encrypted_private
        wallet = generate_ltc_wallet()
        update_ticket(ticket[0], wallet_address=wallet["address"], encrypted_private=wallet["private"])

    pending_task = fake_confirmation_tasks.get(ticket[0])
    if pending_task and not pending_task.done():
        if ctx.interaction:
            await ctx.interaction.response.send_message("Transaction already pending confirmation for this ticket.", ephemeral=True)
        else:
            await ctx.send("Transaction already pending confirmation for this ticket.")
        return

    wait_seconds = random.randint(10, 15)
    required_amount = get_locked_amount_crypto(ticket) if ticket[4] == "LTC" else ticket[5]
    if required_amount is None:
        required_amount = usd_to_ltc(ticket[5]) if ticket[4] == "LTC" else ticket[5]

    update_ticket(ticket[0], status="unconfirmed")
    await audit(ctx.guild, ticket[0], "fake_payment_unconfirmed", f"by={ctx.author.id}")
    status_msg = await ctx.channel.send(
        embed=build_unconfirmed_embed(
            crypto=ticket[4],
            amount_usd=ticket[5],
            required_amount=required_amount,
        )
    )
    fake_confirmation_tasks[ticket[0]] = bot.loop.create_task(
        finalize_fake_confirmation(ctx.guild, ticket[0], status_msg, ticket[4], wait_seconds)
    )

    if ctx.interaction:
        await ctx.interaction.response.send_message(
            "Transaction queued. Showing unconfirmed now.",
            ephemeral=True,
        )
    else:
        await ctx.send("Transaction queued. Showing unconfirmed now.")

@bot.command()
async def fake_tx(ctx, channel_id: int):
    if not await enforce_sensitive_cooldown(ctx, "fake_tx"):
        return
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
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
        await ctx.send(f"Ticket {ticket[0]} already has a pending simulated confirmation.")
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
        fake_txid = f"forced-simulated-{int(time.time())}"
        update_ticket(ticket[0], status="completed")
        await audit(ctx.guild, ticket[0], "force_release_success", f"txid={fake_txid} simulated=true")

        embed = discord.Embed(
            title=SPARKLES_TITLE,
            description="**FORCE RELEASE SUCCESSFUL**\nFunds were marked as released (simulated ticket).",
            color=0x10B981,
        )
        embed.add_field(name="Ticket", value=f"`#{ticket[0]}`", inline=True)
        embed.add_field(name="Seller", value=f"<@{ticket[3]}>", inline=True)
        embed.add_field(name="Transaction", value=f"`{fake_txid}`", inline=False)
        embed.add_field(name="Payout Address", value=f"`{payout_address}`", inline=False)
        embed.set_footer(text="Dog Escrow | Admin force release")
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
        embed.set_footer(text="Dog Escrow | Admin force release")
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

    random_txid = generate_random_txid()
    completed_at = int(time.time())

    proof_embed = discord.Embed(
        title=SPARKLES_TITLE,
        description="**DEAL PROOF**\nThis deal was completed through Dog Auto Middleman.",
        color=0x10B981,
    )
    proof_embed.add_field(name="Proof ID", value=f"`PRF-{ticket[0]}-{completed_at}`", inline=False)
    proof_embed.add_field(name="Deal ID", value=f"`{ticket[12] or f'TKT-{ticket[0]}'}`", inline=True)
    proof_embed.add_field(name="Ticket", value=f"`#{ticket[0]}`", inline=True)
    proof_embed.add_field(name="Buyer", value=f"<@{ticket[2]}>", inline=True)
    proof_embed.add_field(name="Seller", value=f"<@{ticket[3]}>", inline=True)
    proof_embed.add_field(name="Asset", value=f"`{ticket[4]}`", inline=True)
    proof_embed.add_field(name="Deal Amount", value=f"`${ticket[5]:.2f}`", inline=True)
    proof_embed.add_field(name="Status", value="`Completed`", inline=True)
    proof_embed.add_field(name="Transaction ID", value=f"`{random_txid}`", inline=False)
    proof_embed.set_footer(text="Dog Escrow | Proof generated")

    await target_channel.send(embed=proof_embed)
    await audit(ctx.guild, ticket[0], "proof_generated", f"by={ctx.author.id} txid={random_txid}")
    await ctx.send(f"Proof generated in {target_channel.mention}.")


@bot.command(name="proof")
async def proof(ctx, *parts):
    if not await enforce_sensitive_cooldown(ctx, "proof"):
        return
    if not is_admin_user(ctx.guild, ctx.author):
        await ctx.send("Only the configured admin or server owner can use this command.")
        return

    if not parts:
        await ctx.send("Usage: `!proof <amount> [transaction_id]`\nExample: `!proof 23 dollars dbcf54932...1f8f483b8`")
        return

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
    amount_ltc = usd_to_ltc(amount_value)

    proof_embed = discord.Embed(
        title="◔ · Trade Completed",
        description=f"**{amount_ltc:.8f} LTC (${amount_value:.2f} USD)**",
        color=0x111827,
    )
    proof_embed.add_field(name="Sender", value="`Anonymous`", inline=True)
    proof_embed.add_field(name="Receiver", value="`Anonymous`", inline=True)
    if final_txid and len(final_txid) > 21:
        tx_display = f"{final_txid[:9]}...{final_txid[-9:]}"
    else:
        tx_display = final_txid or "pending"
    tx_field_value = f"`{tx_display}`"
    tx_target_url = None
    if final_txid and re.fullmatch(r"[A-Fa-f0-9]{64}", final_txid):
        tx_target_url = ltc_tx_link(final_txid)
        tx_field_value = f"[{tx_display}]({tx_target_url})"
    elif tx_url:
        tx_target_url = tx_url
        tx_field_value = f"[{tx_display}]({tx_target_url})"
    proof_embed.add_field(name="Transaction ID", value=tx_field_value, inline=False)

    target_channel = ctx.guild.get_channel(PROOF_CHANNEL_ID) if (ctx.guild and PROOF_CHANNEL_ID > 0) else ctx.channel
    if not target_channel:
        await ctx.send("Proof channel not found. Set PROOF_CHANNEL_ID or run command in the target channel.")
        return

    proof_view = None
    if tx_target_url:
        proof_view = ui.View(timeout=None)
        proof_view.add_item(ui.Button(label="View Payment", style=discord.ButtonStyle.link, url=tx_target_url))

    try:
        await target_channel.send(embed=proof_embed, view=proof_view, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await ctx.send(f"Failed to send proof message: `{str(exc)[:300]}`")
        return

    if target_channel.id == ctx.channel.id:
        await ctx.send("Proof posted in this channel.")
    else:
        await ctx.send(f"Proof posted in {target_channel.mention}.")


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
async def on_ready():
    global slash_synced
    global payment_view_registered
    global backup_task_started

    enforce_runtime_safety()
    if not slash_synced:
        try:
            await bot.tree.sync()
        except Exception as exc:
            print(f"Slash sync failed: {exc}")
        slash_synced = True
    print("DOG AUTO MM BOT READY")
    if not payment_view_registered:
        try:
            bot.add_view(PaymentDetailsView())
            bot.add_view(RequestLTCView())
            bot.add_view(RequestUSDTBEP20View())
            bot.add_view(RequestUSDTETHView())
            bot.add_view(SparklesPanelView())
            payment_view_registered = True
        except Exception as exc:
            print(f"Persistent view registration failed: {exc}")

    if not backup_task_started:
        try:
            initial_backup = create_db_backup()
            print(f"Initial DB backup created: {initial_backup}")
        except Exception as exc:
            print(f"Initial DB backup failed: {exc}")
        bot.loop.create_task(backup_loop())
        backup_task_started = True

    bot.loop.create_task(resume_pending_monitors())


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
    await ctx.send("An unexpected error occurred while running that command.")

bot.run(TOKEN)
