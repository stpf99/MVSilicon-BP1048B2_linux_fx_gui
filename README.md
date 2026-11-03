# MVSilicon BP1048B2 — Linux ACP FX GUI (Qt)

Sterowanie efektami DSP płytki **BP1048B2** (VID:PID `8888:1719`) przez USB HID (iface 4).  
Audio (iface 0–3) **nie jest ruszane**.

## Szybki start

```bash
# zależności
sudo apt install python3-pyqt5 libusb-1.0-0-dev build-essential
pip3 install pyusb --break-system-packages   # lub w venv

# uprawnienia USB na stałe (jednorazowo)
sudo ./install_udev.sh
# potem odłącz i podepnij płytkę

# kompilacja helperów (robione też automatycznie przy starcie GUI)
gcc -O2 -o acp_send  acp_send.c  -lusb-1.0
gcc -O2 -o acp_query acp_query.c -lusb-1.0

# uruchomienie
python3 acp_gui_qt2.py
```

## Co zostało naprawione / dodane

1. **Brak `acp_query`** — dodany `acp_query.c` + automatyczna kompilacja w GUI.  
   Program wcześniej zgłaszał „Brak acp_query”.

2. **Uprawnienia USB** — reguła udev `99-mvsilicon-bp1048.rules` + skrypt `install_udev.sh`.  
   Po instalacji nie trzeba już ręcznie `sudo chmod a+rw /dev/bus/usb/...` przy każdym podłączeniu.

3. GUI jest specjalizowane pod **BP1048B2** (nie uniwersalny ACP Workbench).  
   Obecnie ma suwaki efektów (mic / music / gain / presety).  
   **EQ 10-pasmowy** — nowa zakładka «EQ 10-band»:
  - Music Pre EQ (0xA2) i Music Out EQ (0xA3)
  - 10 filtrów: Peak / Low·High Shelf / LP / HP / BP / Notch
  - f0, Q, gain (±12 dB), enable per pasmo + pregain
  - podgląd krzywej magnitude (log-f) + tryb Live (debounce)
  - format ramek zgodny z `ZK_DAM_DEFAULT.ini` / ACP Workbench

## Autokalibracja (pomysł)

Płytka ma **Line In + Line Out**. Można:

- puścić sygnał testowy (sweep / pink noise) na Line Out / głośnik,
- nagrać go mikrofonem (lub przez Line In jeśli zapętlone),
- policzyć krzywą odpowiedzi i automatycznie ustawić filtry EQ.

To wymaga dodatkowej warstwy audio (PortAudio / Pulse / ALSA) + analizy FFT — na razie niezaimplementowane, ale infrastruktura Line In/Out na płytce na to pozwala (zob. też materiały Sure Electronics).

## Pliki

| Plik | Opis |
|------|------|
| `acp_gui_qt2.py` | Główne GUI (PyQt5) |
| `acp_send.c` / `acp_send` | Wysyłanie ramek ACP |
| `acp_query.c` | Odczyt stanu (fw / cpu / surowy moduł) — **nowy** |
| `99-mvsilicon-bp1048.rules` | Reguła udev |
| `install_udev.sh` | Instalator reguły |
| `ZK_DAM_DEFAULT.ini` | Domyślna konfiguracja z ACP Workbench |
| `gnome/` | Opcjonalny aplet GNOME + daemon D-Bus (osobny stack) |

## Uwagi

- Claim tylko **iface 4** — odtwarzanie/nagrywanie USB Audio działa równolegle.
- Po `install_udev.sh` wystarczy odpiąć/wpiąć urządzenie.
- Jeśli pyusb nie widzi urządzenia mimo reguły — sprawdź grupę użytkownika (`plugdev` / `uaccess`) i czy nie działa już inny proces trzymający iface 4 (np. `acpd.py` z katalogu gnome).
