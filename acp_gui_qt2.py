#!/usr/bin/env python3
"""
acp_gui_qt2.py — ACP DSP Workbench (PyQt5)
MVSilicon BP1048B2 — sterowanie przez pyusb / acp_send

Wymagania:
    pip install PyQt5 pyusb
    sudo chmod a+rw /dev/bus/usb/*/*
"""

import os, sys, struct, time, json, copy, re as _re, queue as _queue
import subprocess
try:
    import usb.core as _usb_core, usb.util as _usb_util
    _HAS_PYUSB = True
except ImportError:
    _usb_core = None; _usb_util = None; _HAS_PYUSB = False
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTextEdit, QFileDialog, QProgressBar, QTabWidget, QGroupBox,
    QCheckBox, QSlider, QFrame, QScrollArea,
    QTreeWidget, QTreeWidgetItem, QSizePolicy,
    QMessageBox, QStatusBar, QInputDialog, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette, QTextCursor

# ─────────────────────────────────────────────────────────────────────────────
# Protokół ACP
# ─────────────────────────────────────────────────────────────────────────────

SYNC_HEAD = bytes([0xA5, 0x5A])
SYNC_TAIL = 0x16
ALL_PARAM = 0xFF

MODULE = {
    # ── Hardware / System ──
    "FIRMWARE":0x00, "SYSTEM":0x01,
    "PGA0":0x03, "ADC0":0x04, "PGA1":0x06, "ADC1":0x07,
    "AGC1":0x08, "DAC0":0x09, "DAC1":0x0A,
    "I2S0":0x0B, "I2S1":0x0C, "SPDIF":0x0D,
    "USER_TAG":0xFC, "SAVE_FLASH":0xFD,
    # ── Music effects (ID zweryfikowane dla firmware HunXiang_F) ──
    "MUSIC_NOISE":     0x81,
    "MUSIC_PITCH":     0x94,
    "MUSIC_VCUT":      0x95,
    "MUSIC_VREMOVE":   0x96,
    "MUSIC_3D":        0x97,
    "MUSIC_3DPLUS":    0x98,
    "MUSIC_VBASS":     0x99,
    "MUSIC_VBASS_CLS": 0x9A,
    "MUSIC_STEREO":    0x9B,
    "MUSIC_DELAY":     0x9C,
    "MUSIC_EXCITER":   0x9D,
    "MUSIC_PHASE":     0x9E,
    "MUSIC_DRC":       0x9F,
    "MUSIC_PRE_EQ":    0xA2,
    "MUSIC_OUT_EQ":    0xA3,
    "MUSIC_OUT_GAIN":  0xAC,
    "I2S_IN_GAIN":     0xB8,
    "BT_IN_GAIN":      0xB9,
    "USB_CARD_GAIN":   0xBA,
    "SPDIF_IN_GAIN":   0xBB,
    # ── Mic effects ──
    "MIC_NOISE_SUP":    0x82,
    "MIC_FREQ_SHIFT":   0x83,
    "MIC_HOWLING":      0x84,
    "MIC_PITCH":        0x86,
    "MIC_AUTOTUNE":     0x87,
    "MIC_VCHANGER":     0x88,
    "MIC_VCHANGER_PRO": 0x89,
    "MIC_ECHO":         0x8A,
    "MIC_REVERB":       0x8B,
    "MIC_PLATE_REV":    0x8C,
    "MIC_PRO_REVERB":   0x8D,
    "MIC_SILENCE":      0x91,
    "MIC_DRC":          0xA0,
    "MIC_PRE_EQ":       0xA4,
    "MIC_BYPASS_EQ":    0xA5,
    "MIC_ECHO_EQ":      0xA6,
    "MIC_REVERB_EQ":    0xA7,
    "MIC_OUT_EQ":       0xA8,
    "MIC_BYPASS_GAIN":  0xAD,
    "MIC_ECHO_GAIN":    0xAE,
    "MIC_REVERB_GAIN":  0xAF,
    "MIC_OUT_GAIN":     0xB0,
    # ── Guitar effects ──
    "GUITAR_PINGPONG": 0x8E,
    "GUITAR_CHORUS":   0x8F,
    "GUITAR_AUTOWAH":  0x90,
    # ── Rec / Other ──
    "REC_DRC":           0xA1,   # ← BYŁO BRAKUJĄCE
    "REC_OUT_EQ":        0xA9,
    "REC_BYPASS_GAIN":   0xB1,
    "REC_EFFECT_GAIN":   0xB2,
    "REC_MUSIC_GAIN":    0xB3,
    "REC_EFFECT_REMIND": 0xB4,
    "USB_OUT_GAIN":      0xB5,
    "KEY_REMIND_GAIN":   0xB6,
    "EFFECT_REMIND_GAIN":0xB7,
}

def build_frame(module_id, params, param_select=ALL_PARAM):
    """Buduje pełną ramkę ACP: [A5 5A] [mod_id] [len] [FF] [params...] [16]"""
    data = bytes([param_select]) + params
    return SYNC_HEAD + bytes([module_id, len(data)]) + data + bytes([SYNC_TAIL])

def u16(values):
    """Pakuje listę int16 jako little-endian 16-bit."""
    return b"".join(struct.pack("<H", v & 0xFFFF) for v in values)


# ─────────────────────────────────────────────────────────────────────────────
# USB — wykrywanie urządzenia
# ─────────────────────────────────────────────────────────────────────────────

def _find_mvsilicon_hidraw():
    import glob
    for h in sorted(glob.glob("/dev/hidraw*")):
        try:
            r = subprocess.run(["udevadm","info",h], capture_output=True, text=True, timeout=3)
            if "8888" in r.stdout and "1719" in r.stdout:
                return h
        except Exception:
            pass
    return None

def device_present():
    if _HAS_PYUSB:
        try:
            return _usb_core.find(idVendor=0x8888, idProduct=0x1719) is not None
        except Exception:
            pass
    try:
        r = subprocess.run(["lsusb","-d","8888:1719"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and "8888:1719" in r.stdout
    except Exception:
        return False

def scan_hid_devices():
    devices = []
    if _HAS_PYUSB:
        try:
            devs = list(_usb_core.find(idVendor=0x8888, idProduct=0x1719, find_all=True))
            if devs:
                for dev in devs:
                    info = {"path":f"USB {dev.bus:03d}:{dev.address:03d}",
                            "vid":f"{dev.idVendor:04x}","pid":f"{dev.idProduct:04x}","match":True}
                    try: info["devname"] = _usb_util.get_string(dev, dev.iProduct) or "MVSilicon B1"
                    except: info["devname"] = "MVSilicon B1 USB Audio"
                    devices.append(info)
                return devices
        except Exception:
            pass
    try:
        r = subprocess.run(["lsusb","-d","8888:1719"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and "8888:1719" in r.stdout:
            parts = r.stdout.strip().split()
            bus = parts[1] if len(parts)>1 else "?"
            devnum = parts[3].rstrip(":") if len(parts)>3 else "?"
            hidraw = _find_mvsilicon_hidraw() or "/dev/hidraw?"
            devices.append({
                "path": f"USB Bus {bus} Dev {devnum}  |  {hidraw}",
                "vid":"8888","pid":"1719","match":True,
                "devname":"MVSilicon B1 USB Audio  [acp_send + hidraw]",
            })
            return devices
    except Exception:
        pass
    devices.append({"path":"(brak)","vid":"","pid":"",
                    "devname":"Nie znaleziono urządzenia MVSilicon BP1048B2","match":False})
    return devices


# ─────────────────────────────────────────────────────────────────────────────
# acp_send — kompilacja i wysyłanie
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACP_SEND_BIN  = os.path.join(_SCRIPT_DIR, "acp_send")
ACP_SEND_SRC  = os.path.join(_SCRIPT_DIR, "acp_send.c")
ACP_QUERY_BIN = os.path.join(_SCRIPT_DIR, "acp_query")
ACP_QUERY_SRC = os.path.join(_SCRIPT_DIR, "acp_query.c")

def ensure_acp_send():
    if not os.path.isfile(ACP_SEND_SRC):
        if os.path.isfile(ACP_SEND_BIN):
            return True, f"Binarka gotowa: {ACP_SEND_BIN}"
        return False, f"Brak {ACP_SEND_SRC} i brak binarki"
    need_build = (
        not os.path.isfile(ACP_SEND_BIN) or
        os.path.getmtime(ACP_SEND_SRC) > os.path.getmtime(ACP_SEND_BIN)
    )
    if not need_build:
        return True, f"Binarka aktualna: {ACP_SEND_BIN}"
    print(f"[BUILD] Kompiluję {ACP_SEND_SRC} …")
    try:
        r = subprocess.run(
            ["gcc", "-O2", "-o", ACP_SEND_BIN, ACP_SEND_SRC, "-lusb-1.0"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            os.chmod(ACP_SEND_BIN, 0o755)
            return True, f"✓ Skompilowano: {ACP_SEND_BIN}"
        else:
            return False, f"Błąd kompilacji:\n{r.stderr.strip()}"
    except FileNotFoundError:
        return False, "Brak gcc — zainstaluj: sudo apt install build-essential libusb-1.0-0-dev"
    except Exception as e:
        return False, f"Błąd kompilacji: {e}"

def ensure_acp_query():
    if os.path.isfile(ACP_QUERY_BIN):
        return True, ACP_QUERY_BIN
    if not os.path.isfile(ACP_QUERY_SRC):
        return False, f"Brak {ACP_QUERY_SRC}"
    try:
        r = subprocess.run(
            ["gcc", "-O2", "-o", ACP_QUERY_BIN, ACP_QUERY_SRC, "-lusb-1.0"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            os.chmod(ACP_QUERY_BIN, 0o755)
            return True, ACP_QUERY_BIN
        return False, r.stderr.strip()
    except Exception as e:
        return False, str(e)

def acp_query_device(query="fw") -> dict:
    ok, msg = ensure_acp_query()
    if not ok:
        return {"error": msg}
    try:
        r = subprocess.run(
            [ACP_QUERY_BIN, query],
            capture_output=True, text=True, timeout=3.0
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {"error": r.stderr.strip() or "brak odpowiedzi"}
        raw = [int(x, 16) for x in r.stdout.strip().split(",") if x.strip()]
        if len(raw) < 5 or raw[0] != 0xA5 or raw[1] != 0x5A:
            return {"error": f"zła odpowiedź: {r.stdout.strip()[:40]}"}
        module_id = raw[2]
        data = raw[4:]
        result = {"module_id": module_id, "raw": raw}
        if module_id == 0x00 and len(data) >= 5:
            result["fw_type"] = data[0]
            result["fw_ver"]  = f"V{data[1]}.{data[2]}.{data[3] if len(data)>3 else 0}"
            result["fx_ver"]  = f"V{data[4]}.{data[5]}.{data[6] if len(data)>6 else 0}" if len(data)>5 else "?"
            chip_map = {0x30:"BPxx", 0x20:"AP82xx", 0x10:"DU56x", 0x40:"DU26x"}
            result["chip"] = chip_map.get(data[0], f"0x{data[0]:02X}")
        elif module_id == 0x01 and len(data) >= 4:
            def le16(i): return data[i] | (data[i+1] << 8) if i+1 < len(data) else 0
            result["cpu_used"]  = le16(0)
            result["cpu_total"] = le16(2)
            result["mem_used"]  = le16(4)
            result["mem_total"] = le16(6)
        return result
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# WYSYŁANIE RAMKI — naprawione: pełna ramka ACP, nie 3 bajty!
# ─────────────────────────────────────────────────────────────────────────────

def dsp_send_raw(pkt: bytes) -> str:
    """
    Wysyła PEŁNĄ ramkę ACP do DSP przez IFACE=4, control transfer.
    pkt: pełna ramka (np. A5 5A 82 0B FF 01 00 ... 16), padowana do 64 bajtów.
    """
    if _HAS_PYUSB:
        try:
            dev = _usb_core.find(idVendor=0x8888, idProduct=0x1719)
            if dev is not None:
                for i in range(6):
                    try:
                        if dev.is_kernel_driver_active(i):
                            dev.detach_kernel_driver(i)
                    except Exception:
                        pass
                _usb_util.claim_interface(dev, 4)
                # NAPRAWA: wysyłamy pełną ramkę (do 64 bajtów), nie tylko 3 bajty!
                pkt64 = bytes(pkt[:64]).ljust(64, b'\x00')
                dev.ctrl_transfer(0x21, 0x09, 0x0200, 0x0004, pkt64)
                _usb_util.release_interface(dev, 4)
                for i in range(6):
                    try: dev.attach_kernel_driver(i)
                    except: pass
                return "OK"
        except Exception as e:
            return f"pyusb ERR: {e}"
    return "pyusb niedostępne"


def acp_send_frame(frame: bytes) -> str:
    """
    Wysyła pełną ramkę ACP do DSP.
    Metoda 1: pyusb IFACE=4 — pełna ramka (NAPRAWIONE)
    Metoda 2: fallback → subprocess acp_send (raw hex)
    """
    # Metoda 1: pyusb iface 4 — PEŁNA ramka
    if _HAS_PYUSB:
        r = dsp_send_raw(frame)
        if r == "OK":
            return "OK"

    # Metoda 2: subprocess acp_send (raw hex — już obsługuje >3 bajtów)
    hex_str = " ".join(f"{b:02X}" for b in frame)
    try:
        r = subprocess.run(
            [ACP_SEND_BIN, hex_str],
            capture_output=True, text=True, timeout=3.0
        )
        out = (r.stdout + r.stderr).strip()
        return out if out else ("OK" if r.returncode == 0 else f"exit {r.returncode}")
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except FileNotFoundError:
        return f"BRAK: {ACP_SEND_BIN}"
    except Exception as e:
        return f"ERR: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Preset engine
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PRESETS = {
    "Default (DAM)": {
        "mic_ns_en":1,"mic_ns_thr":-4500,"mic_ns_ratio":3,"mic_ns_attack":2,"mic_ns_release":100,
        "mic_howl_en":0,"mic_howl_mode":1,
        "mic_freq_en":0,"mic_freq_delta":2,
        "mic_silence_en":1,"mic_silence_amp":0,
        "mic_echo_en":0,"mic_echo_cf":8000,"mic_echo_att":14636,"mic_echo_delay":256,
        "mic_echo_max_delay":350,"mic_echo_hq":0,"mic_echo_dry":100,"mic_echo_wet":100,
        "mic_rev_en":0,"mic_rev_dry":100,"mic_rev_wet":100,"mic_rev_width":100,"mic_rev_room":65,"mic_rev_damp":35,
        "mic_plate_en":0,"mic_plate_hcf":8000,"mic_plate_mod":1,"mic_plate_pre":2500,
        "mic_plate_diff":60,"mic_plate_decay":65,"mic_plate_damp":5000,"mic_plate_wet":55,
        "mic_pitch_en":0,"mic_pitch_key":70,
        "mic_autotune_en":0,"mic_autotune_key":67,"mic_autotune_snap":110,"mic_autotune_acc":0,
        "mic_vch_en":0,"mic_vch_pitch":200,"mic_vch_formant":150,
        "mic_drc_en":0,"mic_drc_thr1":0,"mic_drc_thr2":0,"mic_drc_ratio1":100,"mic_drc_ratio2":100,"mic_drc_att":1,"mic_drc_rel":1000,
        "mus_ns_en":0,"mus_ns_thr":-6500,"mus_ns_ratio":2,"mus_ns_attack":1,"mus_ns_release":100,
        "mus_vbass_en":1,"mus_vbass_cut":80,"mus_vbass_int":7,"mus_vbass_enh":1,
        "mus_vbass_cls_en":0,"mus_vbass_cls_cut":50,"mus_vbass_cls_int":12,
        "mus_3d_en":0,"mus_3d_int":80,"mus_3dplus_en":0,"mus_3dplus_int":70,
        "mus_stereo_en":1,"mus_stereo_shp":1,
        "mus_vcut_en":0,"mus_vcut_val":100,
        "mus_vrem_en":0,"mus_vrem_lo":200,"mus_vrem_hi":15000,
        "mus_pitch_en":0,"mus_pitch_key":70,
        "mus_drc_en":1,"mus_drc_xfreq":300,"mus_drc_thr1":0,"mus_drc_thr2":0,"mus_drc_ratio1":100,"mus_drc_ratio2":100,"mus_drc_att1":1,"mus_drc_rel":1000,
        "mus_delay_en":0,"mus_delay_val":10,
        "mus_exciter_en":0,"mus_exciter_cut":1000,"mus_exciter_dry":100,"mus_exciter_wet":100,
        "mus_phase_en":0,"mus_phase_diff":1,
        "gain_mus":4096,"gain_mic":4096,"gain_mic_bypass":4096,"gain_mic_echo":4096,"gain_mic_rev":4096,
        "gain_bt":4096,"gain_usb":4096,"gain_i2s":4096,"gain_spdif":4096,
    },
    "Karaoke": {
        "mic_ns_en":1,"mic_ns_thr":-3000,"mic_ns_ratio":4,"mic_ns_attack":2,"mic_ns_release":100,
        "mic_howl_en":1,"mic_howl_mode":2,
        "mic_freq_en":0,"mic_freq_delta":0,
        "mic_silence_en":1,"mic_silence_amp":0,
        "mic_echo_en":1,"mic_echo_cf":8000,"mic_echo_att":12000,"mic_echo_delay":180,
        "mic_echo_max_delay":350,"mic_echo_hq":0,"mic_echo_dry":100,"mic_echo_wet":100,
        "mic_rev_en":0,"mic_rev_dry":100,"mic_rev_wet":40,"mic_rev_width":80,"mic_rev_room":50,"mic_rev_damp":40,
        "mic_plate_en":1,"mic_plate_hcf":8000,"mic_plate_mod":1,"mic_plate_pre":2000,"mic_plate_diff":60,"mic_plate_decay":70,"mic_plate_damp":5000,"mic_plate_wet":65,
        "mic_pitch_en":0,"mic_pitch_key":70,
        "mic_autotune_en":1,"mic_autotune_key":98,"mic_autotune_snap":117,"mic_autotune_acc":0,
        "mic_vch_en":0,"mic_vch_pitch":200,"mic_vch_formant":150,
        "mic_drc_en":1,"mic_drc_thr1":-2000,"mic_drc_thr2":-1000,"mic_drc_ratio1":4,"mic_drc_ratio2":3,"mic_drc_att":4,"mic_drc_rel":500,
        "mus_ns_en":1,"mus_ns_thr":-6000,"mus_ns_ratio":2,"mus_ns_attack":1,"mus_ns_release":100,
        "mus_vbass_en":1,"mus_vbass_cut":80,"mus_vbass_int":20,"mus_vbass_enh":1,
        "mus_vbass_cls_en":0,"mus_vbass_cls_cut":50,"mus_vbass_cls_int":12,
        "mus_3d_en":1,"mus_3d_int":60,"mus_3dplus_en":0,"mus_3dplus_int":50,
        "mus_stereo_en":1,"mus_stereo_shp":1,
        "mus_vcut_en":1,"mus_vcut_val":80,
        "mus_vrem_en":0,"mus_vrem_lo":200,"mus_vrem_hi":15000,
        "mus_pitch_en":0,"mus_pitch_key":70,
        "mus_drc_en":1,"mus_drc_xfreq":300,"mus_drc_thr1":0,"mus_drc_thr2":0,"mus_drc_ratio1":100,"mus_drc_ratio2":100,"mus_drc_att1":1,"mus_drc_rel":1000,
        "mus_delay_en":0,"mus_delay_val":50,
        "mus_exciter_en":0,"mus_exciter_cut":1000,"mus_exciter_dry":100,"mus_exciter_wet":100,
        "mus_phase_en":0,"mus_phase_diff":0,
        "gain_mus":4096,"gain_mic":4096,"gain_mic_bypass":3000,"gain_mic_echo":4096,"gain_mic_rev":3500,
        "gain_bt":4096,"gain_usb":4096,"gain_i2s":4096,"gain_spdif":4096,
    },
    "Studio Vocal": {
        "mic_ns_en":1,"mic_ns_thr":-5000,"mic_ns_ratio":3,"mic_ns_attack":2,"mic_ns_release":100,
        "mic_howl_en":1,"mic_howl_mode":2,
        "mic_freq_en":0,"mic_freq_delta":0,
        "mic_silence_en":1,"mic_silence_amp":0,
        "mic_echo_en":0,"mic_echo_cf":8000,"mic_echo_att":8000,"mic_echo_delay":120,
        "mic_echo_max_delay":350,"mic_echo_hq":0,"mic_echo_dry":100,"mic_echo_wet":100,
        "mic_rev_en":0,"mic_rev_dry":100,"mic_rev_wet":30,"mic_rev_width":80,"mic_rev_room":40,"mic_rev_damp":40,
        "mic_plate_en":1,"mic_plate_hcf":10000,"mic_plate_mod":1,"mic_plate_pre":1000,"mic_plate_diff":70,"mic_plate_decay":45,"mic_plate_damp":6000,"mic_plate_wet":30,
        "mic_pitch_en":0,"mic_pitch_key":70,
        "mic_autotune_en":0,"mic_autotune_key":67,"mic_autotune_snap":110,"mic_autotune_acc":0,
        "mic_vch_en":0,"mic_vch_pitch":200,"mic_vch_formant":150,
        "mic_drc_en":1,"mic_drc_thr1":-3000,"mic_drc_thr2":-1500,"mic_drc_ratio1":3,"mic_drc_ratio2":2,"mic_drc_att":3,"mic_drc_rel":300,
        "mus_ns_en":1,"mus_ns_thr":-7000,"mus_ns_ratio":2,"mus_ns_attack":1,"mus_ns_release":100,
        "mus_vbass_en":0,"mus_vbass_cut":80,"mus_vbass_int":7,"mus_vbass_enh":1,
        "mus_vbass_cls_en":0,"mus_vbass_cls_cut":50,"mus_vbass_cls_int":12,
        "mus_3d_en":0,"mus_3d_int":30,"mus_3dplus_en":0,"mus_3dplus_int":30,
        "mus_stereo_en":1,"mus_stereo_shp":2,
        "mus_vcut_en":0,"mus_vcut_val":100,
        "mus_vrem_en":0,"mus_vrem_lo":200,"mus_vrem_hi":15000,
        "mus_pitch_en":0,"mus_pitch_key":70,
        "mus_drc_en":1,"mus_drc_xfreq":300,"mus_drc_thr1":0,"mus_drc_thr2":0,"mus_drc_ratio1":100,"mus_drc_ratio2":100,"mus_drc_att1":1,"mus_drc_rel":1000,
        "mus_delay_en":0,"mus_delay_val":50,
        "mus_exciter_en":1,"mus_exciter_cut":3000,"mus_exciter_dry":100,"mus_exciter_wet":100,
        "mus_phase_en":0,"mus_phase_diff":0,
        "gain_mus":4096,"gain_mic":4096,"gain_mic_bypass":4096,"gain_mic_echo":3500,"gain_mic_rev":3500,
        "gain_bt":4096,"gain_usb":4096,"gain_i2s":4096,"gain_spdif":4096,
    },
    "Deep Voice": {
        "mic_ns_en":1,"mic_ns_thr":-4000,"mic_ns_ratio":3,"mic_ns_attack":2,"mic_ns_release":100,
        "mic_howl_en":1,"mic_howl_mode":2,
        "mic_freq_en":0,"mic_freq_delta":0,
        "mic_silence_en":1,"mic_silence_amp":0,
        "mic_echo_en":0,"mic_echo_cf":8000,"mic_echo_att":10000,"mic_echo_delay":200,
        "mic_echo_max_delay":350,"mic_echo_hq":0,"mic_echo_dry":100,"mic_echo_wet":100,
        "mic_rev_en":1,"mic_rev_dry":100,"mic_rev_wet":50,"mic_rev_width":80,"mic_rev_room":60,"mic_rev_damp":30,
        "mic_plate_en":0,"mic_plate_hcf":8000,"mic_plate_mod":1,"mic_plate_pre":2000,"mic_plate_diff":60,"mic_plate_decay":50,"mic_plate_damp":5000,"mic_plate_wet":40,
        "mic_pitch_en":1,"mic_pitch_key":50,
        "mic_autotune_en":0,"mic_autotune_key":67,"mic_autotune_snap":110,"mic_autotune_acc":0,
        "mic_vch_en":1,"mic_vch_pitch":170,"mic_vch_formant":130,
        "mic_drc_en":0,"mic_drc_thr1":0,"mic_drc_thr2":0,"mic_drc_ratio1":100,"mic_drc_ratio2":100,"mic_drc_att":1,"mic_drc_rel":1000,
        "mus_ns_en":1,"mus_ns_thr":-6000,"mus_ns_ratio":2,"mus_ns_attack":1,"mus_ns_release":100,
        "mus_vbass_en":1,"mus_vbass_cut":90,"mus_vbass_int":40,"mus_vbass_enh":1,
        "mus_vbass_cls_en":0,"mus_vbass_cls_cut":50,"mus_vbass_cls_int":12,
        "mus_3d_en":0,"mus_3d_int":40,"mus_3dplus_en":1,"mus_3dplus_int":60,
        "mus_stereo_en":1,"mus_stereo_shp":2,
        "mus_vcut_en":0,"mus_vcut_val":100,
        "mus_vrem_en":0,"mus_vrem_lo":200,"mus_vrem_hi":15000,
        "mus_pitch_en":0,"mus_pitch_key":70,
        "mus_drc_en":1,"mus_drc_xfreq":300,"mus_drc_thr1":0,"mus_drc_thr2":0,"mus_drc_ratio1":100,"mus_drc_ratio2":100,"mus_drc_att1":1,"mus_drc_rel":1000,
        "mus_delay_en":1,"mus_delay_val":30,
        "mus_exciter_en":0,"mus_exciter_cut":1000,"mus_exciter_dry":100,"mus_exciter_wet":100,
        "mus_phase_en":0,"mus_phase_diff":0,
        "gain_mus":4096,"gain_mic":4096,"gain_mic_bypass":3800,"gain_mic_echo":3800,"gain_mic_rev":4096,
        "gain_bt":4096,"gain_usb":4096,"gain_i2s":4096,"gain_spdif":4096,
    },
}

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acp_presets.cfg")

def load_presets():
    presets = copy.deepcopy(DEFAULT_PRESETS)
    if os.path.isfile(CFG_PATH):
        try:
            with open(CFG_PATH,"r",encoding="utf-8") as f:
                user = json.load(f)
            presets.update(user)
        except: pass
    return presets

def save_presets(presets):
    try:
        with open(CFG_PATH,"w",encoding="utf-8") as f:
            json.dump(presets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[CFG] Błąd zapisu: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Mapowanie widget keys → moduły ACP (kolejność parametrów per moduł)
# ─────────────────────────────────────────────────────────────────────────────

# Format: "widget_prefix": (MODULE_KEY, [lista kluczy widgetów w kolejności parametrów])
MIC_MODULES = {
    "mic_ns":    (MODULE["MIC_NOISE_SUP"],  ["mic_ns_en","mic_ns_thr","mic_ns_ratio","mic_ns_attack","mic_ns_release"]),
    "mic_howl":  (MODULE["MIC_HOWLING"],    ["mic_howl_en","mic_howl_mode"]),
    "mic_freq":  (MODULE["MIC_FREQ_SHIFT"], ["mic_freq_en","mic_freq_delta"]),
    "mic_sil":   (MODULE["MIC_SILENCE"],    ["mic_silence_en","mic_silence_amp"]),
    "mic_echo":  (MODULE["MIC_ECHO"],       ["mic_echo_en","mic_echo_cf","mic_echo_att","mic_echo_delay","mic_echo_max_delay","mic_echo_hq","mic_echo_dry","mic_echo_wet"]),
    "mic_rev":   (MODULE["MIC_REVERB"],     ["mic_rev_en","mic_rev_dry","mic_rev_wet","mic_rev_width","mic_rev_room","mic_rev_damp"]),
    "mic_plate": (MODULE["MIC_PLATE_REV"],  ["mic_plate_en","mic_plate_hcf","mic_plate_mod","mic_plate_pre","mic_plate_diff","mic_plate_decay","mic_plate_damp","mic_plate_wet"]),
    "mic_pitch": (MODULE["MIC_PITCH"],      ["mic_pitch_en","mic_pitch_key"]),
    "mic_at":    (MODULE["MIC_AUTOTUNE"],   ["mic_autotune_en","mic_autotune_key","mic_autotune_snap","mic_autotune_acc"]),
    "mic_vch":   (MODULE["MIC_VCHANGER"],   ["mic_vch_en","mic_vch_pitch","mic_vch_formant"]),
    "mic_drc":   (MODULE["MIC_DRC"],        ["mic_drc_en","mic_drc_thr1","mic_drc_thr2","mic_drc_ratio1","mic_drc_ratio2","mic_drc_att","mic_drc_rel"]),
}

MUS_MODULES = {
    "mus_ns":      (MODULE["MUSIC_NOISE"],     ["mus_ns_en","mus_ns_thr","mus_ns_ratio","mus_ns_attack","mus_ns_release"]),
    "mus_vbass":   (MODULE["MUSIC_VBASS"],     ["mus_vbass_en","mus_vbass_cut","mus_vbass_int","mus_vbass_enh"]),
    "mus_vbcls":   (MODULE["MUSIC_VBASS_CLS"], ["mus_vbass_cls_en","mus_vbass_cls_cut","mus_vbass_cls_int"]),
    "mus_3d":      (MODULE["MUSIC_3D"],        ["mus_3d_en","mus_3d_int"]),
    "mus_3dp":     (MODULE["MUSIC_3DPLUS"],    ["mus_3dplus_en","mus_3dplus_int"]),
    "mus_stereo":  (MODULE["MUSIC_STEREO"],    ["mus_stereo_en","mus_stereo_shp"]),
    "mus_vcut":    (MODULE["MUSIC_VCUT"],      ["mus_vcut_en","mus_vcut_val"]),
    "mus_vrem":    (MODULE["MUSIC_VREMOVE"],   ["mus_vrem_en","mus_vrem_lo","mus_vrem_hi"]),
    "mus_pitch":   (MODULE["MUSIC_PITCH"],     ["mus_pitch_en","mus_pitch_key"]),
    "mus_drc":     (MODULE["MUSIC_DRC"],       ["mus_drc_en","mus_drc_xfreq","mus_drc_thr1","mus_drc_thr2","mus_drc_ratio1","mus_drc_ratio2","mus_drc_att1","mus_drc_rel"]),
    "mus_delay":   (MODULE["MUSIC_DELAY"],     ["mus_delay_en","mus_delay_val"]),
    "mus_exciter": (MODULE["MUSIC_EXCITER"],   ["mus_exciter_en","mus_exciter_cut","mus_exciter_dry","mus_exciter_wet"]),
    "mus_phase":   (MODULE["MUSIC_PHASE"],     ["mus_phase_en","mus_phase_diff"]),
}

# Gain moduły: każdy ma format [enable=1, pregain, mute=0, channel=2] (SDK)
GAIN_MODULES = {
    "gain_mus":        (MODULE["MUSIC_OUT_GAIN"], "Music Out Gain"),
    "gain_mic":        (MODULE["MIC_OUT_GAIN"],    "Mic Out Gain"),
    "gain_mic_bypass": (MODULE["MIC_BYPASS_GAIN"], "Mic Bypass Gain"),
    "gain_mic_echo":   (MODULE["MIC_ECHO_GAIN"],   "Mic Echo Gain"),
    "gain_mic_rev":    (MODULE["MIC_REVERB_GAIN"], "Mic Reverb Gain"),
    "gain_bt":         (MODULE["BT_IN_GAIN"],      "BT In Gain"),
    "gain_usb":        (MODULE["USB_CARD_GAIN"],   "USB Card Gain"),
    "gain_i2s":        (MODULE["I2S_IN_GAIN"],     "I2S In Gain"),
    "gain_spdif":      (MODULE["SPDIF_IN_GAIN"],   "SPDIF In Gain"),
}


def _widget_val(self, key):
    """Pobiera wartość z widgetu (SliderRow → int, QCheckBox → 0/1)."""
    w = self._w.get(key)
    if isinstance(w, SliderRow):
        return w.value()
    elif isinstance(w, QCheckBox):
        return int(w.isChecked())
    return 0


def _build_module_frames(modules_map):
    """Z dict mapowania buduje listę (label, frame) do wysłania."""
    frames = []
    for prefix, (mod_id, keys) in modules_map.items():
        vals = [_widget_val(None, k) for k in keys]  # placeholder — używane przez FxMixerTab
        frames.append((prefix, mod_id, vals, keys))
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Worker threads
# ─────────────────────────────────────────────────────────────────────────────

class IniSendWorker(QThread):
    frame_done=pyqtSignal(int,str,str); progress=pyqtSignal(int,int)
    log=pyqtSignal(str,str); finished=pyqtSignal()
    def __init__(self,frames,indices): super().__init__(); self.frames=frames; self.indices=indices
    def run(self):
        total=len(self.frames)
        for step,(orig_idx,(mid,name,frame)) in enumerate(zip(self.indices,self.frames)):
            resp=acp_send_frame(frame)
            ok = "ERR" not in resp and "BRAK" not in resp and "TIMEOUT" not in resp
            status = "ok" if ok else "err"
            self.frame_done.emit(orig_idx, f"{'✓' if ok else '✗'}  {resp}","ok" if ok else "err")
            self.log.emit(f"→ [0x{mid:02X} {name}] TX: {frame.hex(' ').upper()}","tx")
            self.log.emit(f"← {resp}", status)
            self.progress.emit(step+1,total)
        self.log.emit(f"✓ Wysłano {total} modułów.","ok"); self.finished.emit()


class SniffWorker(QThread):
    packet=pyqtSignal(bytes,str); stopped=pyqtSignal()
    def __init__(self): super().__init__(); self._active=True
    def stop(self): self._active=False

    def _sniff_pyusb(self):
        dev = _usb_core.find(idVendor=0x8888, idProduct=0x1719)
        if dev is None: return
        try:
            if dev.is_kernel_driver_active(3): dev.detach_kernel_driver(3)
        except: pass
        _usb_util.claim_interface(dev, 4)
        while self._active:
            try:
                data = bytes(dev.read(0x83, 64, timeout=150))
                if any(b != 0 for b in data):
                    ts = time.strftime("%H:%M:%S.")+f"{int(time.time()*1000)%1000:03d}"
                    self.packet.emit(data, ts)
            except: pass
        _usb_util.release_interface(dev, 4)
        try: dev.attach_kernel_driver(3)
        except: pass

    def _sniff_hidraw(self, path):
        import select
        try:
            with open(path, "rb") as f:
                while self._active:
                    rdy,_,_ = select.select([f],[],[],0.15)
                    if rdy:
                        data = f.read(64)
                        if data and any(b!=0 for b in data):
                            ts = time.strftime("%H:%M:%S.")+f"{int(time.time()*1000)%1000:03d}"
                            self.packet.emit(bytes(data), ts)
        except Exception: pass

    def run(self):
        try:
            if _HAS_PYUSB:
                self._sniff_pyusb()
            else:
                h = _find_mvsilicon_hidraw()
                if h: self._sniff_hidraw(h)
        except Exception: pass
        self.stopped.emit()


class ConnectWorker(QThread):
    connected=pyqtSignal(); failed=pyqtSignal(str)
    def run(self):
        if not device_present():
            self.failed.emit("Nie znaleziono urządzenia 8888:1719")
            return
        ok, msg = ensure_acp_send()
        print(f"[BUILD] {msg}")
        if not ok:
            self.failed.emit(msg)
            return
        ensure_acp_query()
        self.connected.emit()


class DeviceInfoWorker(QThread):
    fw_info  = pyqtSignal(dict); cpu_info = pyqtSignal(dict); error = pyqtSignal(str)
    def __init__(self, interval_ms=3000):
        super().__init__(); self._interval = interval_ms / 1000.0; self._active = True
    def stop(self): self._active = False
    def run(self):
        fw = acp_query_device("fw")
        if "error" not in fw:
            self.fw_info.emit(fw)
        else:
            self.error.emit(f"FW query: {fw['error']}")
        while self._active:
            cpu = acp_query_device("cpu")
            if "error" not in cpu:
                self.cpu_info.emit(cpu)
            time.sleep(self._interval)


class QueuedSendWorker(QThread):
    frame_sent=pyqtSignal(str, int, bytes, str)
    frame_error=pyqtSignal(str, str)
    def __init__(self):
        super().__init__(); self._q=_queue.Queue(); self._active=True
    def enqueue(self, frame, label):
        self._q.put((frame, label))
    def stop(self):
        self._active=False; self._q.put(None)
    def run(self):
        while self._active:
            item=self._q.get()
            if item is None: break
            frame, label=item
            resp_str = acp_send_frame(frame)
            if "ERR" in resp_str or "BRAK" in resp_str or "TIMEOUT" in resp_str:
                self.frame_error.emit(label, resp_str)
            else:
                self.frame_sent.emit(label, len(frame), frame, resp_str)


# ─────────────────────────────────────────────────────────────────────────────
# INI parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_ini(path):
    results=[]; current_id=None; current_name=""; current_vals=[]
    with open(path,"r",encoding="latin-1") as f:
        for line in f:
            line=line.strip()
            if not line or line.startswith("#"): continue
            if line.startswith("[0x"):
                if current_id is not None and current_vals:
                    params=u16([v&0xFFFF for v in current_vals])
                    results.append((current_id,current_name,build_frame(current_id,params)))
                m=_re.match(r'\[0x([0-9a-fA-F]+)-?([^\]]*)\]',line)
                if m:
                    current_id=int(m.group(1),16); current_name=m.group(2).strip() or f"0x{current_id:02X}"
                else: current_id=None
                current_vals=[]
            elif "=" in line and current_id is not None:
                _,val=line.split("=",1); val=val.strip()
                for tok in val.split(","):
                    tok=tok.strip()
                    try: current_vals.append(int(tok))
                    except: pass
    if current_id is not None and current_vals:
        params=u16([v&0xFFFF for v in current_vals])
        results.append((current_id,current_name,build_frame(current_id,params)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Style
# ─────────────────────────────────────────────────────────────────────────────

STYLE = """
QMainWindow,QWidget{background:#0e1118;color:#dde1ee;font-family:"DejaVu Sans","Segoe UI",sans-serif;font-size:10pt;}
QTabWidget::pane{border:1px solid #232b3e;background:#0e1118;border-radius:4px;}
QTabBar::tab{background:#161d2e;color:#6b7a99;padding:8px 18px;border:1px solid #232b3e;border-bottom:none;border-radius:4px 4px 0 0;min-width:110px;font-weight:600;}
QTabBar::tab:selected{background:#0e1118;color:#00d4aa;border-bottom:2px solid #00d4aa;}
QTabBar::tab:hover:!selected{color:#c0c8e0;}
QPushButton{background:#1c2438;color:#c0c8e0;border:1px solid #2c3550;border-radius:5px;padding:6px 14px;font-weight:600;}
QPushButton:hover{background:#243050;color:#00d4aa;border-color:#00d4aa;}
QPushButton:pressed{background:#00d4aa;color:#000;}
QPushButton:disabled{color:#3d4a66;border-color:#1c2438;}
QPushButton#accent{background:#00d4aa;color:#021a14;border:none;border-radius:5px;padding:7px 18px;font-weight:700;}
QPushButton#accent:hover{background:#00eebc;}
QPushButton#accent:pressed{background:#009977;}
QPushButton#accent:disabled{background:#005544;color:#002a22;}
QPushButton#danger{background:#2a0a0a;color:#ff4455;border:1px solid #441111;border-radius:5px;padding:6px 14px;font-weight:600;}
QPushButton#danger:hover{background:#3a0f0f;border-color:#ff4455;}
QPushButton#danger:pressed{background:#ff4455;color:#000;}
QPushButton#preset_btn{background:#13192a;color:#aab8d8;border:1px solid #1e2a40;border-radius:5px;padding:7px 10px;font-weight:600;text-align:left;}
QPushButton#preset_btn:hover{background:#1e2d4a;color:#00d4aa;border-color:#00d4aa;}
QPushButton#preset_btn:checked{background:#003d2e;color:#00d4aa;border:2px solid #00d4aa;}
QComboBox{background:#1a2030;color:#dde1ee;border:1px solid #2c3550;border-radius:4px;padding:5px 10px;min-height:28px;}
QComboBox:hover{border-color:#00d4aa;}
QComboBox::drop-down{border:none;width:22px;}
QComboBox QAbstractItemView{background:#1a2030;color:#dde1ee;selection-background-color:#00d4aa;selection-color:#000;border:1px solid #2c3550;}
QLineEdit{background:#131925;color:#dde1ee;border:1px solid #2c3550;border-radius:4px;padding:5px 8px;font-family:"DejaVu Sans Mono","Consolas",monospace;}
QLineEdit:focus{border-color:#00d4aa;}
QTextEdit{background:#080c14;color:#8fa0c0;border:1px solid #1a2030;border-radius:4px;font-family:"DejaVu Sans Mono","Consolas",monospace;font-size:9pt;}
QProgressBar{background:#131925;border:1px solid #232b3e;border-radius:4px;height:8px;text-align:center;color:transparent;}
QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00aabb,stop:1 #00d4aa);border-radius:4px;}
QGroupBox{color:#00d4aa;border:1px solid #232b3e;border-radius:6px;margin-top:14px;font-weight:700;padding:10px 8px 8px 8px;}
QGroupBox::title{subcontrol-origin:margin;subcontrol-position:top left;left:10px;padding:0 6px;color:#00d4aa;}
QSlider::groove:horizontal{background:#1a2030;height:4px;border-radius:2px;}
QSlider::handle:horizontal{background:#00d4aa;width:14px;height:14px;margin:-5px 0;border-radius:7px;}
QSlider::sub-page:horizontal{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0088bb,stop:1 #00d4aa);border-radius:2px;}
QSlider::handle:horizontal:hover{background:#00f0cc;}
QCheckBox{color:#b0bcd8;spacing:8px;}
QCheckBox::indicator{width:16px;height:16px;border:1px solid #3a4a66;border-radius:3px;background:#131925;}
QCheckBox::indicator:checked{background:#00d4aa;border-color:#00d4aa;}
QCheckBox:hover{color:#dde1ee;}
QTreeWidget{background:#0c1020;color:#b0bcd8;border:1px solid #1a2030;border-radius:4px;alternate-background-color:#0f1525;font-family:"DejaVu Sans Mono",monospace;font-size:9pt;}
QTreeWidget::item:selected{background:#003344;color:#00d4aa;}
QTreeWidget::item:hover{background:#0d1a28;}
QHeaderView::section{background:#131925;color:#00d4aa;border:none;border-right:1px solid #232b3e;padding:5px 10px;font-weight:700;}
QScrollBar:vertical{background:#0e1118;width:10px;border-radius:5px;}
QScrollBar::handle:vertical{background:#2c3550;border-radius:5px;min-height:30px;}
QScrollBar::handle:vertical:hover{background:#3a4870;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar:horizontal{background:#0e1118;height:10px;border-radius:5px;}
QScrollBar::handle:horizontal{background:#2c3550;border-radius:5px;min-width:30px;}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}
QFrame[frameShape="4"],QFrame[frameShape="5"]{color:#1e2840;}
QStatusBar{background:#090d18;color:#4a5a78;border-top:1px solid #161e30;font-family:"DejaVu Sans Mono",monospace;font-size:9pt;padding:2px 8px;}
QLabel#dim{color:#4a5a78;font-size:9pt;}
QLabel#head{color:#00d4aa;font-weight:700;font-size:11pt;}
QLabel#mono{font-family:"DejaVu Sans Mono",monospace;font-size:9pt;}
QLabel#conn_dim{color:#4a5a78;font-size:10pt;}
QLabel#val{color:#00d4aa;font-family:"DejaVu Sans Mono",monospace;font-size:9pt;min-width:54px;}
QLabel#section{color:#00aacc;font-weight:700;font-size:10pt;padding-top:4px;}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pomocnicze widgety
# ─────────────────────────────────────────────────────────────────────────────

class LogEdit(QTextEdit):
    COLORS={"ok":"#00cc88","err":"#ff4455","info":"#4488ff","tx":"#6b7a99",
            "rx":"#00aacc","warn":"#ffaa00","acp":"#ffdd44","dim":"#3a4a66"}
    def __init__(self,parent=None):
        super().__init__(parent); self.setReadOnly(True)
        self.document().setMaximumBlockCount(2000)
    def append_colored(self,text,level=""):
        color=self.COLORS.get(level,"#8fa0c0"); ts=time.strftime("%H:%M:%S")
        cursor=self.textCursor(); cursor.movePosition(QTextCursor.End)
        fmt=cursor.charFormat(); fmt.setForeground(QColor("#2c3a54"))
        cursor.setCharFormat(fmt); cursor.insertText(f"[{ts}] ")
        fmt.setForeground(QColor(color)); cursor.setCharFormat(fmt)
        cursor.insertText(text+"\n"); self.setTextCursor(cursor); self.ensureCursorVisible()


class SliderRow(QWidget):
    valueChanged=pyqtSignal(int)
    def __init__(self,label,lo,hi,default,unit="",parent=None):
        super().__init__(parent); self.lo=lo; self.hi=hi; self._unit=unit
        lay=QHBoxLayout(self); lay.setContentsMargins(0,1,0,1); lay.setSpacing(6)
        lbl=QLabel(label); lbl.setFixedWidth(148); lbl.setObjectName("dim"); lay.addWidget(lbl)
        self.slider=QSlider(Qt.Horizontal); self.slider.setRange(lo,hi)
        self.slider.setValue(max(lo,min(hi,default))); lay.addWidget(self.slider,1)
        self.val_lbl=QLabel(self._fmt(default)); self.val_lbl.setObjectName("val")
        self.val_lbl.setFixedWidth(64); self.val_lbl.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        lay.addWidget(self.val_lbl)
        self.slider.valueChanged.connect(self._on_change)
    def _fmt(self,v): return f"{v}{self._unit}"
    def _on_change(self,v): self.val_lbl.setText(self._fmt(v)); self.valueChanged.emit(v)
    def value(self): return self.slider.value()
    def setValue(self,v): self.slider.setValue(max(self.lo,min(self.hi,int(v))))


def hsep():
    f=QFrame(); f.setFrameShape(QFrame.HLine); f.setFrameShadow(QFrame.Sunken); return f

def accent_btn(text):
    b=QPushButton(text); b.setObjectName("accent"); return b

def danger_btn(text):
    b=QPushButton(text); b.setObjectName("danger"); return b

def section_lbl(text):
    l=QLabel(text); l.setObjectName("section"); return l


# ─────────────────────────────────────────────────────────────────────────────
# FX Mixer — główna zakładka
# ─────────────────────────────────────────────────────────────────────────────

class FxMixerTab(QWidget):
    send_frames = pyqtSignal(list)   # list[(bytes, label)]
    send_single = pyqtSignal(bytes, str)
    log         = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._presets = load_presets()
        self._w = {}        # key → SliderRow | QCheckBox
        self._active_preset = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        # ── Panel presetów (lewy) ──
        outer.addWidget(self._build_preset_panel())
        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color:#1e2840;")
        outer.addWidget(sep)

        # ── FX area (prawy, scrollowalny) ──
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget(); scroll.setWidget(inner)
        self._fx_root = QVBoxLayout(inner)
        self._fx_root.setContentsMargins(14,10,14,14); self._fx_root.setSpacing(6)
        self._build_fx()
        outer.addWidget(scroll, 1)

    # ── Helpers: dodawanie widgetów do layoutu + rejestracja w self._w ──

    def _ck(self, layout, key, label, default):
        """Dodaje QCheckBox do layoutu i rejestruje w self._w[key]."""
        cb = QCheckBox(label); cb.setChecked(bool(default))
        layout.addWidget(cb); self._w[key] = cb
        return cb

    def _sl(self, layout, key, label, lo, hi, default, unit=""):
        """Dodaje SliderRow do layoutu i rejestruje w self._w[key]."""
        sr = SliderRow(label, lo, hi, default, unit)
        layout.addWidget(sr); self._w[key] = sr
        return sr

    def _val(self, key):
        """Pobiera wartość widgetu jako int."""
        w = self._w.get(key)
        if isinstance(w, SliderRow):
            return w.value()
        elif isinstance(w, QCheckBox):
            return int(w.isChecked())
        return 0

    # ── Preset panel ──

    def _build_preset_panel(self):
        w = QWidget(); w.setFixedWidth(198)
        w.setStyleSheet("background:#090e1a;")
        lay = QVBoxLayout(w); lay.setContentsMargins(8,10,8,10); lay.setSpacing(4)
        hdr = QLabel("PRESETY"); hdr.setObjectName("head"); hdr.setAlignment(Qt.AlignCenter)
        lay.addWidget(hdr); lay.addWidget(hsep())

        self._preset_list_w = QWidget()
        self._preset_list_lay = QVBoxLayout(self._preset_list_w)
        self._preset_list_lay.setContentsMargins(0,0,0,0); self._preset_list_lay.setSpacing(3)
        scroll2 = QScrollArea(); scroll2.setWidgetResizable(True)
        scroll2.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll2.setWidget(self._preset_list_w)
        lay.addWidget(scroll2, 1)
        self._rebuild_preset_btns()

        lay.addWidget(hsep())
        apply_all = accent_btn("▶ Zastosuj preset")
        apply_all.setToolTip("Wyślij aktywny preset do DSP (MIC + MUSIC + GAIN)")
        apply_all.clicked.connect(self._apply_all)
        lay.addWidget(apply_all)
        save_btn = QPushButton("💾 Zapisz jako…"); save_btn.clicked.connect(self._save_preset)
        lay.addWidget(save_btn)
        del_btn = danger_btn("🗑 Usuń"); del_btn.clicked.connect(self._delete_preset)
        lay.addWidget(del_btn)
        self._status_lbl = QLabel(""); self._status_lbl.setObjectName("dim")
        self._status_lbl.setWordWrap(True); self._status_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._status_lbl)
        return w

    def _rebuild_preset_btns(self):
        while self._preset_list_lay.count():
            it = self._preset_list_lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        self._active_preset = None
        for name in self._presets:
            btn = QPushButton(name); btn.setObjectName("preset_btn"); btn.setCheckable(True)
            btn.clicked.connect(lambda _, n=name: self._load_preset(n))
            self._preset_list_lay.addWidget(btn)
        self._preset_list_lay.addStretch()

    def _load_preset(self, name):
        p = self._presets.get(name)
        if not p: return
        for i in range(self._preset_list_lay.count()):
            w = self._preset_list_lay.itemAt(i).widget()
            if isinstance(w, QPushButton): w.setChecked(w.text() == name)
        for key, val in p.items():
            w = self._w.get(key)
            if isinstance(w, SliderRow): w.setValue(int(val))
            elif isinstance(w, QCheckBox): w.setChecked(bool(val))
        self._active_preset = name
        self._status_lbl.setText(f"📂 {name}")

    def _save_preset(self):
        text, ok = QInputDialog.getText(self, "Zapisz preset", "Nazwa presetu:")
        if not ok or not text.strip(): return
        name = text.strip(); vals = {}
        for key, w in self._w.items():
            if isinstance(w, SliderRow): vals[key] = w.value()
            elif isinstance(w, QCheckBox): vals[key] = int(w.isChecked())
        self._presets[name] = vals; save_presets(self._presets)
        self._rebuild_preset_btns()
        for i in range(self._preset_list_lay.count()):
            w = self._preset_list_lay.itemAt(i).widget()
            if isinstance(w, QPushButton): w.setChecked(w.text() == name)
        self._active_preset = name
        self._status_lbl.setText(f"✓ Zapisano: {name}")

    def _delete_preset(self):
        if not self._active_preset:
            QMessageBox.information(self, "Brak", "Wybierz najpierw preset."); return
        if self._active_preset in DEFAULT_PRESETS:
            QMessageBox.warning(self, "Chroniony", "Nie można usunąć wbudowanego presetu."); return
        self._presets.pop(self._active_preset, None); save_presets(self._presets)
        self._rebuild_preset_btns(); self._active_preset = None; self._status_lbl.setText("")

    # ── Budowanie sekcji FX ──

    def _build_fx(self):
        r = self._fx_root

        # ══════════════════════════════════════════════════════════════════
        # 🎙 MIKROFON
        # ══════════════════════════════════════════════════════════════════
        r.addWidget(section_lbl("🎙  MIKROFON"))
        mg = QGridLayout(); mg.setSpacing(6)

        # Noise Suppressor
        g = QGroupBox("Noise Suppressor"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_ns_en", "Włącz", True)
        self._sl(l, "mic_ns_thr", "Próg", -9999, 0, -4500)
        self._sl(l, "mic_ns_ratio", "Ratio", 1, 10, 3)
        self._sl(l, "mic_ns_attack", "Attack", 1, 50, 2)
        self._sl(l, "mic_ns_release", "Release", 10, 500, 100)
        mg.addWidget(g, 0, 0)

        # Howling / Freq / Silence
        g = QGroupBox("Ochrona / Detekcja"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_howl_en", "Howling Control", True)
        self._sl(l, "mic_howl_mode", "Tryb (1=Mild,2=Massive,3=Strong)", 1, 3, 2)
        l.addWidget(hsep())
        self._ck(l, "mic_freq_en", "Freq. Shifter", True)
        self._sl(l, "mic_freq_delta", "Delta-F", -100, 100, 0, " Hz")
        l.addWidget(hsep())
        self._ck(l, "mic_silence_en", "Silence Detector", True)
        self._sl(l, "mic_silence_amp", "Amplitude", 0, 10, 0)
        mg.addWidget(g, 0, 1)

        # Echo
        g = QGroupBox("Echo"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_echo_en", "Włącz", False)
        self._sl(l, "mic_echo_cf", "Cutoff", 100, 16000, 8000, " Hz")
        self._sl(l, "mic_echo_att", "Attenuation", 0, 65535, 14636)
        self._sl(l, "mic_echo_delay", "Delay", 10, 500, 256, " ms")
        self._sl(l, "mic_echo_max_delay", "Max Delay", 50, 1000, 350, " ms")
        self._sl(l, "mic_echo_hq", "High Quality", 0, 1, 0)
        self._sl(l, "mic_echo_dry", "Dry", 0, 100, 100)
        self._sl(l, "mic_echo_wet", "Wet", 0, 100, 100)
        mg.addWidget(g, 1, 0)

        # Reverb (Room)
        g = QGroupBox("Reverb (Room)"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_rev_en", "Włącz", False)
        self._sl(l, "mic_rev_dry", "Dry", 0, 100, 100)
        self._sl(l, "mic_rev_wet", "Wet", 0, 100, 100)
        self._sl(l, "mic_rev_width", "Width", 0, 100, 100)
        self._sl(l, "mic_rev_room", "Room size", 0, 100, 65)
        self._sl(l, "mic_rev_damp", "Damping", 0, 100, 35)
        mg.addWidget(g, 1, 1)

        # Plate Reverb
        g = QGroupBox("Plate Reverb"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_plate_en", "Włącz", True)
        self._sl(l, "mic_plate_hcf", "High Cutoff", 1000, 20000, 8000, " Hz")
        self._sl(l, "mic_plate_mod", "Modulation", 0, 1, 1)
        self._sl(l, "mic_plate_pre", "Pre-delay", 0, 5000, 2500)
        self._sl(l, "mic_plate_diff", "Diffusion", 0, 100, 60)
        self._sl(l, "mic_plate_decay", "Decay", 0, 100, 65)
        self._sl(l, "mic_plate_damp", "Damping", 1000, 20000, 5000, " Hz")
        self._sl(l, "mic_plate_wet", "Wet/Dry", 0, 100, 55)
        mg.addWidget(g, 2, 0)

        # Pitch + AutoTune
        g = QGroupBox("Pitch / Auto Tune"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_pitch_en", "Pitch Shifter", False)
        self._sl(l, "mic_pitch_key", "Key (semitony)", 30, 90, 70)
        l.addWidget(hsep())
        self._ck(l, "mic_autotune_en", "Auto Tune", False)
        self._sl(l, "mic_autotune_key", "Key", 30, 110, 98)
        self._sl(l, "mic_autotune_snap", "Snap", 0, 200, 117)
        self._sl(l, "mic_autotune_acc", "Accuracy", 0, 1, 0)
        mg.addWidget(g, 2, 1)

        # Voice Changer
        g = QGroupBox("Voice Changer"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_vch_en", "Włącz", False)
        self._sl(l, "mic_vch_pitch", "Pitch", 50, 400, 200)
        self._sl(l, "mic_vch_formant", "Formant", 50, 200, 150)
        mg.addWidget(g, 3, 0)

        # Mic DRC
        g = QGroupBox("Mic DRC"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mic_drc_en", "Włącz", False)
        self._sl(l, "mic_drc_thr1", "Threshold 1", -9999, 0, 0)
        self._sl(l, "mic_drc_thr2", "Threshold 2", -9999, 0, 0)
        self._sl(l, "mic_drc_ratio1", "Ratio 1", 1, 200, 100)
        self._sl(l, "mic_drc_ratio2", "Ratio 2", 1, 200, 100)
        self._sl(l, "mic_drc_att", "Attack", 1, 100, 1)
        self._sl(l, "mic_drc_rel", "Release", 10, 5000, 1000)
        mg.addWidget(g, 3, 1)

        r.addLayout(mg)
        row = QHBoxLayout()
        b = accent_btn("▶  Zastosuj MIC")
        b.setToolTip("Wyślij wszystkie parametry mikrofonu do DSP")
        b.clicked.connect(self._apply_mic)
        row.addWidget(b); row.addStretch(); r.addLayout(row)
        r.addWidget(hsep())

        # ══════════════════════════════════════════════════════════════════
        # 🎵 MUZYKA
        # ══════════════════════════════════════════════════════════════════
        r.addWidget(section_lbl("🎵  MUZYKA"))
        mug = QGridLayout(); mug.setSpacing(6)

        # Music NS
        g = QGroupBox("Noise Suppressor"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_ns_en", "Włącz", True)
        self._sl(l, "mus_ns_thr", "Próg", -9999, 0, -6500)
        self._sl(l, "mus_ns_ratio", "Ratio", 1, 10, 2)
        self._sl(l, "mus_ns_attack", "Attack", 1, 50, 1)
        self._sl(l, "mus_ns_release", "Release", 10, 500, 100)
        mug.addWidget(g, 0, 0)

        # Virtual Bass
        g = QGroupBox("Virtual Bass"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_vbass_en", "Virtual Bass", True)
        self._sl(l, "mus_vbass_cut", "Cutoff", 20, 300, 80, " Hz")
        self._sl(l, "mus_vbass_int", "Intensity", 0, 100, 7)
        self._ck(l, "mus_vbass_enh", "Enhanced", True)
        l.addWidget(hsep())
        self._ck(l, "mus_vbass_cls_en", "Bass Classic", False)
        self._sl(l, "mus_vbass_cls_cut", "Cutoff (classic)", 20, 200, 50, " Hz")
        self._sl(l, "mus_vbass_cls_int", "Intensity (classic)", 0, 50, 12)
        mug.addWidget(g, 0, 1)

        # 3D / Stereo / Phase
        g = QGroupBox("3D / Stereo / Phase"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_3d_en", "3D Surround", False)
        self._sl(l, "mus_3d_int", "Intensity", 0, 100, 80)
        l.addWidget(hsep())
        self._ck(l, "mus_3dplus_en", "3D Plus", False)
        self._sl(l, "mus_3dplus_int", "Intensity", 0, 100, 70)
        l.addWidget(hsep())
        self._ck(l, "mus_stereo_en", "Stereo Widener", True)
        self._sl(l, "mus_stereo_shp", "Shaping (1=Narrow…3=Wide)", 1, 3, 1)
        l.addWidget(hsep())
        self._ck(l, "mus_phase_en", "Phase Shift", False)
        self._sl(l, "mus_phase_diff", "Diff (0=0°, 1=180°)", 0, 1, 0)
        mug.addWidget(g, 1, 0)

        # Voice Cut / Remove / Pitch
        g = QGroupBox("Voice / Pitch"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_vcut_en", "Voice Cut", False)
        self._sl(l, "mus_vcut_val", "Mix %", 0, 100, 100)
        l.addWidget(hsep())
        self._ck(l, "mus_vrem_en", "Voice Remove", False)
        self._sl(l, "mus_vrem_lo", "Low freq", 20, 2000, 200, " Hz")
        self._sl(l, "mus_vrem_hi", "High freq", 5000, 20000, 15000, " Hz")
        l.addWidget(hsep())
        self._ck(l, "mus_pitch_en", "Pitch Shifter", False)
        self._sl(l, "mus_pitch_key", "Key (cents)", 30, 110, 70)
        mug.addWidget(g, 1, 1)

        # Music DRC
        g = QGroupBox("Music DRC"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_drc_en", "Włącz", True)
        self._sl(l, "mus_drc_xfreq", "Crossover freq", 20, 2000, 300, " Hz")
        self._sl(l, "mus_drc_thr1", "Threshold 1", -9999, 0, 0)
        self._sl(l, "mus_drc_thr2", "Threshold 2", -9999, 0, 0)
        self._sl(l, "mus_drc_ratio1", "Ratio 1", 1, 200, 100)
        self._sl(l, "mus_drc_ratio2", "Ratio 2", 1, 200, 100)
        self._sl(l, "mus_drc_att1", "Attack", 1, 100, 1)
        self._sl(l, "mus_drc_rel", "Release", 10, 5000, 1000)
        mug.addWidget(g, 2, 0)

        # Delay / Exciter
        g = QGroupBox("Delay / Exciter"); l = QVBoxLayout(g); l.setSpacing(3)
        self._ck(l, "mus_delay_en", "PCM Delay", False)
        self._sl(l, "mus_delay_val", "Delay", 0, 500, 10, " ms")
        l.addWidget(hsep())
        self._ck(l, "mus_exciter_en", "Exciter", False)
        self._sl(l, "mus_exciter_cut", "Cutoff", 200, 16000, 1000, " Hz")
        self._sl(l, "mus_exciter_dry", "Dry", 0, 100, 100)
        self._sl(l, "mus_exciter_wet", "Wet", 0, 100, 100)
        mug.addWidget(g, 2, 1)

        r.addLayout(mug)
        row = QHBoxLayout()
        b = accent_btn("▶  Zastosuj MUSIC")
        b.setToolTip("Wyślij wszystkie parametry muzyki do DSP")
        b.clicked.connect(self._apply_music)
        row.addWidget(b); row.addStretch(); r.addLayout(row)
        r.addWidget(hsep())

        # ══════════════════════════════════════════════════════════════════
        # 🔊 WZMACNIANIE (Gain)
        # ══════════════════════════════════════════════════════════════════
        r.addWidget(section_lbl("🔊  WZMACNIANIE (Gain)"))
        gg = QGridLayout(); gg.setSpacing(6)

        g = QGroupBox("Mic — kanały"); l = QVBoxLayout(g); l.setSpacing(3)
        self._sl(l, "gain_mic", "Mic Out", 0, 8192, 4096)
        self._sl(l, "gain_mic_bypass", "Mic Bypass", 0, 8192, 4096)
        self._sl(l, "gain_mic_echo", "Mic Echo", 0, 8192, 4096)
        self._sl(l, "gain_mic_rev", "Mic Reverb", 0, 8192, 4096)
        gg.addWidget(g, 0, 0)

        g = QGroupBox("Wejścia"); l = QVBoxLayout(g); l.setSpacing(3)
        self._sl(l, "gain_bt", "Bluetooth", 0, 8192, 4096)
        self._sl(l, "gain_usb", "USB Card", 0, 8192, 4096)
        self._sl(l, "gain_i2s", "I2S", 0, 8192, 4096)
        self._sl(l, "gain_spdif", "SPDIF", 0, 8192, 4096)
        gg.addWidget(g, 0, 1)

        g = QGroupBox("Wyjście muzyki"); l = QVBoxLayout(g); l.setSpacing(3)
        self._sl(l, "gain_mus", "Music Out", 0, 8192, 4096)
        gg.addWidget(g, 1, 0)

        r.addLayout(gg)
        row = QHBoxLayout()
        b = accent_btn("▶  Zastosuj GAIN")
        b.setToolTip("Wyślij wszystkie wartości wzmocnienia do DSP")
        b.clicked.connect(self._apply_gains)
        row.addWidget(b); row.addStretch(); r.addLayout(row)

        r.addStretch()

    # ── Wysyłanie modułów do DSP ──

    def _make_frames(self, modules_map):
        """
        Z mapowania (prefix → (mod_id, [keys])) buduje listę (label, frame_bytes).
        """
        frames = []
        for prefix, (mod_id, keys) in modules_map.items():
            vals = [self._val(k) for k in keys]
            params = u16(vals)
            frame = build_frame(mod_id, params)
            frames.append((prefix, frame))
        return frames

    def _apply_mic(self):
        frames = self._make_frames(MIC_MODULES)
        self.log.emit(f"→ Wysyłanie {len(frames)} modułów MIC…", "info")
        self.send_frames.emit([(f, lbl) for lbl, f in frames])

    def _apply_music(self):
        frames = self._make_frames(MUS_MODULES)
        self.log.emit(f"→ Wysyłanie {len(frames)} modułów MUSIC…", "info")
        self.send_frames.emit([(f, lbl) for lbl, f in frames])

    def _apply_gains(self):
        """
        Gain moduły w SDK mają format: [enable, pregain, mute, channel]
        enable=1, mute=0, channel=2 (stereo).  GUI kontroluje tylko pregain.
        """
        frames = []
        for key, (mod_id, label) in GAIN_MODULES.items():
            pregain = self._val(key)
            params = u16([1, pregain, 0, 2])  # enable=1, pregain, mute=0, stereo=2
            frame = build_frame(mod_id, params)
            frames.append((label, frame))
        self.log.emit(f"→ Wysyłanie {len(frames)} modułów GAIN…", "info")
        self.send_frames.emit([(f, lbl) for lbl, f in frames])

    def _apply_all(self):
        """Wyślij wszystko: MIC + MUSIC + GAIN."""
        self.log.emit("══ Zastosuj WSZYSTKO ══", "info")
        self._apply_mic()
        self._apply_music()
        self._apply_gains()
        self.log.emit("══ Zakończono ══", "ok")


# ─────────────────────────────────────────────────────────────────────────────
# INI Upload Tab
# ─────────────────────────────────────────────────────────────────────────────

class IniUploadTab(QWidget):
    log = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setSpacing(8)

        row = QHBoxLayout()
        self._path_edit = QLineEdit(); self._path_edit.setPlaceholderText("Ścieżka do pliku .ini …")
        self._path_edit.setMinimumWidth(400)
        row.addWidget(self._path_edit, 1)
        browse = QPushButton("📁 Przeglądaj…"); browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["#", "Moduł", "Parametry", "Rozmiar"])
        self._tree.setColumnWidth(0, 40); self._tree.setColumnWidth(1, 200)
        self._tree.setAlternatingRowColors(True)
        lay.addWidget(self._tree, 1)

        self._progress = QProgressBar(); self._progress.setVisible(False)
        lay.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._send_btn = accent_btn("▶  Wyślij do DSP")
        self._send_btn.setEnabled(False); self._send_btn.clicked.connect(self._send)
        btn_row.addWidget(self._send_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._frames = []; self._worker = None

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik INI", "", "INI (*.ini);;Wszystkie (*)")
        if path:
            self._path_edit.setText(path)
            self._load_ini(path)

    def _load_ini(self, path):
        self._tree.clear(); self._frames = []; self._send_btn.setEnabled(False)
        try:
            parsed = parse_ini(path)
        except Exception as e:
            self.log.emit(f"✗ Błąd parsowania INI: {e}", "err"); return
        for idx, (mid, name, frame) in enumerate(parsed):
            item = QTreeWidgetItem([
                str(idx),
                f"0x{mid:02X}  {name}",
                frame.hex(" ").upper(),
                f"{len(frame)} B"
            ])
            self._tree.addTopLevelItem(item)
        self._frames = parsed
        self._send_btn.setEnabled(len(parsed) > 0)
        self.log.emit(f"✓ Wczytano {len(parsed)} modułów z {os.path.basename(path)}", "ok")

    def _send(self):
        if not self._frames or self._worker and self._worker.isRunning():
            return
        frames = [(mid, name, frame) for mid, name, frame in self._frames]
        indices = list(range(len(frames)))
        self._progress.setVisible(True); self._progress.setValue(0)
        self._progress.setMaximum(len(frames))
        self._worker = IniSendWorker(frames, indices)
        self._worker.frame_done.connect(self._on_frame_done)
        self._worker.progress.connect(self._progress.setValue)
        self._worker.log.connect(self.log.emit)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_frame_done(self, idx, text, level):
        item = self._tree.topLevelItem(idx)
        if item:
            item.setText(2, text)

    def _on_finished(self):
        self._progress.setVisible(False)
        self.log.emit("✓ INI upload zakończony.", "ok")


# ─────────────────────────────────────────────────────────────────────────────
# USB Sniffer Tab
# ─────────────────────────────────────────────────────────────────────────────

class SnifferTab(QWidget):
    log = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setSpacing(8)

        ctrl = QHBoxLayout()
        self._start_btn = accent_btn("▶  Start nasłuchu")
        self._start_btn.clicked.connect(self._toggle)
        ctrl.addWidget(self._start_btn)
        self._clear_btn = QPushButton("🗑 Wyczyść"); self._clear_btn.clicked.connect(self._clear)
        ctrl.addWidget(self._clear_btn)
        ctrl.addStretch()
        self._count_lbl = QLabel("Pakietów: 0"); self._count_lbl.setObjectName("mono")
        ctrl.addWidget(self._count_lbl)
        lay.addLayout(ctrl)

        self._view = QTextEdit(); self._view.setReadOnly(True)
        self._view.setFont(QFont("DejaVu Sans Mono", 9))
        self._view.setStyleSheet("background:#060a12;color:#8fa0c0;")
        lay.addWidget(self._view, 1)

        self._worker = None; self._pkt_count = 0

    def _toggle(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop(); self._worker.wait(2000)
            self._start_btn.setText("▶  Start nasłuchu")
            self.log.emit("⏹ Nasłuch zatrzymany.", "dim")
        else:
            self._worker = SniffWorker()
            self._worker.packet.connect(self._on_packet)
            self._worker.stopped.connect(self._on_stopped)
            self._worker.start()
            self._start_btn.setText("⏹  Stop")
            self.log.emit("▶ Nasłuch HID uruchomiony.", "info")

    def _on_packet(self, data, ts):
        self._pkt_count += 1
        hex_str = " ".join(f"{b:02X}" for b in data if b != 0)
        if not hex_str:
            return
        # Spróbuj rozpoznać ramkę ACP
        prefix = ""
        if len(data) >= 4 and data[0] == 0xA5 and data[1] == 0x5A:
            prefix = " ◀ACP "
        self._view.append(f"<span style='color:#4a5a78'>[{ts}]</span> {prefix}"
                          f"<span style='color:#00aacc'>{hex_str}</span>")
        self._count_lbl.setText(f"Pakietów: {self._pkt_count}")

    def _on_stopped(self):
        self._start_btn.setText("▶  Start nasłuchu")

    def _clear(self):
        self._view.clear(); self._pkt_count = 0
        self._count_lbl.setText("Pakietów: 0")


# ─────────────────────────────────────────────────────────────────────────────
# Device Info Tab
# ─────────────────────────────────────────────────────────────────────────────

class DeviceInfoTab(QWidget):
    log = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setSpacing(12)

        # Firmware info
        fg = QGroupBox("Firmware"); fl = QGridLayout(fg); fl.setSpacing(6)
        self._fw_labels = {}
        for row, (key, label) in enumerate([("chip","Chip"),("fw_ver","FW Version"),
                                             ("fx_ver","Effect Lib Version")]):
            l = QLabel(label + ":"); l.setObjectName("dim"); l.setFixedWidth(160)
            v = QLabel("—"); v.setObjectName("val")
            fl.addWidget(l, row, 0); fl.addWidget(v, row, 1)
            self._fw_labels[key] = v
        lay.addWidget(fg)

        # CPU / Memory
        rg = QGroupBox("Zasoby (cyklicznie)"); rl = QGridLayout(rg); rl.setSpacing(6)
        self._cpu_bar = QProgressBar(); self._cpu_bar.setFormat("CPU: %v/%m %")
        self._mem_bar = QProgressBar(); self._mem_bar.setFormat("MEM: %v/%m %")
        rl.addWidget(QLabel("CPU:"), 0, 0)
        rl.addWidget(self._cpu_bar, 0, 1)
        rl.addWidget(QLabel("Memory:"), 1, 0)
        rl.addWidget(self._mem_bar, 1, 1)
        lay.addWidget(rg)

        # Device list
        dg = QGroupBox("Urządzenia USB"); dl = QVBoxLayout(dg)
        self._dev_list = QLabel("—"); self._dev_list.setObjectName("mono")
        self._dev_list.setWordWrap(True)
        dl.addWidget(self._dev_list)
        refresh = QPushButton("🔄 Odśwież listę"); refresh.clicked.connect(self._refresh_devs)
        dl.addWidget(refresh)
        lay.addWidget(dg)

        lay.addStretch()
        self._worker = None

    def start_monitoring(self):
        self._refresh_devs()
        self._worker = DeviceInfoWorker(interval_ms=3000)
        self._worker.fw_info.connect(self._on_fw)
        self._worker.cpu_info.connect(self._on_cpu)
        self._worker.error.connect(lambda e: self.log.emit(f"⚠ {e}", "warn"))
        self._worker.start()

    def stop_monitoring(self):
        if self._worker:
            self._worker.stop(); self._worker.wait(2000); self._worker = None

    def _on_fw(self, info):
        for key, lbl in self._fw_labels.items():
            lbl.setText(str(info.get(key, "—")))

    def _on_cpu(self, info):
        cu = info.get("cpu_used", 0); ct = info.get("cpu_total", 1)
        mu = info.get("mem_used", 0); mt = info.get("mem_total", 1)
        self._cpu_bar.setRange(0, ct); self._cpu_bar.setValue(cu)
        self._mem_bar.setRange(0, mt); self._mem_bar.setValue(mu)

    def _refresh_devs(self):
        devs = scan_hid_devices()
        lines = []
        for d in devs:
            if d.get("match"):
                lines.append(f"✓ {d['path']}\n  {d['devname']}  (VID:{d['vid']} PID:{d['pid']})")
            else:
                lines.append(f"✗ {d['devname']}")
        self._dev_list.setText("\n\n".join(lines) if lines else "—")


# ─────────────────────────────────────────────────────────────────────────────
# Główne okno
# ─────────────────────────────────────────────────────────────────────────────

class AcpMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ACP DSP Workbench — MVSilicon BP1048B2")
        self.resize(1280, 820)

        # Centralny widget z zakładkami
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        # Status bar
        self._status = QStatusBar(); self.setStatusBar(self._status)
        self._status.showMessage("Rozłączono")

        # Log panel (dolny)
        self._log = LogEdit()
        self._log.setMaximumHeight(180)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._tabs)
        splitter.addWidget(self._log)
        self.setCentralWidget(splitter)

        # ── Zakładki ──
        self._fx_tab = FxMixerTab()
        self._ini_tab = IniUploadTab()
        self._sniff_tab = SnifferTab()
        self._info_tab = DeviceInfoTab()

        self._tabs.addTab(self._fx_tab, "🎛  FX Mixer")
        self._tabs.addTab(self._ini_tab, "📄  INI Upload")
        self._tabs.addTab(self._sniff_tab, "📡  USB Sniffer")
        self._tabs.addTab(self._info_tab, "ℹ️  Device Info")

        # ── Połączenie sygnałów log ──
        self._fx_tab.log.connect(self._log.append_colored)
        self._ini_tab.log.connect(self._log.append_colored)
        self._sniff_tab.log.connect(self._log.append_colored)
        self._info_tab.log.connect(self._log.append_colored)

        # ── QueuedSendWorker — stały wątek wysyłania ──
        self._send_worker = QueuedSendWorker()
        self._send_worker.frame_sent.connect(self._on_frame_sent)
        self._send_worker.frame_error.connect(self._on_frame_error)
        self._send_worker.start()

        # Połącz send_frames z kolejką
        self._fx_tab.send_frames.connect(self._enqueue_frames)

        # ── ConnectWorker ──
        self._conn_worker = None
        self._connected = False

        # Auto-connect
        QTimer.singleShot(500, self._auto_connect)

    def _auto_connect(self):
        self._log.append_colored("Szukam urządzenia 8888:1719…", "info")
        self._conn_worker = ConnectWorker()
        self._conn_worker.connected.connect(self._on_connected)
        self._conn_worker.failed.connect(self._on_connect_failed)
        self._conn_worker.start()

    def _on_connected(self):
        self._connected = True
        self._status.showMessage("✔ Połączono z MVSilicon BP1048B2")
        self._log.append_colored("✔ Połączono!", "ok")
        self._info_tab.start_monitoring()

    def _on_connect_failed(self, msg):
        self._connected = False
        self._status.showMessage("✗ Rozłączono")
        self._log.append_colored(f"✗ {msg}", "err")
        self._log.append_colored("Podłącz urządzenie i zrestartuj GUI.", "warn")

    def _enqueue_frames(self, frame_list):
        """frame_list: list[(bytes_frame, str_label)]"""
        for frame, label in frame_list:
            self._send_worker.enqueue(frame, label)

    def _on_frame_sent(self, label, tx_len, frame_bytes, resp):
        hex_short = frame_bytes.hex(" ").upper()
        if len(hex_short) > 60:
            hex_short = hex_short[:57] + "…"
        self._log.append_colored(f"  ✓ [{label}] {tx_len}B → {hex_short}  ← {resp}", "tx")

    def _on_frame_error(self, label, resp):
        self._log.append_colored(f"  ✗ [{label}] {resp}", "err")

    def closeEvent(self, event):
        if self._send_worker:
            self._send_worker.stop(); self._send_worker.wait(2000)
        self._info_tab.stop_monitoring()
        if self._conn_worker and self._conn_worker.isRunning():
            self._conn_worker.quit(); self._conn_worker.wait(2000)
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("DejaVu Sans", 10))

    win = AcpMainWindow()
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
