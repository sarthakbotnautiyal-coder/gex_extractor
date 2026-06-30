# GEX Extractor

Discord GEX (Gamma Exposure) polling service for SPX options.

## Features

- Polls Discord `gex.bot` channel for gamma exposure snapshots
- Saves GEX data to local SQLite database
- Optional dual-write to Supabase cloud (best-effort with dead-letter queue)
- Automatic market hours detection (9:30 AM - 4:00 PM ET)

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

1. Copy `.env.example` to `.env` and fill in your Discord token:
   ```bash
   cp .env.example .env
   ```

2. (Optional) Add Supabase credentials for cloud sync:
   ```
   SUPABASE_URL=your_url
   SUPABASE_SECRET_KEY=your_key
   ```

## Usage

```bash
python run.py
```

Logs are written to `logs/gex_extractor.log` and GEX data is stored in `data/gex.db`.

## Cron / lifecycle (production)

Production cadence is **single-instruction** (watchdog-only). The shared
`extractor-watchdog.sh` (also covers `premium_extractor`) fires every 5
minutes during the trading day and is responsible for **both** cold-start at
market open and post-crash recovery. There is no separate START line — only
one cron instruction can ever start the extractor, which makes the lifecycle
structurally race-free.

> See [`docs/CRON.md`](docs/CRON.md) for the canonical crontab reference,
> cold-start timing analysis, and the one-time host crontab edit command.
> The crontab itself lives on the host, not in this repo.

## Database

The GEX snapshots are stored in SQLite with the following schema:
- `timestamp`: Unique snapshot timestamp (YYYY-MM-DD HH:MM:SS)
- `gex_by_oi`: GEX by open interest
- `gex_by_volume`: GEX by volume
- `spot`: SPX spot price
- `major_negative_by_volume`: Major negative GEX by volume
- `major_positive_by_volume`: Major positive GEX by volume
- `major_negative_by_oi`: Major negative GEX by OI
- `major_positive_by_oi`: Major positive GEX by OI
- `zero_gamma`: Zero gamma distance

## Architecture

- `src/discord_monitor.py`: Main polling loop and Discord API client
- `src/db_writer.py`: SQLite database operations
- `src/supabase_writer.py`: Supabase dual-write (best-effort)
- `src/log_setup.py`: Logging configuration