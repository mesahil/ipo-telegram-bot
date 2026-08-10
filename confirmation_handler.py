"""
Handles user confirmations for fuzzy matches via Telegram inline keyboards.
"""
import json
import asyncio
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fuzzy_matcher import FuzzyMatch


@dataclass
class PendingConfirmation:
    """Represents a pending user confirmation."""
    user_id: int
    chat_id: int
    original_query: str
    matches: list[FuzzyMatch]
    registrar: str
    pan: str
    callback_data: str
    timestamp: float


class ConfirmationHandler:
    """Handles fuzzy match confirmations via Telegram."""
    
    def __init__(self):
        """Initialize confirmation handler."""
        # Store pending confirmations: confirmation_id -> PendingConfirmation
        self.pending_confirmations: Dict[str, PendingConfirmation] = {}
        # Auto-cleanup after 5 minutes
        self.cleanup_timeout = 300
    
    def generate_confirmation_id(self, user_id: int, timestamp: float) -> str:
        """Generate unique confirmation ID."""
        return f"confirm_{user_id}_{int(timestamp)}"
    
    async def request_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 target: str, matches: list[FuzzyMatch], 
                                 registrar: str, pan: str) -> Optional[str]:
        """
        Send fuzzy match confirmation request to user.
        
        Args:
            update: Telegram update object
            context: Telegram context
            target: Original target company name
            matches: List of fuzzy matches found
            registrar: Registrar name
            pan: PAN number
            
        Returns:
            Confirmation ID if request sent successfully
        """
        if not matches:
            return None
        
        import time
        timestamp = time.time()
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        confirmation_id = self.generate_confirmation_id(user_id, timestamp)
        
        # Store pending confirmation
        self.pending_confirmations[confirmation_id] = PendingConfirmation(
            user_id=user_id,
            chat_id=chat_id,
            original_query=target,
            matches=matches,
            registrar=registrar,
            pan=pan,
            callback_data=confirmation_id,
            timestamp=timestamp
        )
        
        # Create message text
        message_lines = [
            f"🔍 *Exact Match Not Found*",
            f"",
            f"**Requested:** {target}",
            f"**Registrar:** {registrar.upper()}",
            f"",
            f"Select an available IPO on {registrar.upper()}:"
        ]
        
        # Create inline keyboard with match options
        keyboard = []
        for i, match in enumerate(matches):
            if match.confidence > 0:
                confidence_percent = int(match.confidence * 100)
                button_text = f"✅ {match.match} ({confidence_percent}%)"
            else:
                button_text = f"• {match.match}"
            callback_data = f"fuzz_confirm:{confirmation_id}:{i}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Add cancel option
        keyboard.append([InlineKeyboardButton("🔔 None of these (Subscribe for Auto-Polling)", callback_data=f"fuzz_cancel:{confirmation_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send confirmation message
        message_text = "\n".join(message_lines)
        await update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        # Schedule cleanup
        asyncio.create_task(self._cleanup_confirmation(confirmation_id))
        
        return confirmation_id
    
    async def handle_confirmation_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Handle user response to fuzzy match confirmation.
        
        Args:
            update: Telegram update object  
            context: Telegram context
            
        Returns:
            True if handled successfully
        """
        query = update.callback_query
        await query.answer()
        
        # Parse callback data
        parts = query.data.split(":", 2)
        if len(parts) < 2:
            return False
        
        action = parts[0]  # "fuzz_confirm" or "fuzz_cancel"
        confirmation_id = parts[1]
        
        # Get pending confirmation
        pending = self.pending_confirmations.get(confirmation_id)
        if not pending:
            await query.edit_message_text("⚠️ Confirmation expired or not found.")
            return False
        
        if action == "fuzz_cancel":
            # Save subscription for auto-polling in JSONBin
            from bot import add_allotment_subscription, get_pan_list
            pans = context.user_data.get('fuzzy_pans') or get_pan_list()
            ignored = [m.match for m in pending.matches]
            added = add_allotment_subscription(
                chat_id=pending.chat_id,
                ipo_name=pending.original_query,
                registrar=pending.registrar,
                pans=pans,
                ignored_matches=ignored
            )
            del self.pending_confirmations[confirmation_id]
            if added:
                await query.edit_message_text(
                    f"🔔 *Subscription Confirmed!*\n\n"
                    f"We will keep polling for matches for *'{pending.original_query}'* on {pending.registrar.upper()} and notify you as soon as a match or allotment status is found.",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text("❌ Failed to add subscription. Please try again.")
            return True
        
        elif action == "fuzz_confirm":
            if len(parts) < 3:
                return False
            
            try:
                match_index = int(parts[2])
                selected_match = pending.matches[match_index]
            except (ValueError, IndexError):
                await query.edit_message_text("⚠️ Invalid selection.")
                return False
            
            # Process the confirmed match
            await self._process_confirmed_match(query, context, pending, selected_match)
            
            # Clean up
            del self.pending_confirmations[confirmation_id]
            return True
        
        return False
    
    async def _process_confirmed_match(self, query, context: ContextTypes.DEFAULT_TYPE,
                                     pending: PendingConfirmation, selected_match: FuzzyMatch):
        """Process a confirmed fuzzy match."""
        # Import here to avoid circular imports
        from registrar_clients import get_client_for_registrar
        import httpx
        
        await query.edit_message_text("✅ Match confirmed! Fetching status...")
        
        # Get the registrar client
        client = get_client_for_registrar(pending.registrar)
        if not client:
            await query.edit_message_text(f"❌ Registrar '{pending.registrar}' not supported.")
            return
        
        # Get all PANs from context (stored during initial callback)
        pans = context.user_data.get('fuzzy_pans', [pending.pan])
        
        # Fetch status using the confirmed match for all PANs
        async with httpx.AsyncClient(timeout=20) as session:
            try:
                # Create tasks for all PANs using the confirmed match
                tasks = []
                for pan in pans:
                    # Check if client supports confirmed_match parameter
                    import inspect
                    sig = inspect.signature(client.status_by_pan)
                    if 'confirmed_match' in sig.parameters:
                        task = client.status_by_pan(
                            session, 
                            pan=pan, 
                            company_name=selected_match.match,
                            confirmed_match=selected_match.match
                        )
                    else:
                        task = client.status_by_pan(
                            session, 
                            pan=pan, 
                            company_name=selected_match.match
                        )
                    tasks.append(task)
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Format and send results for all PANs
                message_lines = [
                    f"📊 *Allotment Status*",
                    f"",
                    f"**IPO:** {selected_match.match}",
                    f"**Registrar:** {pending.registrar.upper()}",
                    f""
                ]
                
                for pan, result in zip(pans, results):
                    if isinstance(result, Exception):
                        message_lines.append(f"{pan}  –  error fetching status")
                    else:
                        message_lines.append(f"{pan}  –  {result}")
                
                await query.edit_message_text(
                    text="\n".join(message_lines),
                    parse_mode="Markdown"
                )
                
            except Exception as e:
                await query.edit_message_text(f"❌ Error fetching status: {str(e)}")
    
    async def _cleanup_confirmation(self, confirmation_id: str):
        """Clean up expired confirmation after timeout."""
        await asyncio.sleep(self.cleanup_timeout)
        
        if confirmation_id in self.pending_confirmations:
            del self.pending_confirmations[confirmation_id]


# Global instance
confirmation_handler = ConfirmationHandler()
