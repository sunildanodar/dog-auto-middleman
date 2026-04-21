# Dog Auto Middleman V2 Migration Guide

## Overview
Your bot has been completely rebuilt with a clean, modular architecture that matches Dog's quality and user experience. This guide will help you migrate to the new system.

## What's New

### Architecture Improvements
- **Modular Design**: Split into clean, focused modules (state, ui, ticket, payment, admin)
- **State Management**: Proper deal state machine with transitions and validation
- **Premium UI**: Modern Discord UI with consistent branding and smooth flows
- **Clean Code**: Separated concerns, better error handling, scalable design

### User Experience Upgrades
- **Smooth Flow**: Step-by-step guided deal creation
- **Modern UI**: Premium embeds, buttons, and modals
- **Better Feedback**: Clear status updates and progress indicators
- **Responsive Interactions**: Fast, intuitive user interactions

### Admin Features
- **Comprehensive Panel**: Full admin control system
- **Action Logging**: Complete audit trail of admin actions
- **System Stats**: Real-time monitoring and statistics
- **Advanced Controls**: Force release, cancel, refund capabilities

## Migration Steps

### 1. Backup Current System
```bash
# Backup your current bot files
cp bot.py bot_backup.py
cp -r . bot_backup/
```

### 2. Update Environment Variables
Ensure your `.env` file has all required variables:
```env
DISCORD_TOKEN=your_bot_token
BLOCKCYPHER_TOKEN=your_blockcypher_token
ENCRYPTION_KEY=your_encryption_key
ADMIN_ID=your_admin_user_id
TICKET_CATEGORY_ID=your_ticket_category_id
LOG_CHANNEL_ID=your_log_channel_id
PROOF_CHANNEL_ID=your_proof_channel_id
CONFIRMATIONS_REQUIRED=1
```

### 3. Install Dependencies
```bash
pip install discord.py web3 cryptography requests
```

### 4. Deploy New Bot
Replace your current bot with the new version:

```bash
# Use the new bot file
python bot_v2.py
```

### 5. Test Core Features
Test these features before going live:

1. **Panel Command**: `/panel` - Shows main interface
2. **Ticket Creation**: Start a new trade
3. **Role Selection**: Assign buyer/seller roles
4. **Payment Processing**: Test payment detection
5. **Fund Release**: Test fund release flow
6. **Admin Panel**: `/admin` - Test admin controls

## Key Differences

### Command Changes
- **Old**: `!panel` (prefix command)
- **New**: `/panel` (slash command)

### UI Improvements
- **Old**: Basic embeds with inconsistent styling
- **New**: Premium embeds with consistent branding and modern design

### State Management
- **Old**: Manual status tracking in database
- **New**: Proper state machine with validation and transitions

### Error Handling
- **Old**: Basic try/catch blocks
- **New**: Comprehensive error handling with user-friendly messages

## Configuration

### Required Environment Variables
```env
# Core Bot Settings
DISCORD_TOKEN=your_discord_bot_token
ADMIN_ID=your_discord_user_id

# Crypto Settings
BLOCKCYPHER_TOKEN=your_blockcypher_api_token
ENCRYPTION_KEY=your_encryption_key

# Channel Settings
TICKET_CATEGORY_ID=your_ticket_category_id
LOG_CHANNEL_ID=your_log_channel_id
PROOF_CHANNEL_ID=your_proof_channel_id

# Payment Settings
CONFIRMATIONS_REQUIRED=1
```

### Optional Settings
```env
# Database
DB_NAME=data.db
DB_BACKUP_DIR=db_backups

# Performance
PAYMENT_POLL_INTERVAL_SECONDS=20
PAYMENT_TIMEOUT_MINUTES=20
```

## New Features

### State Machine
The bot now uses a proper state machine for deal management:
- `CREATED` -> `WAITING_ROLES` -> `WAITING_AMOUNT` -> `WAITING_PAYMENT`
- `PAYMENT_DETECTED` -> `PAYMENT_CONFIRMED` -> `FUNDED`
- `COMPLETED` / `DISPUTED` / `CANCELLED` / `EXPIRED`

### Premium UI Components
- Consistent color scheme and branding
- Modern embed designs with thumbnails
- Interactive buttons with emojis
- Smooth modal flows

### Admin Dashboard
- Real-time system statistics
- Active deal monitoring
- Admin action logging
- Advanced control options

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure all modules are in the `core/` directory
2. **Database Issues**: The new system uses your existing database
3. **Permission Errors**: Check bot has proper Discord permissions
4. **State Issues**: Restart bot to clear state if needed

### Debug Mode
Enable debug logging by setting:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Performance Improvements

### Optimizations
- **Async Operations**: All I/O operations are async
- **State Cleanup**: Automatic cleanup of completed deals
- **Memory Management**: Efficient data structures
- **Background Tasks**: Non-blocking payment monitoring

### Scaling
- **Modular Architecture**: Easy to add new features
- **Clean Separation**: Independent components
- **Database Optimization**: Efficient queries
- **Resource Management**: Proper cleanup

## Support

### Getting Help
1. Check this guide first
2. Review error logs
3. Test in development environment
4. Check Discord permissions

### Feature Requests
The new modular architecture makes it easy to add:
- New payment methods
- Additional admin features
- Custom UI components
- Advanced state transitions

## Deployment

### Railway/Heroku
1. Update your `Procfile`:
```
web: python bot_v2.py
```

2. Update dependencies in `requirements.txt`:
```
discord.py
web3
cryptography
requests
python-dotenv
```

### Docker
Update your Dockerfile to use `bot_v2.py` as the entry point.

## Rollback Plan

If you need to rollback:
1. Stop the new bot
2. Restore `bot.py` from backup
3. Update your deployment to use the old version
4. Restart with original configuration

## Next Steps

1. **Test Thoroughly**: Test all features before going live
2. **Monitor Performance**: Watch for any issues
3. **Gather Feedback**: Get user feedback on new UI
4. **Iterate**: Make improvements based on usage

Your bot now has the same quality and user experience as top-tier escrow services like Dog!
