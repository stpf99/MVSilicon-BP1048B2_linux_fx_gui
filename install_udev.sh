#!/usr/bin/env bash
# Instalacja reguły udev dla MVSilicon BP1048B2 (VID:PID 8888:1719)
# Dzięki temu program NIE wymaga ręcznego sudo chmod przy każdym podłączeniu.
set -euo pipefail

RULE_NAME="99-mvsilicon-bp1048.rules"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULE_SRC="$SCRIPT_DIR/$RULE_NAME"
RULE_DST="/etc/udev/rules.d/$RULE_NAME"

if [[ ! -f "$RULE_SRC" ]]; then
  echo "Brak pliku reguły: $RULE_SRC"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Uruchom z sudo:"
  echo "  sudo $0"
  exit 1
fi

cp -v "$RULE_SRC" "$RULE_DST"
chmod 644 "$RULE_DST"
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=8888 --attr-match=idProduct=1719 2>/dev/null || true

echo
echo "✓ Zainstalowano regułę: $RULE_DST"
echo "  Odłącz i podepnij urządzenie (lub zrestartuj), aby uprawnienia zadziałały na stałe."
echo
echo "Sprawdzenie (po podpięciu DSP):"
echo "  lsusb -d 8888:1719"
echo "  # uprawnienia powinny być crw-rw-rw- lub uaccess dla Twojej sesji"
