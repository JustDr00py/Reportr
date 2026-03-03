# Reportr — VPS Deployment Guide

## Architecture

- **API**: runs 24/7 as a `systemd` service (`--api-only`)
- **TUI**: SSH in and run `--tui-only` whenever you want to check it

---

## 1. Set Up the VPS

```bash
# Clone/copy your project onto the VPS
git clone <your-repo> /opt/reportr
cd /opt/reportr
pip install -r requirements.txt
```

Or copy with `scp -r ./Reportr user@vps:/opt/reportr`.

---

## 2. Create a systemd Service

```bash
sudo nano /etc/systemd/system/reportr.service
```

```ini
[Unit]
Description=Reportr API
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/opt/reportr
ExecStart=/usr/bin/python3 run.py --api-only
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable reportr
sudo systemctl start reportr
sudo systemctl status reportr   # verify it's running
```

---

## 3. View the TUI On Demand

SSH into the VPS and run:

```bash
cd /opt/reportr
python run.py --tui-only
```

The TUI connects to the locally running API and renders in your terminal. Quit with `q` — the API keeps running.

---

## 4. Firewall

If the API only needs to be accessed locally (by the TUI via `localhost`), don't expose port 8000 publicly:

```bash
# Block external access to port 8000 (ufw example)
sudo ufw deny 8000
```

If external systems need to POST data to `/api/ingest` (e.g. meter devices), open it selectively:

```bash
sudo ufw allow from <meter-device-ip> to any port 8000
```

---

## Quick Reference

| Task | Command |
|---|---|
| Start API service | `sudo systemctl start reportr` |
| Stop API service | `sudo systemctl stop reportr` |
| Restart after code changes | `sudo systemctl restart reportr` |
| View API logs | `journalctl -u reportr -f` |
| Open TUI | `python run.py --tui-only` |
| Keep TUI alive after disconnect | Run inside `tmux` or `screen` |
