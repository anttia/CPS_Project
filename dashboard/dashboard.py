#!/usr/bin/env python3
"""Luxlampun PC-dashboard.

Lukee Arduinolta USB-sarjaportin kautta rivejä muodossa
    pir,valo,kirkkaus,jannite_mV,virta_mA,teho_mW      (firmware/luxlamppu)
tai vanhemmassa muodossa
    pir,valo                                            (firmware/valo_liike_usb)
ja näyttää:
  - milloin ihminen on viimeksi havaittu
  - valoanturin arvon ja LED-nauhan todellisen kirkkauden kaaviona
  - INA219:n mittaaman jännitteen, virran ja tehon sekä kertyneen energian

Liukusäädin lähettää Arduinolle LED-kirkkauden komennolla "B,<0-100>". Kaavion
kirkkauskäyrä on Arduinon ilmoittama todellinen arvo, ja dashboard lähettää
komennon uudelleen, jos se ei vastaa liukusäädintä (esim. Arduinon uudelleenkäynnistyksen jälkeen).

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
from matplotlib.widgets import Button, Slider

HISTORY_S = 120          # kuinka monta sekuntia kaaviot näyttävät
ADC_MAX = 1023
NO_DATA_S = 3            # näin pitkä tauko datassa näytetään virheenä
RETRY_S = 2              # kuinka usein porttia yritetään avata uudelleen
SEND_MIN_INTERVAL_S = 0.15   # liukusäätimen komentoja korkeintaan tällä välillä
RESEND_S = 1.0           # komento lähetetään uudelleen, jos Arduino ei ole ottanut sitä käyttöön

# Tumma teema
BG = "#111418"
PANEL = "#181c22"
TEXT = "#e6e9ef"
MUTED = "#9aa4b2"
GRID = "#2c333d"
LIGHT_COLOR = "#4ea1ff"
BRIGHT_COLOR = "#c084fc"
POWER_COLOR = "#f472b6"
MOTION_COLOR = "#f59e0b"
ERROR_BG = "#5c1a1a"
ERROR_EDGE = "#ef4444"


class State:
    """Viimeisimmät mittaukset ja ohjaus, jaettu lukijasäikeen ja käyttöliittymän kesken."""

    def __init__(self):
        self.lock = threading.Lock()
        self.times = deque()
        self.light = deque()
        self.pir = deque()
        self.bright = deque()        # Arduinon ilmoittama kirkkaus (%) tai None
        self.power = deque()         # teho (mW) tai None
        self.latest = None           # viimeisin näyte (dict)
        self.last_seen = None        # time.time() viimeisestä liikkeestä
        self.last_data = None        # time.time() viimeisestä kelvollisesta rivistä
        self.connected_at = None
        self.status = "Odotetaan dataa..."
        self.error = None            # näkyvä virheilmoitus tai None
        self.energy_mwh = 0.0
        self._last_power_t = None

        self.desired = 0             # liukusäätimen kirkkaus (%)
        self.last_sent = None        # viimeksi lähetetty kirkkaus
        self.last_send_t = 0.0
        self.write = None            # funktio, joka lähettää rivin Arduinolle

    def add(self, t, sample):
        with self.lock:
            self.times.append(t)
            self.light.append(sample["light"])
            self.pir.append(sample["pir"])
            self.bright.append(sample["bright"])
            power = sample["mw"] if sample["mw"] is not None and sample["mw"] >= 0 else None
            self.power.append(power)
            if power is not None:
                if self._last_power_t is not None:
                    dt = min(t - self._last_power_t, 2.0)    # katkon yli ei integroida
                    self.energy_mwh += power * dt / 3600.0
                self._last_power_t = t
            self.latest = sample
            self.last_data = t
            if sample["pir"]:
                self.last_seen = t
            while self.times and self.times[0] < t - HISTORY_S:
                self.times.popleft()
                self.light.popleft()
                self.pir.popleft()
                self.bright.popleft()
                self.power.popleft()

    def set_connection(self, status, error=None, connected=False, write=None):
        with self.lock:
            self.status = status
            self.error = error
            self.connected_at = time.time() if connected else None
            self.write = write
            self.last_sent = None             # uusi yhteys: komento lähetetään uudelleen

    def set_desired(self, value):
        with self.lock:
            self.desired = int(value)

    def snapshot(self):
        with self.lock:
            return dict(times=list(self.times), light=list(self.light), pir=list(self.pir),
                        bright=list(self.bright), power=list(self.power), latest=self.latest,
                        last_seen=self.last_seen, last_data=self.last_data,
                        connected_at=self.connected_at, status=self.status, error=self.error,
                        energy_mwh=self.energy_mwh, desired=self.desired)


def parse_line(line):
    """Palauttaa näytteen (dict) tai None, jos rivi on vajaa tai rikki."""
    parts = line.strip().split(",")
    if len(parts) not in (2, 6):
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    pir, light = values[0], values[1]
    if pir not in (0, 1) or not 0 <= light <= ADC_MAX:
        return None
    sample = dict(pir=pir, light=light, bright=None, mv=None, ma=None, mw=None)
    if len(values) == 6:
        bright, mv, ma, mw = values[2:]
        if not 0 <= bright <= 100:
            return None
        sample.update(bright=bright, mv=mv, ma=ma, mw=mw)
    return sample


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


def log_sample(log, t, s):
    log.writerow([datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                  s["pir"], s["light"], s["bright"], s["mv"], s["ma"], s["mw"]])


def serial_reader(port, baud, state, log):
    import serial
    while True:
        try:
            ser = serial.Serial(port, baud, timeout=1)
        except (serial.SerialException, OSError) as e:
            state.set_connection(f"Ei yhteyttä: {port}", explain_open_error(port, str(e)))
            time.sleep(RETRY_S)
            continue

        write_lock = threading.Lock()

        def write(line):
            with write_lock:
                ser.write((line + "\n").encode("ascii"))

        state.set_connection(f"Yhdistetty: {port}", connected=True, write=write)
        try:
            with ser:
                while True:
                    raw = ser.readline().decode("ascii", errors="ignore")
                    sample = parse_line(raw)
                    if sample is None:
                        continue      # Arduinon käynnistyksen ensimmäiset rivit voivat olla vajaita
                    now = time.time()
                    state.add(now, sample)
                    log_sample(log, now, sample)
        except (serial.SerialException, OSError) as e:
            state.set_connection(f"Ei yhteyttä: {port}",
                                 f"Yhteys sarjaporttiin {port} katkesi.\n"
                                 f"Onko USB-kaapeli irronnut? ({e})")
            time.sleep(RETRY_S)


def demo_reader(state, log):
    """Testidata: valo vaihtelee hitaasti, liikettä tulee välillä, ja nauha tottelee komentoja."""
    sim = {"bright": 0}

    def write(line):
        if line.startswith("B,"):
            sim["bright"] = max(0, min(100, int(line[2:])))

    state.set_connection("Demotila: testidata", connected=True, write=write)
    pir_until = 0
    while True:
        now = time.time()
        if random.random() < 0.01:
            pir_until = now + random.uniform(3, 10)
        pir = 1 if now < pir_until else 0
        light = int(60 + 40 * math.sin(now / 15) + 1.2 * sim["bright"] + random.gauss(0, 3))
        ma = int(39 + 8.2 * sim["bright"] + random.gauss(0, 2))
        mv = int(5000 - 0.1 * ma)
        sample = dict(pir=pir, light=max(0, min(ADC_MAX, light)), bright=sim["bright"],
                      mv=mv, ma=ma, mw=int(mv * ma / 1000))
        state.add(now, sample)
        log_sample(log, now, sample)
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


def style_axis(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=MUTED)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(color=GRID, alpha=0.8)


def build_figure(state):
    plt.style.use("dark_background")
    fig, (ax_info, ax, ax_p) = plt.subplots(3, 1, figsize=(9.5, 8.5),
                                            gridspec_kw={"height_ratios": [1.2, 3, 1.8]})
    fig.patch.set_facecolor(BG)
    fig.canvas.manager.set_window_title("Luxlamppu - dashboard")
    fig.subplots_adjust(left=0.09, right=0.91, top=0.97, bottom=0.15, hspace=0.35)

    ax_info.axis("off")
    art = {}
    art["seen"] = ax_info.text(0.0, 0.85, "", fontsize=14, fontweight="bold", color=TEXT,
                               transform=ax_info.transAxes)
    art["light"] = ax_info.text(0.0, 0.52, "", fontsize=12, color=TEXT, transform=ax_info.transAxes)
    art["power_text"] = ax_info.text(0.0, 0.22, "", fontsize=12, color=TEXT, transform=ax_info.transAxes)
    art["status"] = ax_info.text(0.0, -0.05, "", fontsize=9, color=MUTED, transform=ax_info.transAxes)

    # Valoanturi (vasen akseli) ja LED-kirkkaus (oikea akseli)
    style_axis(ax)
    (art["line"],) = ax.plot([], [], color=LIGHT_COLOR, linewidth=1.8, label="valoanturi")
    ax.set_xlim(-HISTORY_S, 0)
    ax.set_ylim(0, 300)
    ax.set_ylabel("valoanturin arvo (ADC)", color=MUTED)
    ax_b = ax.twinx()
    ax_b.set_ylim(0, 105)
    ax_b.set_ylabel("LED-kirkkaus (%)", color=MUTED)
    ax_b.tick_params(colors=MUTED)
    (art["bright_line"],) = ax_b.plot([], [], color=BRIGHT_COLOR, linewidth=1.6,
                                      drawstyle="steps-post", label="LED-kirkkaus")
    ax.legend(handles=[art["line"], art["bright_line"],
                       Patch(color=MOTION_COLOR, alpha=0.3, label="liike havaittu")],
              loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    # INA219: teho
    style_axis(ax_p)
    (art["power_line"],) = ax_p.plot([], [], color=POWER_COLOR, linewidth=1.6)
    ax_p.set_xlim(-HISTORY_S, 0)
    ax_p.set_ylim(0, 1)
    ax_p.set_ylabel("nauhan teho (W)", color=MUTED)
    ax_p.set_xlabel("aika (s, 0 = nyt)", color=MUTED)

    # Kirkkauden liukusäädin ja sammutuspainike
    ax_slider = fig.add_axes([0.2, 0.055, 0.5, 0.035], facecolor=PANEL)
    art["slider"] = Slider(ax_slider, "LED-kirkkaus %", 0, 100, valinit=state.desired, valstep=1,
                           color=BRIGHT_COLOR, track_color=GRID)
    art["slider"].label.set_color(TEXT)
    art["slider"].valtext.set_color(TEXT)
    ax_button = fig.add_axes([0.77, 0.045, 0.14, 0.055])
    art["button"] = Button(ax_button, "Sammuta", color=PANEL, hovercolor=GRID)
    art["button"].label.set_color(TEXT)
    fig.text(0.2, 0.012, "Liukusäädin asettaa nauhan kirkkauden. Violetti käyrä on Arduinon "
             "ilmoittama todellinen kirkkaus.", fontsize=8.5, color=MUTED)

    # Virheilmoitus näkyy kaavioiden päällä vain, kun jokin on vialla
    art["error"] = fig.text(0.5, 0.5, "", ha="center", va="center", fontsize=13, fontweight="bold",
                            color="white", zorder=10, linespacing=1.6,
                            bbox=dict(boxstyle="round,pad=0.9", facecolor=ERROR_BG,
                                      edgecolor=ERROR_EDGE, linewidth=2))
    art["error"].set_visible(False)
    return fig, ax, ax_b, ax_p, art


def connect_controls(state, art):
    art["slider"].on_changed(state.set_desired)
    art["button"].on_clicked(lambda _event: art["slider"].set_val(0))


def sync_brightness(state, snap, now):
    """Lähettää liukusäätimen arvon Arduinolle, kunnes Arduino ilmoittaa käyttävänsä sitä."""
    latest = snap["latest"]
    if state.write is None or latest is None or latest["bright"] is None:
        return
    want = snap["desired"]
    if latest["bright"] == want:
        return
    changed = state.last_sent != want
    wait = SEND_MIN_INTERVAL_S if changed else RESEND_S
    if now - state.last_send_t >= wait:
        try:
            state.write(f"B,{want}")
            state.last_sent = want
            state.last_send_t = now
        except Exception:     # yhteys katkesi juuri; lukijasäie ilmoittaa siitä
            pass


def make_updater(state, ax, ax_b, ax_p, art):
    motion_fill = [None]

    def update(_frame):
        s = state.snapshot()
        now = time.time()
        rel = [t - now for t in s["times"]]
        sync_brightness(state, s, now)

        art["line"].set_data(rel, s["light"])
        if s["light"]:
            ax.set_ylim(0, min(ADC_MAX, max(300, max(s["light"]) * 1.2)))
        bright_pts = [(r, b) for r, b in zip(rel, s["bright"]) if b is not None]
        art["bright_line"].set_data([p[0] for p in bright_pts], [p[1] for p in bright_pts])
        power_pts = [(r, p / 1000) for r, p in zip(rel, s["power"]) if p is not None]
        art["power_line"].set_data([p[0] for p in power_pts], [p[1] for p in power_pts])
        if power_pts:
            ax_p.set_ylim(0, max(1.0, max(p[1] for p in power_pts) * 1.2))

        if motion_fill[0] is not None:
            motion_fill[0].remove()
        top = ax.get_ylim()[1]
        motion_fill[0] = ax.fill_between(rel, 0, top, where=[p == 1 for p in s["pir"]],
                                         step="post", color=MOTION_COLOR, alpha=0.3, linewidth=0)

        art["seen"].set_text(format_last_seen(s["last_seen"], now))
        latest = s["latest"]
        if latest:
            motion = "kyllä" if latest["pir"] else "ei"
            bright = f"{latest['bright']} %" if latest["bright"] is not None else "–"
            art["light"].set_text(f"Valoisuus: {latest['light']}    LED-kirkkaus: {bright}    "
                                  f"Liikettä: {motion}")
            if latest["mw"] is None:
                art["power_text"].set_text("INA219: ei tietoa (firmware lähettää vain anturit)")
            elif latest["mw"] < 0:
                art["power_text"].set_text("INA219 ei vastaa: tarkista VCC, GND, SDA (A4) ja SCL (A5)")
            else:
                text = (f"Jännite: {latest['mv'] / 1000:.2f} V    Virta: {latest['ma']} mA    "
                        f"Teho: {latest['mw'] / 1000:.2f} W    Energia: {s['energy_mwh']:.1f} mWh")
                art["power_text"].set_text(text.replace(".", ","))
        else:
            art["light"].set_text("Valoisuus: –")
            art["power_text"].set_text("")
        art["status"].set_text(s["status"])

        # Portti on auki, mutta dataa ei tule: väärä ohjelma, väärä nopeus tai kortti jumissa
        error = s["error"]
        if error is None and s["connected_at"] is not None:
            last = s["last_data"]
            since = now - (last if last and last > s["connected_at"] else s["connected_at"])
            if since > NO_DATA_S:
                error = (f"Ei dataa Arduinolta {int(since)} sekuntiin.\n"
                         "Onko kortille ladattu oikea ohjelma ja nopeus 9600 baudia?")

        art["error"].set_visible(error is not None)
        if error is not None:
            art["error"].set_text(error)
        return (art["line"], art["bright_line"], art["power_line"], art["seen"], art["light"],
                art["power_text"], art["status"], art["error"])

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
    log.writerow(["aika", "pir", "valo", "kirkkaus_pct", "jannite_mV", "virta_mA", "teho_mW"])

    if args.demo:
        target, targs = demo_reader, (state, log)
    else:
        port = args.port or find_port()
        target, targs = serial_reader, (port, args.baud, state, log)
    threading.Thread(target=target, args=targs, daemon=True).start()

    fig, ax, ax_b, ax_p, art = build_figure(state)
    connect_controls(state, art)
    update = make_updater(state, ax, ax_b, ax_p, art)
    _anim = FuncAnimation(fig, update, interval=200, cache_frame_data=False)
    plt.show()
    logfile.close()


if __name__ == "__main__":
    main()
