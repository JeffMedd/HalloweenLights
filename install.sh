#!/usr/bin/env bash
# Install the Halloween window display on a Raspberry Pi 5 running Pi OS Bookworm.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-pip \
    libsdl2-mixer-2.0-0 libsdl2-2.0-0 python3-lgpio python3-numpy python3-pygame

echo "==> Creating virtual environment"
python3 -m venv --system-site-packages "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip wheel
"$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt"

echo "==> Creating directories"
mkdir -p "$HERE/sounds" "$HERE/data"

if [ ! -f "$HERE/config.json" ]; then
    echo "==> Writing a starter config"
    cp "$HERE/config.example.json" "$HERE/config.json"
fi

if [ -z "$(ls -A "$HERE/sounds" 2>/dev/null)" ]; then
    echo "==> Generating placeholder sounds"
    "$HERE/.venv/bin/python" "$HERE/tools/make_sounds.py" --out "$HERE/sounds"
    "$HERE/.venv/bin/python" "$HERE/tools/apply_default_sounds.py" \
        --config "$HERE/config.json" --sounds "$HERE/sounds"
fi

echo "==> Installing the service"
sed -e "s|__DIR__|$HERE|g" -e "s|__USER__|$USER_NAME|g" \
    "$HERE/systemd/halloween-display.service" \
    | sudo tee /etc/systemd/system/halloween-display.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now halloween-display.service

sleep 2
sudo systemctl --no-pager --lines=15 status halloween-display.service || true

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Done. Web interface: http://${IP}:8080"
echo "Logs:  journalctl -u halloween-display -f"
