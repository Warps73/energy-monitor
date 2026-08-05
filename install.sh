#!/bin/bash
set -e

PROJ="/home/openclaw/projects/energy-monitor"
USER="openclaw"

echo "=== Energy Monitor — Installation ==="

# 1. Python dependencies
cd "$PROJ"
pip install -r requirements.txt --quiet
pip install pytz --quiet

# 2. First collector run to create the DB
python3 collector.py && echo "✓ Collector OK"

# 3. FastAPI service
sudo tee /etc/systemd/system/energy-server.service > /dev/null <<EOF
[Unit]
Description=Energy Monitor Server
After=network.target

[Service]
User=$USER
WorkingDirectory=$PROJ
ExecStart=/usr/bin/python3 $PROJ/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4. Collector service (one-shot)
sudo tee /etc/systemd/system/energy-collector.service > /dev/null <<EOF
[Unit]
Description=Energy Collector (one-shot)

[Service]
User=$USER
WorkingDirectory=$PROJ
ExecStart=/usr/bin/python3 $PROJ/collector.py
EOF

# 5. 15-minute timer
sudo tee /etc/systemd/system/energy-collector.timer > /dev/null <<EOF
[Unit]
Description=Energy Collector — every 15 min

[Timer]
OnBootSec=1min
OnUnitActiveSec=15min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

# 6. Enable everything
sudo systemctl daemon-reload
sudo systemctl enable --now energy-server.service
sudo systemctl enable --now energy-collector.timer
sudo systemctl start energy-collector.service

echo ""
echo "✓ energy-server  : http://localhost:8000"
echo "✓ energy-collector timer active (every 15 min)"
echo ""
echo "=== Cloudflare Tunnel ==="
echo "1. curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb"
echo "2. sudo dpkg -i /tmp/cloudflared.deb"
echo "3. cloudflared tunnel --url http://localhost:8000"
echo "   → public URL printed in the terminal"
echo ""
echo "For a permanent tunnel (after cloudflare.com login):"
echo "   cloudflared login"
echo "   cloudflared tunnel create energy"
echo "   cloudflared tunnel route dns energy ton-domaine.fr"
