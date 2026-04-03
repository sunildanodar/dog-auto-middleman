# Dog Auto Middleman 24/7 Hosting

## Required Environment Setup

Create a .env file in the project folder using .env.example as a template.

Minimum required values:

- DISCORD_TOKEN
- BLOCKCYPHER_TOKEN
- ENCRYPTION_KEY
- ADMIN_ID

Strongly recommended for production safety:

- DB_BACKUP_DIR (default: db_backups)
- DB_BACKUP_INTERVAL_MINUTES (default: 30)
- DB_BACKUP_RETENTION_DAYS (default: 14)
- DB_BACKUP_MAX_FILES (default: 300)
- BACKUP_EXPORT_DIR (default: db_backups/exports)
- BACKUP_EXPORT_MAX_FILES (default: 120)
- BACKUP_ALERT_MAX_AGE_MINUTES (default: 120)
- BACKUP_STARTUP_MAX_AGE_MINUTES (default: 0, disabled)
- BACKUP_ENCRYPTION_KEY (required for encrypted export command)
- STRICT_KEY_FINGERPRINT=true
- REQUIRE_PERSISTENT_DB=true (enable this when using cloud hosting)

You can generate an ENCRYPTION_KEY with:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Option 1: Keep It Running On Your Windows PC

Run this from PowerShell:

```powershell
cd C:\Users\ireac\Downloads\sparkles_real_final
powershell -ExecutionPolicy Bypass -File .\run_bot_24_7.ps1
```

This will:

- start `bot.py`
- write logs into the `logs` folder
- restart the bot automatically if it crashes

## Option 2: Start It Automatically When Windows Boots

Run PowerShell as administrator, then:

```powershell
cd C:\Users\ireac\Downloads\sparkles_real_final
powershell -ExecutionPolicy Bypass -File .\install_startup_task.ps1
```

This creates a scheduled task called `DogAutoMiddlemanBot`.

To start the task manually:

```powershell
Start-ScheduledTask -TaskName DogAutoMiddlemanBot
```

To stop it:

```powershell
Stop-ScheduledTask -TaskName DogAutoMiddlemanBot
```

To remove it:

```powershell
Unregister-ScheduledTask -TaskName DogAutoMiddlemanBot -Confirm:$false
```

## Option 3: Railway (Cloud Hosting)

This repo is now prepared for Railway with:

- `Procfile`
- `runtime.txt`
- `railway.json`

### Deploy Steps

1. Push this project to GitHub (do not upload your `.env` file).
2. In Railway, create a new project from your GitHub repo.
3. Railway will install from `requirements.txt` and run `python bot.py`.
4. Add these required environment variables in Railway:
	- `DISCORD_TOKEN`
	- `BLOCKCYPHER_TOKEN`
	- `ENCRYPTION_KEY`
	- `ADMIN_ID`
	- `LOG_CHANNEL_ID` (if used)
	- `TICKET_CATEGORY_ID` (if used)
	- `MASTER_PRIVATE_KEY` (if withdrawals are enabled)
	- `MASTER_ADDRESS` (if withdrawals are enabled)
5. Deploy and check logs for `DOG AUTO MM BOT READY`.

### Railway Notes

- Free/trial credits are limited and can run out.
- If credits run out, the service stops until billing is added.
- SQLite (`data.db`) on cloud instances can be non-persistent across restarts/redeploys.
- For production reliability, move DB storage to a persistent database service later.
- If you keep SQLite, mount persistent storage and point `DB_NAME` to that mounted path.
- Set `REQUIRE_PERSISTENT_DB=true` in cloud production to block unsafe startup.

## Security Hardening Included

- Startup encryption-key fingerprint lock (prevents using the wrong ENCRYPTION_KEY with an existing DB).
- Automatic rotating SQLite backups (created on startup and on interval).
- Backup retention cleanup by age and max-file count.
- Backup stale-age alerting (security alerts in logs channel).
- Optional startup block when backup age is too old (`BACKUP_STARTUP_MAX_AGE_MINUTES`).
- Encrypted backup exports for offsite storage (`!backup_export`).
- Admin safety commands:
	- `!backup_now` (create immediate backup)
	- `!security_status` (shows DB/backup/key safety state)
	- `!backup_export` (creates encrypted backup export + checksum)

Generate backup encryption key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Logs

Logs are written to:

```text
logs\bot_YYYY-MM-DD_HH-mm-ss.log
```

## Important

- If your PC turns off, the bot goes offline until Windows starts again.
- For real 24/7 uptime, move this project to a VPS later.
- Keep `data.db`, your encryption key, and your config backed up.