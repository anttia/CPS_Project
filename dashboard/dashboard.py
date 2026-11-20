#!/usr/bin/env python3
"""Luxlampun PC-dashboard, vaihe 1.

Lukee Arduinolta USB-sarjaportin kautta rivejä muodossa "pir,valo" (esim. "1,523")
ja näyttää:
  - milloin ihminen on viimeksi havaittu
  - valoanturin nykyisen arvon
  - valoanturin arvon kaaviona viimeisten minuuttien ajalta (liike merkittynä taustaan)

Jos sarjaporttia ei saada auki tai data lakkaa tulemasta, dashboard näyttää siitä
punaisen ilmoituksen ja yrittää yhdistää uudelleen automaattisesti.

Kaikki vastaanotettu data tallentuu myös CSV-tiedostoon.

Käyttö:
    pip install pyserial matplotlib
    python dashboard.py                 # etsii Arduinon sarjaportin automaattisesti
    python dashboard.py --port COM3     # tai portti käsin (Linux: /dev/ttyACM0)
    python dashboard.py --demo          # testidata ilman Arduinoa
"""
import argparse
import csv
import math
import random
import sys
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Patch

HISTORY_S = 120          # kuinka monta sekuntia kaavio näyttää
ADC_MAX = 1023
NO_DATA_S = 3            # näin pitkä tauko datassa näytetään virheenä
RETRY_S = 2              # kuinka usein porttia yritetään avata uudelleen

# Tumma teema
BG = "#111418"
PANEL = "#181c22"
TEXT = "#e6e9ef"
MUTED = "#9aa4b2"
GRID = "#2c333d"
LIGHT_COLOR = "#4ea1ff"
MOTION_COLOR = "#f59e0b"
ERROR_BG = "#5c1a1a"
ERROR_EDGE = "#ef4444"


class State:
    """Viimeisimmät mittaukset, jaettu lukijasäikeen ja piirron kesken."""

    def __init__(self):
        self.lock = threading.Lock()
        self.times = deque()
        self.light = deque()
        self.pir = deque()
        self.last_seen = None        # time.time() viimeisestä liikkeestä
        self.last_data = None        # time.time() viimeisestä kelvollisesta rivistä
        self.connected_at = None
        self.status = "Odotetaan dataa..."
        self.error = None            # näkyvä virheilmoitus tai None

    def add(self, t, pir, light):
        with self.lock:
            self.times.append(t)
            self.light.append(light)
            self.pir.append(pir)
            self.last_data = t
            if pir:
                self.last_seen = t
            while self.times and self.times[0] < t - HISTORY_S:
                self.times.popleft()
                self.light.popleft()
                self.pir.popleft()

    def set_connection(self, status, error=None, connected=False):
        with self.lock:
            self.status = status
            self.error = error
            self.connected_at = time.time() if connected else None

    def snapshot(self):
        with self.lock:
            return (list(self.times), list(self.light), list(self.pir), self.last_seen,
                    self.last_data, self.connected_at, self.status, self.error)


def parse_line(line):
    """Palauttaa (pir, valo) tai None, jos rivi on vajaa tai rikki."""
    parts = line.strip().split(",")
    if len(parts) != 2:
        return None
    try:
        pir, light = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if pir not in (0, 1) or not 0 <= light <= ADC_MAX:
        return None
    return pir, light


def explain_open_error(port, message):
    """Muuttaa pyserialin virheilmoituksen ohjeeksi käyttäjälle."""
    text = message.lower()
    if "access is denied" in text or "busy" in text or "errno 16" in text:
        return (f"Sarjaporttia {port} ei saada auki: se on käytössä toisessa ohjelmassa.\n"
                "Sulje Arduino IDE:n Serial Monitor (tai muu sarjaterminaali).\n"
                "Dashboard yhdistää automaattisesti, kun portti vapautuu.")
    if "permission denied" in text:
        return (f"Ei oikeutta avata sarjaporttia {port}.\n"
                "Linux: lisää käyttäjä dialout-ryhmään ja kirjaudu uudelleen sisään.")
    if "cannot find" in text or "no such file" in text or "filenotfound" in text:
        return (f"Sarjaporttia {port} ei löydy.\n"
                "Onko Arduino kytketty USB:llä? Tarkista portin nimi (--port).")
    return f"Sarjaporttia {port} ei saada auki:\n{message}"


def find_port():
    """Etsii todennäköisen Arduinon sarjaportin."""
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        text = f"{p.description} {p.manufacturer or ''}".lower()
        if "arduino" in text or "ch340" in text or "medbg" in text:
            return p.device
    for p in ports:
        if "ACM" in p.device or "usbmodem" in p.device or "usbserial" in p.device:
            return p.device
    if len(ports) == 1:
        return ports[0].device
    names = ", ".join(p.device for p in ports) or "ei yhtään"
    sys.exit(f"Arduinoa ei löytynyt automaattisesti. Portit: {names}. Anna portti: --port COM3")


def serial_reader(port, baud, state, log):
    import serial
    while True:
        try:
            ser = serial.Serial(port, baud, timeout=1)
        except (serial.SerialException, OSError) as e:
            state.set_connection(f"Ei yhteyttä: {port}", explain_open_error(port, str(e)))
            time.sleep(RETRY_S)
            continue

        state.set_connection(f"Yhdistetty: {port}", connected=True)
        try:
            with ser:
                while True:
                    raw = ser.readline().decode("ascii", errors="ignore")
                    sample = parse_line(raw)
                    if sample is None:
                        continue      # Arduinon käynnistyksen ensimmäiset rivit voivat olla vajaita
                    now = time.time()
                    state.add(now, *sample)
                    log.writerow([datetime.fromtimestamp(now).isoformat(timespec="milliseconds"), *sample])
        except (serial.SerialException, OSError) as e:
            state.set_connection(f"Ei yhteyttä: {port}",
                                 f"Yhteys sarjaporttiin {port} katkesi.\n"
                                 f"Onko USB-kaapeli irronnut? ({e})")
            time.sleep(RETRY_S)


def demo_reader(state, log):
    """Testidata: valo vaihtelee hitaasti, ja liikettä tulee välillä."""
    state.set_connection("Demotila: testidata", connected=True)
    pir_until = 0
    while True:
        now = time.time()
        if random.random() < 0.01:
            pir_until = now + random.uniform(3, 10)
        pir = 1 if now < pir_until else 0
        light = int(500 + 250 * math.sin(now / 15) + random.gauss(0, 8))
        light = max(0, min(ADC_MAX, light))
        state.add(now, pir, light)
        log.writerow([datetime.fromtimestamp(now).isoformat(timespec="milliseconds"), pir, light])
        time.sleep(0.2)


def format_last_seen(last_seen, now):
    if last_seen is None:
        return "Ihmistä ei ole vielä havaittu"
    ago = int(now - last_seen)
    clock = datetime.fromtimestamp(last_seen).strftime("%H:%M:%S")
    if ago < 1:
        return f"Ihminen havaittu nyt ({clock})"
    if ago < 60:
        return f"Ihminen viimeksi havaittu {clock} ({ago} s sitten)"
    return f"Ihminen viimeksi havaittu {clock} ({ago // 60} min {ago % 60} s sitten)"


def build_figure():
    plt.style.use("dark_background")
    fig, (ax_info, ax) = plt.subplots(2, 1, figsize=(9, 6), gridspec_kw={"height_ratios": [1, 3]})
    fig.patch.set_facecolor(BG)
    fig.canvas.manager.set_window_title("Luxlamppu - dashboard")
    ax_info.axis("off")
    seen_text = ax_info.text(0.0, 0.75, "", fontsize=14, fontweight="bold", color=TEXT,
                             transform=ax_info.transAxes)
    light_text = ax_info.text(0.0, 0.35, "", fontsize=13, color=TEXT, transform=ax_info.transAxes)
    status_text = ax_info.text(0.0, 0.0, "", fontsize=9, color=MUTED, transform=ax_info.transAxes)

    ax.set_facecolor(PANEL)
    (line,) = ax.plot([], [], color=LIGHT_COLOR, linewidth=1.8, label="valoanturi")
    ax.set_xlim(-HISTORY_S, 0)
    ax.set_ylim(0, ADC_MAX)
    ax.set_xlabel("aika (s, 0 = nyt)", color=MUTED)
    ax.set_ylabel("valoanturin arvo (ADC 0–1023)", color=MUTED)
    ax.tick_params(colors=MUTED)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(color=GRID, alpha=0.8)
    ax.legend(handles=[line, Patch(color=MOTION_COLOR, alpha=0.3, label="liike havaittu")],
              loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    # Virheilmoitus näkyy kaavion päällä vain, kun jokin on vialla
    error_text = fig.text(0.5, 0.45, "", ha="center", va="center", fontsize=13, fontweight="bold",
                          color="white", zorder=10, linespacing=1.6,
                          bbox=dict(boxstyle="round,pad=0.9", facecolor=ERROR_BG,
                                    edgecolor=ERROR_EDGE, linewidth=2))
    error_text.set_visible(False)

    fig.tight_layout()
    return fig, ax, line, seen_text, light_text, status_text, error_text


def make_updater(state, ax, line, seen_text, light_text, status_text, error_text):
    motion_fill = [None]

    def update(_frame):
        times, light, pir, last_seen, last_data, connected_at, status, error = state.snapshot()
        now = time.time()
        rel = [t - now for t in times]
        line.set_data(rel, light)

        if motion_fill[0] is not None:
            motion_fill[0].remove()
        motion_fill[0] = ax.fill_between(rel, 0, ADC_MAX, where=[p == 1 for p in pir],
                                         step="post", color=MOTION_COLOR, alpha=0.3, linewidth=0)

        seen_text.set_text(format_last_seen(last_seen, now))
        if light:
            motion = "kyllä" if pir[-1] else "ei"
            light_text.set_text(f"Valoisuus nyt: {light[-1]} / {ADC_MAX}      Liikettä nyt: {motion}")
        else:
            light_text.set_text("Valoisuus nyt: –")
        status_text.set_text(status)

        # Portti on auki, mutta dataa ei tule: väärä ohjelma, väärä nopeus tai kortti jumissa
        if error is None and connected_at is not None:
            since = now - (last_data if last_data and last_data > connected_at else connected_at)
            if since > NO_DATA_S:
                error = (f"Ei dataa Arduinolta {int(since)} sekuntiin.\n"
                         "Onko kortille ladattu oikea ohjelma ja nopeus 9600 baudia?")

        error_text.set_visible(error is not None)
        if error is not None:
            error_text.set_text(error)
        return line, seen_text, light_text, status_text, error_text

    return update


def main():
    parser = argparse.ArgumentParser(description="Luxlampun PC-dashboard")
    parser.add_argument("--port", help="sarjaportti, esim. COM3 tai /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--demo", action="store_true", help="testidata ilman Arduinoa")
    parser.add_argument("--log", default=f"mittaus_{datetime.now():%Y%m%d_%H%M%S}.csv",
                        help="CSV-tiedosto, johon data tallennetaan")
    args = parser.parse_args()

    state = State()
    logfile = open(args.log, "w", newline="", buffering=1)
    log = csv.writer(logfile)
    log.writerow(["aika", "pir", "valo"])

    if args.demo:
        target, targs = demo_reader, (state, log)
    else:
        port = args.port or find_port()
        target, targs = serial_reader, (port, args.baud, state, log)
    threading.Thread(target=target, args=targs, daemon=True).start()

    fig, ax, line, seen_text, light_text, status_text, error_text = build_figure()
    update = make_updater(state, ax, line, seen_text, light_text, status_text, error_text)
    _anim = FuncAnimation(fig, update, interval=200, cache_frame_data=False)
    plt.show()
    logfile.close()


if __name__ == "__main__":
    main()
