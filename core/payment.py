"""
Payment Processing System
Clean payment tracking, confirmation, and fund management
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import discord

from crypto import (
    generate_ltc_wallet, generate_bep20_wallet, generate_cashapp_wallet, generate_paypal_wallet,
    detect_ltc_payment, detect_usdt_payment, send_ltc, send_usdt,
    usd_to_ltc, decrypt_key
)
from .state import DealState, StateManager
from .ui import UIComponents, EmbedBuilder, ViewTemplates


class PaymentStatus(Enum):
    """Payment status tracking"""
    PENDING = "pending"
    DETECTED = "detected"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class PaymentInfo:
    """Payment information structure"""
    ticket_id: int
    crypto: str
    amount_usd: float
    amount_crypto: Optional[float] = None
    address: Optional[str] = None
    encrypted_private: Optional[str] = None
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: datetime = None
    detected_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    txid: Optional[str] = None
    confirmations: int = 0
    required_confirmations: int = 1
    retry_count: int = 0
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


class PaymentProcessor:
    """Payment processing and wallet generation"""
    
    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.active_payments: Dict[int, PaymentInfo] = {}
    
    def get_required_confirmations(self, amount_usd: float) -> int:
        """Get required confirmations based on deal value"""
        if amount_usd < 100:
            return 1
        elif amount_usd <= 500:
            return 2
        else:
            return 3
    
    async def create_payment(self, ticket_id: int, crypto: str, amount_usd: float) -> PaymentInfo:
        """Create new payment with wallet generation"""
        payment_info = PaymentInfo(
            ticket_id=ticket_id,
            crypto=crypto.upper(),
            amount_usd=amount_usd
        )
        
        # Generate wallet based on crypto type
        if crypto.upper() == "LTC":
            wallet = generate_ltc_wallet()
            payment_info.address = wallet["address"]
            payment_info.encrypted_private = wallet["private"]
            payment_info.amount_crypto = usd_to_ltc(amount_usd)
            
        elif crypto.upper() in ["USDT", "USDT_BEP20"]:
            wallet = generate_bep20_wallet()
            payment_info.address = wallet["address"]
            payment_info.encrypted_private = wallet["private"]
            payment_info.amount_crypto = amount_usd  # USDT is stablecoin
            
        elif crypto.upper() in ["USDT_ETH"]:
            wallet = generate_bep20_wallet()  # Same generation method
            payment_info.address = wallet["address"]
            payment_info.encrypted_private = wallet["private"]
            payment_info.amount_crypto = amount_usd
            
        elif crypto.upper() == "CASHAPP":
            wallet = generate_cashapp_wallet()
            payment_info.address = wallet["address"]
            payment_info.amount_crypto = amount_usd
            
        elif crypto.upper() == "PAYPAL":
            wallet = generate_paypal_wallet()
            payment_info.address = wallet["address"]
            payment_info.amount_crypto = amount_usd
            
        else:
            raise ValueError(f"Unsupported crypto: {crypto}")
        
        # Set required confirmations
        payment_info.required_confirmations = self.get_required_confirmations(amount_usd)
        
        # Store payment info
        self.active_payments[ticket_id] = payment_info
        
        return payment_info
    
    async def release_funds(self, ticket_id: int, seller_address: str) -> Tuple[bool, Optional[str]]:
        """Release funds to seller"""
        payment_info = self.active_payments.get(ticket_id)
        if not payment_info:
            return False, "Payment not found"
        
        if payment_info.status != PaymentStatus.CONFIRMED:
            return False, "Payment not confirmed"
        
        try:
            # Decrypt private key
            private_key = decrypt_key(payment_info.encrypted_private)
            
            # Calculate payout amount (minus fees if applicable)
            payout_amount = self._calculate_payout(payment_info)
            
            # Send funds based on crypto type
            if payment_info.crypto == "LTC":
                tx_result = send_ltc(seller_address, payout_amount, private_key)
            elif payment_info.crypto in ["USDT", "USDT_BEP20", "USDT_ETH"]:
                network = "ETH" if payment_info.crypto == "USDT_ETH" else "BEP20"
                tx_result = send_usdt(seller_address, payout_amount, private_key, network=network)
            else:
                return False, "Auto-release not supported for this payment method"
            
            # Extract transaction ID
            txid = self._extract_txid(tx_result)
            if txid:
                payment_info.txid = txid
                return True, txid
            else:
                return False, "Failed to send funds"
                
        except Exception as e:
            return False, f"Release failed: {str(e)}"
    
    def _calculate_payout(self, payment_info: PaymentInfo) -> float:
        """Calculate payout amount after fees"""
        # For now, return full amount (fees handled elsewhere)
        return payment_info.amount_crypto or 0
    
    def _extract_txid(self, tx_result: Any) -> Optional[str]:
        """Extract transaction ID from result"""
        if isinstance(tx_result, str):
            return tx_result
        elif isinstance(tx_result, dict):
            return tx_result.get("tx_hash") or tx_result.get("hash") or tx_result.get("txid")
        return None


class PaymentTracker:
    """Payment monitoring and confirmation tracking"""
    
    def __init__(self, processor: PaymentProcessor, state_manager: StateManager):
        self.processor = processor
        self.state_manager = state_manager
        self.monitoring_tasks: Dict[int, asyncio.Task] = {}
    
    async def start_monitoring(self, ticket_id: int):
        """Start monitoring payment for ticket"""
        if ticket_id in self.monitoring_tasks:
            return  # Already monitoring
        
        task = asyncio.create_task(self._monitor_payment(ticket_id))
        self.monitoring_tasks[ticket_id] = task
    
    async def stop_monitoring(self, ticket_id: int):
        """Stop monitoring payment for ticket"""
        if ticket_id in self.monitoring_tasks:
            self.monitoring_tasks[ticket_id].cancel()
            del self.monitoring_tasks[ticket_id]
    
    async def _monitor_payment(self, ticket_id: int):
        """Monitor payment status continuously"""
        payment_info = self.processor.active_payments.get(ticket_id)
        if not payment_info:
            return
        
        max_attempts = 60  # Monitor for 20 minutes (60 * 20 seconds)
        attempt = 0
        
        while attempt < max_attempts:
            try:
                # Check payment status
                status_changed = await self._check_payment_status(payment_info)
                
                if status_changed:
                    # Handle status change
                    await self._handle_status_change(payment_info)
                    
                    # Stop monitoring if payment is confirmed or failed
                    if payment_info.status in [PaymentStatus.CONFIRMED, PaymentStatus.FAILED, PaymentStatus.EXPIRED]:
                        break
                
                # Wait before next check
                await asyncio.sleep(20)
                attempt += 1
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error monitoring payment {ticket_id}: {e}")
                await asyncio.sleep(20)
                attempt += 1
        
        # Cleanup
        await self.stop_monitoring(ticket_id)
    
    async def _check_payment_status(self, payment_info: PaymentInfo) -> bool:
        """Check current payment status"""
        old_status = payment_info.status
        
        try:
            if payment_info.crypto == "LTC":
                paid, confirmed, txid, amount, is_confirmed = detect_ltc_payment(
                    payment_info.address,
                    payment_info.amount_usd,
                    required_ltc=payment_info.amount_crypto,
                    required_confirmations=payment_info.required_confirmations
                )
                
            elif payment_info.crypto in ["USDT", "USDT_BEP20", "USDT_ETH"]:
                network = "ETH" if payment_info.crypto == "USDT_ETH" else "BEP20"
                paid, confirmed, txid, amount = detect_usdt_payment(
                    payment_info.address,
                    payment_info.amount_usd,
                    network=network
                )
                
            else:
                # For Cash App and PayPal, payment is manually confirmed
                return False
            
            if paid:
                # Update confirmation count
                payment_info.confirmations = confirmed
                payment_info.txid = txid
                payment_info.amount_crypto = amount
                
                if is_confirmed:
                    payment_info.status = PaymentStatus.CONFIRMED
                    payment_info.confirmed_at = datetime.now(timezone.utc)
                else:
                    payment_info.status = PaymentStatus.CONFIRMING
                    if payment_info.status != old_status:
                        payment_info.detected_at = datetime.now(timezone.utc)
            
        except Exception as e:
            print(f"Error checking payment status: {e}")
            return False
        
        return payment_info.status != old_status
    
    async def _handle_status_change(self, payment_info: PaymentInfo):
        """Handle payment status changes"""
        ticket_id = payment_info.ticket_id
        
        if payment_info.status == PaymentStatus.CONFIRMING:
            # Payment detected, move to confirming state
            await self.state_manager.transition_deal(ticket_id, DealState.PAYMENT_DETECTED)
            
        elif payment_info.status == PaymentStatus.CONFIRMED:
            # Payment confirmed, move to funded state
            await self.state_manager.transition_deal(ticket_id, DealState.PAYMENT_CONFIRMED)
            await self.state_manager.transition_deal(ticket_id, DealState.FUNDED)
    
    async def manually_confirm_payment(self, ticket_id: int, user_id: int) -> Tuple[bool, str]:
        """Manually confirm payment (for Cash App/PayPal)"""
        payment_info = self.processor.active_payments.get(ticket_id)
        if not payment_info:
            return False, "Payment not found"
        
        if payment_info.status == PaymentStatus.CONFIRMED:
            return False, "Payment already confirmed"
        
        # Update payment status
        payment_info.status = PaymentStatus.CONFIRMED
        payment_info.confirmed_at = datetime.now(timezone.utc)
        payment_info.txid = f"manual_{int(time.time())}_{user_id}"
        
        # Update state
        await self.state_manager.transition_deal(ticket_id, DealState.PAYMENT_CONFIRMED)
        await self.state_manager.transition_deal(ticket_id, DealState.FUNDED)
        
        return True, "Payment confirmed manually"
    
    async def get_payment_status(self, ticket_id: int) -> Optional[PaymentInfo]:
        """Get current payment status"""
        return self.processor.active_payments.get(ticket_id)
    
    async def cleanup_payment(self, ticket_id: int):
        """Clean up payment data"""
        await self.stop_monitoring(ticket_id)
        self.processor.active_payments.pop(ticket_id, None)


class PaymentUI:
    """UI components for payment flows"""
    
    @staticmethod
    async def send_payment_request(channel: discord.TextChannel, payment_info: PaymentInfo):
        """Send payment request embed"""
        crypto_display = payment_info.crypto.replace("_", " ")
        color = UIHelper.get_crypto_color(payment_info.crypto)
        
        embed = EmbedBuilder.payment_required(
            amount_usd=payment_info.amount_usd,
            address=payment_info.address or "N/A",
            crypto=crypto_display,
            color=color
        )
        
        # Add additional fields based on crypto type
        if payment_info.crypto == "LTC" and payment_info.amount_crypto:
            embed.add_field(
                name="LTC Amount",
                value=f"{payment_info.amount_crypto:.8f}".rstrip("0").rstrip("."),
                inline=True
            )
        
        await channel.send(embed=embed)
    
    @staticmethod
    async def send_payment_detected(channel: discord.TextChannel, payment_info: PaymentInfo):
        """Send payment detected notification"""
        embed = EmbedBuilder.payment_detected(
            amount=payment_info.amount_crypto or 0,
            crypto=payment_info.crypto.replace("_", " "),
            confirmations=payment_info.confirmations
        )
        
        # Add confirmation progress
        if payment_info.crypto == "LTC":
            progress = f"{payment_info.confirmations}/{payment_info.required_confirmations}"
            embed.add_field(name="Confirmations", value=progress, inline=True)
            
            # Add security note based on deal value
            if payment_info.required_confirmations > 1:
                security_level = "Enhanced" if payment_info.required_confirmations == 3 else "Standard"
                embed.add_field(name="Security Level", value=security_level, inline=True)
        
        if payment_info.txid:
            embed.add_field(name="Transaction ID", value=f"`{payment_info.txid[:16]}...`", inline=False)
        
        await channel.send(embed=embed)
    
    @staticmethod
    async def send_payment_confirmed(channel: discord.TextChannel, payment_info: PaymentInfo):
        """Send payment confirmed notification"""
        embed = EmbedBuilder.payment_confirmed(
            amount=payment_info.amount_crypto or 0,
            crypto=payment_info.crypto.replace("_", " ")
        )
        
        # Add confirmation details
        if payment_info.crypto == "LTC":
            embed.add_field(name="Confirmations", value=f"{payment_info.confirmations}/{payment_info.required_confirmations}", inline=True)
            
            # Add security completion message
            if payment_info.required_confirmations > 1:
                embed.add_field(name="Security", value="✅ Enhanced verification complete", inline=True)
        
        if payment_info.txid:
            embed.add_field(name="Transaction ID", value=f"`{payment_info.txid[:16]}...`", inline=False)
        
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
