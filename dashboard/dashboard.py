#!/usr/bin/env python3
"""Luxlampun PC-dashboard.

Lukee Arduinolta USB-sarjaportin kautta rivejä muodossa
    pir,valo,kirkkaus,tavoite,tila,jannite_mV,virta_mA,teho_mW   (firmware/luxlamppu)
tai vanhemmissa muodoissa
    pir,valo,kirkkaus,jannite_mV,virta_mA,teho_mW
    pir,valo                                                      (firmware/valo_liike_usb)
ja näyttää:
  - milloin ihminen on viimeksi havaittu ja lampun tilan
  - valoanturin arvon, tavoitteen ja LED-nauhan kirkkauden kaaviona
  - INA219:n mittaaman jännitteen, virran ja tehon sekä kertyneen energian

Kaavioiden aikavälin voi valita painikkeilla (2 min – 12 h). Pitkillä aikaväleillä
näytetään 10 sekunnin jaksojen keskiarvot, ja tehokaaviossa vaalea alue näyttää
jakson pienimmän ja suurimman tehon. Dashboard lataa käynnistyessään edellisten
12 tunnin mittaukset kansion dashboard/mittaukset mittaus_*.csv-tiedostoista, joten
historia säilyy, vaikka dashboard käynnistetään uudelleen.

Liukusäädin asettaa tavoitteen valoanturin yksiköissä: Arduino säätää LED-nauhan
kirkkautta niin, että anturin lukema pysyy tavoitteessa. Dashboard lähettää
tavoitteen (T), pitoajan (H) ja tarvittaessa säätimen kertoimen (K) aina, kun
yhteys avataan, ja uudelleen, jos Arduinon ilmoittama tavoite ei vastaa liukusäädintä.

Ikkunassa on kaksi välilehteä. "Lamppu" näyttää yllä kuvatut kaaviot. "Järjestelmä"
näyttää firmwaren omat ajoitusmittarit: dashboard kytkee ne päälle komennolla D,1, ja
Arduino lähettää kerran sekunnissa rivin
    #diag,tikit,ohitetut,suurin_viive_us,kuorma_promillea[,pir_liikkeet,pir_viive_max_us]
Välilehdellä näkyvät suoritinkuorma ja suurin viive tikistä työhön ajan funktiona,
ohitetut tikit, PIR:n viive (jos firmware mittaa sen), telemetrian tahti ja firmwaren versio.

Jos sarjaporttia ei saada auki tai data lakkaa tulemasta, dashboard näyttää siitä
punaisen ilmoituksen ja yrittää yhdistää uudelleen automaattisesti.

Kaikki vastaanotettu data tallentuu myös CSV-tiedostoon.

Energy accounting on the MCU: the dashboard sends the time of day (C,<seconds since
midnight>) when the connection opens and every 10 minutes, because the Uno has no
real-time clock. The firmware integrates the strip power per hour of the day and sends
    E,<clock_s>,<synced>,<day_uWh>,<hour_uWh>   every 10 s
    EH,<hour>,<uWh>,<gap_s>                     when an hour ends
    ED,<uWh>                                    when the day ends
    #sync,<clock_s before | -1>,<new clock_s>   after each C command
These lines are saved with PC timestamps to mittaukset/energia_<time>.csv
(validointi/validoi_energia.py compares them with the PC's own integral).

Käyttö:
    pip install pyserial matplotlib
    python dashboard.py                 # etsii Arduinon sarjaportin automaattisesti
    python dashboard.py --port COM3     # tai portti käsin (Linux: /dev/ttyACM0)
    python dashboard.py --demo          # testidata ilman Arduinoa (3 h valmiina historiana)
    python dashboard.py --ei-historiaa  # älä lataa aiempia mittauksia
    python dashboard.py --pitoaika 20   # lyhyt pitoaika testiin (oletus 180 s)
    python dashboard.py stop            # sulje käynnissä oleva dashboard (vapauttaa sarjaportin)

Käynnissä oleva dashboard kuuntelee pysäytyskäskyä paikallisessa TCP-portissa
CONTROL_PORT (vain 127.0.0.1). "stop" odottaa, kunnes dashboard on sulkeutunut,
joten sen jälkeen sarjaportti on vapaa esimerkiksi firmwaren lataamiseen.
"""
import argparse
import bisect
import csv
import glob
import os
import math
import random
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.widgets import Button, Slider

RAW_S = 900              # raakadata (5 näytettä/s) säilytetään näin pitkältä ajalta
RAW_GAP_S = 2.0          # tätä pidempi tauko raakadatassa katkaisee käyrän
LONG_S = 12 * 3600       # pitkä historia 10 s:n jaksoina
BUCKET_S = 10            # pitkän historian jakson pituus
RANGES = [("2 min", 120), ("15 min", 900), ("1 h", 3600), ("3 h", 3 * 3600), ("12 h", 12 * 3600)]
TICK_STEP = {120: 20, 900: 120, 3600: 600, 3 * 3600: 1800, 12 * 3600: 7200}   # aika-akselin jako (s)
LONG_KEYS = ("t", "light", "bright", "pir", "pmean", "pmin", "pmax", "energy", "dur")
ADC_MAX = 1023
UPDATE_S = 2.0           # näkymän päivitysväli (--paivitys): koko kuvan piirto vie ~200 ms, joten
                         # tiheä päivitys varaa käyttöliittymän (data tallentuu silti 5 kertaa sekunnissa)
NO_DATA_S = 3           # näin pitkä tauko datassa näytetään virheenä
RETRY_S = 2              # kuinka usein porttia yritetään avata uudelleen
SEND_MIN_INTERVAL_S = 0.15   # liukusäätimen komentoja korkeintaan tällä välillä
RESEND_S = 1.0           # komento lähetetään uudelleen, jos Arduino ei ole ottanut sitä käyttöön
DEFAULT_TARGET = 150     # tavoite valoanturin yksiköissä
TARGET_MAX = 600         # liukusäätimen yläraja
ANIM_S = 30.0            # animaation kesto (sama kuin firmwaren LAMP_ANIM_MS)
CONTROL_PORT = 47219     # paikallinen portti, josta dashboard ottaa vastaan "stop"-käskyn
STOP_WAIT_S = 10         # kuinka kauan "stop" odottaa dashboardin sulkeutumista
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mittaukset")
                         # mittauslokit (mittaus_*.csv, demo_*.csv) dashboardin alakansiossa,
                         # riippumatta siitä, mistä kansiosta dashboard käynnistetään
DIAG_WINDOW_S = 600      # Järjestelmä-välilehden kaavioiden aikaväli (10 min)
DIAG_STALE_S = 5         # näin kauan ilman #diag-riviä: firmware ei lähetä niitä
TICK_MS = 10             # firmwaren Timer1-tikki
CLOCK_SYNC_S = 600       # how often the time of day is sent to the MCU (C command)
ENERGY_PREFIXES = ("E,", "EH,", "ED,", "#sync,")   # energy accounting lines from the MCU
LOAD_COLOR = "#38bdf8"
LATENCY_COLOR = "#fbbf24"
STATE_NAMES = {0: "pois (ei liikettä)", 1: "säätö", 2: "sammumisanimaatio", 3: "heräämisanimaatio"}

# Tumma teema
BG = "#111418"
PANEL = "#181c22"
TEXT = "#e6e9ef"
MUTED = "#9aa4b2"
GRID = "#2c333d"
LIGHT_COLOR = "#4ea1ff"
BRIGHT_COLOR = "#c084fc"
POWER_COLOR = "#f472b6"
TARGET_COLOR = "#34d399"
MOTION_COLOR = "#f59e0b"
ERROR_BG = "#5c1a1a"
ERROR_EDGE = "#ef4444"
ACTIVE = "#24508a"       # valitun aikavälin painike


class State:
    """Viimeisimmät mittaukset ja ohjaus, jaettu lukijasäikeen ja käyttöliittymän kesken."""

    def __init__(self):
        self.lock = threading.Lock()
        self.times = deque()
        self.light = deque()
        self.pir = deque()
        self.bright = deque()        # Arduinon ilmoittama kirkkaus (%) tai None
        self.power = deque()         # teho (mW) tai None
        self.energy_s = deque()      # näytteen energia (mWh)
        self.dur_s = deque()         # aika, jolta näytteen teho integroitiin (s)
        self.range_s = RANGES[0][1]  # kaavioiden aikaväli
        self.long = {k: deque() for k in LONG_KEYS}   # 10 s:n jaksojen keskiarvot
        self._bucket = None          # keskeneräinen jakso
        self.latest = None           # viimeisin näyte (dict)
        self.last_seen = None        # time.time() viimeisestä liikkeestä
        self.last_data = None        # time.time() viimeisestä kelvollisesta rivistä
        self.connected_at = None
        self.status = "Odotetaan dataa..."
        self.firmware = None         # firmwaren käännösaika (V-rivi) tai None
        self.error = None            # näkyvä virheilmoitus tai None
        self.energy_mwh = 0.0
        self._last_power_t = None
        self.mcu_energy = None       # latest E line: dict(clock_s, synced, day_uwh, hour_uwh)
        self.energy_log = None       # csv.writer for the energy lines, or None
        self.last_clock_sync = 0.0   # time.time() of the last C command

        self.desired = DEFAULT_TARGET  # liukusäätimen tavoite (valoanturin yksiköissä)
        self.hold_s = 180            # pitoaika sekunteina
        self.ki = None               # säätimen kerroin; None = Arduinon oletus
        self.last_sent = None        # viimeksi lähetetty tavoite
        self.last_send_t = 0.0
        self.need_config = False     # uusi yhteys: lähetä kaikki asetukset
        self.write = None            # funktio, joka lähettää rivin Arduinolle
        self.stop_requested = threading.Event()   # "dashboard.py stop" pyysi sulkemaan
        self.diag = deque(maxlen=3600)  # (aika, tikit, ohitetut, viive_us, kuorma_pm, pir_n, pir_viive_us)

    def add(self, t, sample, history=False):
        """Lisää näytteen. history=True: näyte on ladattu aiemmasta mittauksesta, joten se
        ei ole viimeisin lukema eikä kasvata tämän käynnistyksen energiaa."""
        with self.lock:
            power = sample["mw"] if sample["mw"] is not None and sample["mw"] >= 0 else None
            dt = energy = 0.0
            if power is not None:
                if self._last_power_t is not None:
                    dt = max(0.0, min(t - self._last_power_t, 2.0))   # katkon yli ei integroida
                    energy = power * dt / 3600.0
                self._last_power_t = t
            self.times.append(t)
            self.light.append(sample["light"])
            self.pir.append(sample["pir"])
            self.bright.append(sample["bright"])
            self.power.append(power)
            self.energy_s.append(energy)
            self.dur_s.append(dt)
            self._add_to_bucket(t, sample, power, dt, energy)
            if not history:
                self.energy_mwh += energy
                self.latest = sample
                self.last_data = t
            if sample["pir"]:
                self.last_seen = t
            while self.times and self.times[0] < t - RAW_S:
                for d in (self.times, self.light, self.pir, self.bright, self.power,
                          self.energy_s, self.dur_s):
                    d.popleft()

    def _add_to_bucket(self, t, sample, power, dt, energy):
        key = int(t // BUCKET_S)
        b = self._bucket
        if b is not None and b["key"] != key:
            values = self._bucket_values(b)
            for k in LONG_KEYS:
                self.long[k].append(values[k])
            while self.long["t"][0] < values["t"] - LONG_S:
                for d in self.long.values():
                    d.popleft()
            b = None
        if b is None:
            b = self._bucket = dict(key=key, n=0, light=0, nb=0, bright=0, np=0, psum=0.0,
                                    pmin=None, pmax=None, pir=0, energy=0.0, dur=0.0)
        b["n"] += 1
        b["light"] += sample["light"]
        if sample["bright"] is not None:
            b["nb"] += 1
            b["bright"] += sample["bright"]
        if power is not None:
            b["np"] += 1
            b["psum"] += power
            b["pmin"] = power if b["pmin"] is None else min(b["pmin"], power)
            b["pmax"] = power if b["pmax"] is None else max(b["pmax"], power)
            b["energy"] += energy
            b["dur"] += dt
        if sample["pir"]:
            b["pir"] = 1

    @staticmethod
    def _bucket_values(b):
        return dict(t=(b["key"] + 0.5) * BUCKET_S, light=b["light"] / b["n"],
                    bright=b["bright"] / b["nb"] if b["nb"] else None, pir=b["pir"],
                    pmean=b["psum"] / b["np"] if b["np"] else None, pmin=b["pmin"], pmax=b["pmax"],
                    energy=b["energy"], dur=b["dur"])

    def set_range(self, span):
        with self.lock:
            self.range_s = span

    def _view(self, now):
        """Valitun aikavälin data: raakadata lyhyille, 10 s:n jaksot pitkille aikaväleille."""
        span = self.range_s
        if span <= RAW_S:
            times = list(self.times)
            i = bisect.bisect_left(times, now - span)
            return dict(t=times[i:], light=list(self.light)[i:], bright=list(self.bright)[i:],
                        pir=list(self.pir)[i:], pmean=list(self.power)[i:], pmin=None, pmax=None,
                        energy=list(self.energy_s)[i:], dur=list(self.dur_s)[i:],
                        gap=RAW_GAP_S, buckets=False)
        times = list(self.long["t"])
        i = bisect.bisect_left(times, now - span)
        view = {k: list(self.long[k])[i:] for k in LONG_KEYS}
        if self._bucket is not None:
            values = self._bucket_values(self._bucket)
            for k in LONG_KEYS:
                view[k].append(values[k])
        view.update(gap=1.5 * BUCKET_S, buckets=True)
        return view

    def set_connection(self, status, error=None, connected=False, write=None):
        with self.lock:
            self.status = status
            self.error = error
            self.firmware = None     # Arduino käynnistyy uudelleen ja lähettää V-rivin
            self.connected_at = time.time() if connected else None
            self.write = write
            self.last_sent = None             # uusi yhteys: asetukset lähetetään uudelleen
            self.need_config = connected

    def set_desired(self, value):
        with self.lock:
            self.desired = int(value)

    def add_diag(self, t, diag):
        with self.lock:
            self.diag.append((t,) + diag)

    def add_energy_line(self, t, line):
        """Stores an energy accounting line from the MCU (E, EH, ED, #sync)."""
        with self.lock:
            if self.energy_log is not None:
                self.energy_log.writerow([datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                                          line])
            e = parse_energy_status(line)
            if e is not None:
                self.mcu_energy = e

    def system_snapshot(self, now):
        """Järjestelmä-välilehden tiedot: #diag-rivit ja telemetrian tahti."""
        with self.lock:
            diag = [d for d in self.diag if d[0] >= now - DIAG_WINDOW_S]
            recent = sum(1 for t in reversed(self.times) if t >= now - 10)
            return dict(diag=diag, firmware=self.firmware, latest=self.latest,
                        connected_at=self.connected_at, rate=recent / 10.0)

    def snapshot(self, now):
        with self.lock:
            return dict(view=self._view(now), range_s=self.range_s, latest=self.latest,
                        last_seen=self.last_seen, last_data=self.last_data,
                        connected_at=self.connected_at, status=self.status, error=self.error,
                        energy_mwh=self.energy_mwh, desired=self.desired)


def parse_line(line):
    """Palauttaa näytteen (dict) tai None, jos rivi on vajaa tai rikki."""
    parts = line.strip().split(",")
    if len(parts) not in (2, 6, 8):
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    pir, light = values[0], values[1]
    if pir not in (0, 1) or not 0 <= light <= ADC_MAX:
        return None
    sample = dict(pir=pir, light=light, bright=None, target=None, state=None,
                  mv=None, ma=None, mw=None)
    if len(values) == 6:
        bright, mv, ma, mw = values[2:]
        target = state = None
    elif len(values) == 8:
        bright, target, state, mv, ma, mw = values[2:]
        if not 0 <= target <= ADC_MAX or state not in STATE_NAMES:
            return None
    else:
        return sample
    if not 0 <= bright <= 100:
        return None
    sample.update(bright=bright, target=target, state=state, mv=mv, ma=ma, mw=mw)
    return sample


def parse_energy_status(line):
    """'E,36000,1,152000,4000' -> dict(clock_s, synced, day_uwh, hour_uwh), else None."""
    parts = line.strip().split(",")
    if parts[0] != "E" or len(parts) != 5:
        return None
    try:
        clock_s, synced, day_uwh, hour_uwh = (int(p) for p in parts[1:])
    except ValueError:
        return None
    if not 0 <= clock_s < 86400 or synced not in (0, 1):
        return None
    return dict(clock_s=clock_s, synced=synced, day_uwh=day_uwh, hour_uwh=hour_uwh)


def seconds_since_midnight(now=None):
    """Local time of day in whole seconds, for the C command."""
    d = datetime.fromtimestamp(time.time() if now is None else now)
    return d.hour * 3600 + d.minute * 60 + d.second


def parse_diag(line):
    """'#diag,1200,0,12,41[,3,152]' -> (tikit, ohitetut, viive_us, kuorma_pm, pir_n, pir_viive_us).
    PIR-kentät ovat None, jos firmware ei lähetä niitä. Rikkinäinen rivi -> None."""
    parts = line.strip().split(",")
    if parts[0] != "#diag" or len(parts) not in (5, 7):
        return None
    try:
        values = [int(p) for p in parts[1:]]
    except ValueError:
        return None
    if len(values) == 4:
        values += [None, None]
    return tuple(values)


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
                  s["pir"], s["light"], s["bright"], s["target"], s["state"],
                  s["mv"], s["ma"], s["mw"]])


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
                    v = raw.find("V,")        # alussa voi olla käynnistyksen roskatavuja
                    if v >= 0:
                        with state.lock:
                            state.firmware = raw[v + 2:].strip()   # firmwaren käännösaika
                        continue
                    if raw.startswith("#diag"):
                        diag = parse_diag(raw)
                        if diag:
                            state.add_diag(time.time(), diag)
                        continue
                    if raw.startswith(ENERGY_PREFIXES):
                        state.add_energy_line(time.time(), raw.strip())
                        continue
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


def start_control_server(state):
    """Kuuntelee pysäytyskäskyä. Palauttaa False, jos portti on jo toisen dashboardin käytössä."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", CONTROL_PORT))
    except OSError:
        srv.close()
        return False
    srv.listen(1)

    def serve():
        while True:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(2)
                try:
                    command = conn.recv(64).decode("ascii", errors="ignore").strip()
                    if command == "stop":
                        conn.sendall(b"ok\n")
                        state.stop_requested.set()
                    elif command:
                        conn.sendall(b"tuntematon komento\n")
                except OSError:
                    pass      # esim. "stop"-komennon tarkistusyhteys, joka sulkeutuu heti

    threading.Thread(target=serve, daemon=True).start()
    return True


def send_stop():
    """Pyytää käynnissä olevaa dashboardia sulkeutumaan ja odottaa, että se on sulkeutunut."""
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=2) as conn:
            conn.sendall(b"stop\n")
            reply = conn.recv(64).decode("ascii", errors="ignore").strip()
    except OSError:
        print("Dashboard ei ole käynnissä.")
        return 1
    if reply != "ok":
        print(f"Odottamaton vastaus: {reply!r}")
        return 1
    # Kun prosessi on sulkeutunut, portti ei enää ota yhteyksiä vastaan ja sarjaportti on vapaa
    deadline = time.time() + STOP_WAIT_S
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=0.5).close()
        except OSError:
            print("Dashboard suljettu.")
            return 0
        time.sleep(0.2)
    print(f"Dashboard otti käskyn vastaan, mutta ei sulkeutunut {STOP_WAIT_S} sekunnissa.")
    return 1


class DemoSim:
    """Simuloitu lamppu: taustavalo vaihtelee (hidas vaihtelu ja nopea aaltoilu), ihminen
    on välillä paikalla ja välillä poissa, ja lamppu säätää kirkkautta tavoitteeseen samalla
    tavalla kuin Arduino."""

    def __init__(self, start):
        self.target, self.hold = DEFAULT_TARGET, 180.0
        self.level, self.state, self.light = 0.0, 1, 60.0
        self.pir_until, self.last_motion, self.anim_start, self.anim_level = 0.0, start, 0.0, 0.0
        self.present, self.switch_at = True, start + random.uniform(600, 2400)

    def command(self, line):
        cmd, _, value = line.partition(",")
        if cmd == "T":
            self.target = int(value)
        elif cmd == "H":
            self.hold = float(value)

    def step(self, now):
        if now >= self.switch_at:              # ihminen lähtee tai palaa
            self.present = not self.present
            self.switch_at = now + (random.uniform(600, 2400) if self.present
                                    else random.uniform(300, 1500))
        if self.present and random.random() < 0.004:
            self.pir_until = now + random.uniform(3, 10)
        pir = 1 if now < self.pir_until else 0
        if pir:
            self.last_motion = now
        if self.state == 1 and now - self.last_motion > self.hold:
            self.state, self.anim_start, self.anim_level = 2, now, self.level
        elif self.state == 2 and pir:          # herääminen jatkuu samasta kohdasta
            elapsed = min(now - self.anim_start, ANIM_S)
            self.state, self.anim_start = 3, now - (ANIM_S - elapsed)
        elif self.state == 0 and pir:
            self.state, self.anim_start, self.anim_level = 3, now, max(self.level, 30.0)
        elif self.state in (2, 3) and now - self.anim_start >= ANIM_S:
            if self.state == 3:
                self.level = self.anim_level
            self.state = 0 if self.state == 2 else 1
        if self.state == 1:
            shown = self.level
        elif self.state == 0:
            shown = 0.0
        else:
            phase = min(now - self.anim_start, ANIM_S) / ANIM_S
            shown = self.anim_level * anim_fraction(phase if self.state == 3 else 1 - phase)
        ambient = max(0.0, 80 + 45 * math.sin(now / 2400) + 8 * math.sin(now / 60))
        self.light += (ambient + 2.0 * shown + random.gauss(0, 2) - self.light) / 4
        if self.state == 1:
            err = self.target - self.light
            if abs(err) > 3:
                self.level = max(0.0, min(100.0, self.level + 0.3 * err))
        ma = int(39 + 8.2 * shown)
        mv = int(5000 - 0.1 * ma)
        return dict(pir=pir, light=int(max(0, min(ADC_MAX, self.light))), bright=int(round(shown)),
                    target=self.target, state=self.state, mv=mv, ma=ma, mw=int(mv * ma / 1000))


def anim_fraction(phase, n=39, edge=0.4):
    """Nauhan keskikirkkaus animaation vaiheessa 0–1 suhteessa animaation kirkkauteen
    (sama muoto kuin firmwaren lamp_led_pm)."""
    reach = phase * (1 + edge)
    total = 0.0
    for i in range(n):
        d = abs(2 * i - (n - 1)) / (n - 1)
        f = min(1.0, max(0.0, (reach - d) / edge))
        total += f * f
    return total / n


def demo_reader(state, log, sim):
    state.set_connection("Demotila: testidata", connected=True, write=sim.command)
    with state.lock:
        state.firmware = "demo"
    start, next_diag, pir_n, pir_max, last_pir = time.time(), time.time() + 1, 0, 0, 0
    day_uwh, hour_uwh, next_energy = 0.0, 0.0, time.time() + 10
    while True:
        now = time.time()
        sample = sim.step(now)
        state.add(now, sample)
        log_sample(log, now, sample)
        day_uwh += sample["mw"] * 200 / 3600       # simulated MCU energy accounting
        hour_uwh += sample["mw"] * 200 / 3600
        if now >= next_energy:
            state.add_energy_line(now, f"E,{seconds_since_midnight(now)},1,{int(day_uwh)},"
                                       f"{int(hour_uwh)}")
            next_energy += 10
        if sample["pir"] and not last_pir:          # simuloitu PIR-viive liikkeen alussa
            pir_n += 1
            pir_max = max(pir_max, random.randint(50, 9500))
        last_pir = sample["pir"]
        if now >= next_diag:                        # simuloitu #diag-rivi kerran sekunnissa
            anim = sample["state"] in (2, 3)
            load = random.randint(250, 360) if anim else random.randint(35, 50)
            lat = random.randint(800, 2500) if anim else random.randint(12, 40)
            state.add_diag(now, (int((now - start) * 100), 0, lat, load, pir_n, pir_max))
            next_diag += 1
        time.sleep(0.2)


def load_history(state, paths, now):
    """Lataa aiempien mittausten viimeiset 12 tuntia CSV-tiedostoista. Palauttaa näytteiden määrän."""
    def num(row, key):
        try:
            return int(row[key])
        except (KeyError, TypeError, ValueError):
            return None

    rows = []
    for path in paths:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        t = datetime.fromisoformat(row["aika"]).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not now - LONG_S <= t <= now:
                        continue
                    sample = dict(pir=num(row, "pir"), light=num(row, "valo"),
                                  bright=num(row, "kirkkaus_pct"), target=num(row, "tavoite"),
                                  state=num(row, "tila"), mv=num(row, "jannite_mV"),
                                  ma=num(row, "virta_mA"), mw=num(row, "teho_mW"))
                    if sample["pir"] is not None and sample["light"] is not None:
                        rows.append((t, sample))
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            print(f"Historiaa ei voitu lukea tiedostosta {path}: {e}")
    rows.sort(key=lambda r: r[0])
    for t, sample in rows:
        state.add(t, sample, history=True)
    return len(rows)


def with_gaps(xs, ys, max_gap):
    """Muuttaa None-arvot NaN:iksi ja lisää NaN-pisteen datan katkoihin, jotta käyrä katkeaa."""
    out_x, out_y = [], []
    prev = None
    for x, y in zip(xs, ys):
        if prev is not None and x - prev > max_gap:
            out_x.append((prev + x) / 2)
            out_y.append(math.nan)
        out_x.append(x)
        out_y.append(math.nan if y is None else y)
        prev = x
    return out_x, out_y


def fmt_num(value, decimals):
    return f"{value:.{decimals}f}".replace(".", ",")


def fmt_energy(mwh):
    return f"{fmt_num(mwh / 1000, 2)} Wh" if mwh >= 1000 else f"{fmt_num(mwh, 1)} mWh"


def set_time_axis(axes, span, now):
    """Lyhyellä aikavälillä sekunnit nykyhetkestä, pitkillä kellonajat tasaväleillä."""
    step = TICK_STEP[span]
    if span <= 120:
        ticks = list(range(-span, 1, step))
        labels = [str(t) for t in ticks]
        xlabel = "aika (s, 0 = nyt)"
    else:
        offset = datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds()
        first = math.ceil((now - span + offset) / step) * step - offset
        ticks, labels = [], []
        t = first
        while t <= now:
            ticks.append(t - now)
            labels.append(datetime.fromtimestamp(t).strftime("%H:%M"))
            t += step
        xlabel = "kellonaika"
    for a in axes:
        a.set_xlim(-span, 0)
        a.set_xticks(ticks)
        a.set_xticklabels(labels)
    axes[-1].set_xlabel(xlabel, color=MUTED)


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
    fig = Figure(figsize=(9.5, 9.2))
    ax_info, ax, ax_p = fig.subplots(3, 1, gridspec_kw={"height_ratios": [1.4, 3, 1.8]})
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(left=0.09, right=0.91, top=0.97, bottom=0.225, hspace=0.35)

    ax_info.axis("off")
    art = {}
    art["seen"] = ax_info.text(0.0, 0.88, "", fontsize=14, fontweight="bold", color=TEXT,
                               transform=ax_info.transAxes)
    art["mode"] = ax_info.text(0.0, 0.62, "", fontsize=12, color=TEXT, transform=ax_info.transAxes)
    art["light"] = ax_info.text(0.0, 0.38, "", fontsize=12, color=TEXT, transform=ax_info.transAxes)
    art["power_text"] = ax_info.text(0.0, 0.14, "", fontsize=12, color=TEXT, transform=ax_info.transAxes)
    art["status"] = ax_info.text(0.0, -0.08, "", fontsize=9, color=MUTED, transform=ax_info.transAxes)

    # Valoanturi ja tavoite (vasen akseli), LED-kirkkaus (oikea akseli)
    style_axis(ax)
    (art["line"],) = ax.plot([], [], color=LIGHT_COLOR, linewidth=1.8, label="valoanturi")
    art["target"] = ax.axhline(state.desired, color=TARGET_COLOR, linewidth=1.5, linestyle="--",
                               label="tavoite")
    ax.set_ylim(0, 300)
    ax.set_ylabel("valoanturin arvo (ADC)", color=MUTED)
    ax_b = ax.twinx()
    ax_b.set_ylim(0, 105)
    ax_b.set_ylabel("LED-kirkkaus (%)", color=MUTED)
    ax_b.tick_params(colors=MUTED)
    (art["bright_line"],) = ax_b.plot([], [], color=BRIGHT_COLOR, linewidth=1.4, alpha=0.9,
                                      drawstyle="steps-post", label="LED-kirkkaus")
    ax.legend(handles=[art["line"], art["target"], art["bright_line"],
                       Patch(color=MOTION_COLOR, alpha=0.3, label="liike havaittu")],
              loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    # INA219: teho
    style_axis(ax_p)
    (art["power_line"],) = ax_p.plot([], [], color=POWER_COLOR, linewidth=1.6,
                                     label="keskiteho 10 s:n jaksossa")
    ax_p.set_ylim(0, 1)
    ax_p.set_ylabel("nauhan teho (W)", color=MUTED)
    art["power_legend"] = ax_p.legend(
        handles=[art["power_line"], Patch(color=POWER_COLOR, alpha=0.25, label="pienin–suurin")],
        loc="upper right", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8.5)
    art["power_stats"] = ax_p.text(0.01, 0.95, "", transform=ax_p.transAxes, va="top",
                                   fontsize=9.5, color=TEXT,
                                   bbox=dict(boxstyle="round,pad=0.3", facecolor=PANEL,
                                             edgecolor=GRID, alpha=0.9))
    set_time_axis((ax, ax_p), state.range_s, time.time())

    # Aikavälin valinta
    fig.text(0.09, 0.124, "Aikaväli", fontsize=10, color=TEXT, va="center")
    art["range_buttons"] = []
    for i, (label, span) in enumerate(RANGES):
        b = Button(fig.add_axes([0.17 + i * 0.1, 0.104, 0.09, 0.04]), label,
                   color=PANEL, hovercolor=GRID)
        b.label.set_color(TEXT)
        b.label.set_fontsize(9)
        art["range_buttons"].append((b, span))

    # Tavoitteen liukusäädin ja "käytä nykyistä lukemaa" -painike
    ax_slider = fig.add_axes([0.17, 0.055, 0.5, 0.035], facecolor=PANEL)
    art["slider"] = Slider(ax_slider, "Tavoite", 0, TARGET_MAX, valinit=state.desired, valstep=1,
                           color=TARGET_COLOR, track_color=GRID, initcolor="none")
    art["slider"].label.set_color(TEXT)
    art["slider"].valtext.set_color(TEXT)
    ax_button = fig.add_axes([0.73, 0.045, 0.18, 0.055])
    art["button"] = Button(ax_button, "Käytä nykyistä lukemaa", color=PANEL, hovercolor=GRID)
    art["button"].label.set_color(TEXT)
    art["button"].label.set_fontsize(9)
    fig.text(0.17, 0.012, "Liukusäädin asettaa tavoitteen valoanturin yksiköissä. Arduino säätää "
             "LED-nauhan kirkkautta, kunnes anturin lukema on tavoitteessa.", fontsize=8.5, color=MUTED)

    # Virheilmoitus näkyy kaavioiden päällä vain, kun jokin on vialla
    art["error"] = fig.text(0.5, 0.5, "", ha="center", va="center", fontsize=13, fontweight="bold",
                            color="white", zorder=10, linespacing=1.6,
                            bbox=dict(boxstyle="round,pad=0.9", facecolor=ERROR_BG,
                                      edgecolor=ERROR_EDGE, linewidth=2))
    art["error"].set_visible(False)
    return fig, ax, ax_b, ax_p, art


def connect_controls(state, art):
    def set_target(value):
        state.set_desired(value)
        now = time.time()
        sync_settings(state, state.snapshot(now), now)   # heti, ei vasta seuraavalla päivityksellä

    art["slider"].on_changed(set_target)

    def use_current(_event):
        latest = state.snapshot(time.time())["latest"]
        if latest:
            art["slider"].set_val(min(TARGET_MAX, latest["light"]))   # kutsuu myös set_target

    art["button"].on_clicked(use_current)

    def select_range(span):
        state.set_range(span)
        for b, s in art["range_buttons"]:
            b.color = ACTIVE if s == span else PANEL
            b.ax.set_facecolor(b.color)

    for b, span in art["range_buttons"]:
        b.on_clicked(lambda _event, span=span: select_range(span))
    select_range(state.range_s)


def sync_settings(state, snap, now):
    """Lähettää tavoitteen Arduinolle, kunnes Arduino ilmoittaa käyttävänsä sitä.
    Uuden yhteyden alussa lähetetään myös pitoaika ja säätimen kerroin."""
    latest = snap["latest"]
    if state.write is None or latest is None or latest["target"] is None:
        return
    want = snap["desired"]
    try:
        if state.need_config:
            state.write("D,1")                # ajoitusmittarit Järjestelmä-välilehdelle
            state.write(f"H,{state.hold_s}")
            if state.ki is not None:
                state.write(f"K,{state.ki}")
            state.need_config = False
            state.last_clock_sync = 0.0       # new connection: send the clock now
        if now - state.last_clock_sync >= CLOCK_SYNC_S:
            state.write(f"C,{seconds_since_midnight(now)}")   # the Uno has no real-time clock
            state.last_clock_sync = now
        if latest["target"] == want:
            return
        changed = state.last_sent != want
        wait = SEND_MIN_INTERVAL_S if changed else RESEND_S
        if now - state.last_send_t >= wait:
            state.write(f"T,{want}")
            state.last_sent = want
            state.last_send_t = now
    except Exception:     # yhteys katkesi juuri; lukijasäie ilmoittaa siitä
        pass


def make_updater(state, ax, ax_b, ax_p, art):
    fills = {"motion": None, "band": None}

    def update():
        now = time.time()
        s = state.snapshot(now)
        v, span = s["view"], s["range_s"]
        latest = s["latest"]
        rel = [t - now for t in v["t"]]
        gap = v["gap"]

        # Valoanturi, tavoite ja LED-kirkkaus
        x, light = with_gaps(rel, v["light"], gap)
        art["line"].set_data(x, light)
        target = latest["target"] if latest and latest["target"] is not None else s["desired"]
        art["target"].set_ydata([target, target])
        top = min(ADC_MAX, max(300, max(v["light"], default=0) * 1.2, target * 1.2))
        ax.set_ylim(0, top)
        art["bright_line"].set_data(*with_gaps(rel, v["bright"], gap))
        art["bright_line"].set_drawstyle("default" if v["buckets"] else "steps-post")

        # Teho: pitkillä aikaväleillä keskiarvo ja vaihteluväli
        to_w = lambda values: [None if p is None else p / 1000 for p in values]
        x, pmean = with_gaps(rel, to_w(v["pmean"]), gap)
        art["power_line"].set_data(x, pmean)
        for key, fill in fills.items():
            if fill is not None:
                fill.remove()
                fills[key] = None
        if v["buckets"]:
            _, lo = with_gaps(rel, to_w(v["pmin"]), gap)
            _, hi = with_gaps(rel, to_w(v["pmax"]), gap)
            fills["band"] = ax_p.fill_between(x, lo, hi, color=POWER_COLOR, alpha=0.25, linewidth=0)
        art["power_legend"].set_visible(v["buckets"])
        peaks = [p for p in (v["pmax"] if v["buckets"] else v["pmean"]) if p is not None]
        ax_p.set_ylim(0, max(1.0, max(peaks, default=0) / 1000 * 1.5))   # tilaa selitteille
        energy, dur = sum(v["energy"]), sum(v["dur"])
        if dur > 0:
            art["power_stats"].set_text(
                f"Näkyvä jakso: keskiteho {fmt_num(energy * 3.6 / dur, 2)} W    "
                f"suurin {fmt_num(max(peaks) / 1000, 2)} W    energia {fmt_energy(energy)}")
        else:
            art["power_stats"].set_text("Näkyvällä jaksolla ei tehomittauksia")

        # Liike
        x, pir = with_gaps(rel, v["pir"], gap)
        fills["motion"] = ax.fill_between(x, 0, top, where=[p == 1 for p in pir],
                                          step="mid" if v["buckets"] else "post",
                                          color=MOTION_COLOR, alpha=0.3, linewidth=0)
        set_time_axis((ax, ax_p), span, now)

        art["seen"].set_text(format_last_seen(s["last_seen"], now))
        if latest:
            if latest["state"] is not None:
                art["mode"].set_text(f"Lampun tila: {STATE_NAMES[latest['state']]}    "
                                     f"Pitoaika: {state.hold_s} s")
            else:
                art["mode"].set_text("Lampun tila: ei tietoa (vanha firmware)")
            motion = "kyllä" if latest["pir"] else "ei"
            bright = f"{latest['bright']} %" if latest["bright"] is not None else "–"
            art["light"].set_text(f"Valoisuus: {latest['light']} / tavoite {target}    "
                                  f"LED-kirkkaus: {bright}    Liikettä: {motion}")
            if latest["mw"] is None:
                art["power_text"].set_text("INA219: ei tietoa (firmware lähettää vain anturit)")
            elif latest["mw"] < 0:
                art["power_text"].set_text("INA219 ei vastaa: tarkista VCC, GND, SDA (A4) ja SCL (A5)")
            else:
                art["power_text"].set_text(
                    f"Jännite: {fmt_num(latest['mv'] / 1000, 2)} V    Virta: {latest['ma']} mA    "
                    f"Teho: {fmt_num(latest['mw'] / 1000, 2)} W    "
                    f"Energia käynnistyksestä: {fmt_energy(s['energy_mwh'])}")
        else:
            art["mode"].set_text("")
            art["light"].set_text("Valoisuus: –")
            art["power_text"].set_text("")
        firmware = state.firmware
        art["status"].set_text(s["status"] + (f"    Firmware käännetty: {firmware}" if firmware else ""))

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

    return update


def format_mcu_energy(e):
    """Energy counted by the MCU itself (E line)."""
    if e is None:
        return "MCU energy: no E lines (older firmware)"
    clock = f"{e['clock_s'] // 3600:02d}:{e['clock_s'] // 60 % 60:02d}"
    return (f"MCU energy today: {fmt_energy(e['day_uwh'] / 1000)}, this hour "
            f"{fmt_energy(e['hour_uwh'] / 1000)} (MCU clock {clock}"
            f"{'' if e['synced'] else ', not synced'})")


def build_system_figure():
    """Järjestelmä-välilehti: firmwaren ajoitusmittarit (#diag-rivit)."""
    fig = Figure(figsize=(9.5, 9.2))
    ax_info, ax_load, ax_lat = fig.subplots(3, 1, gridspec_kw={"height_ratios": [1.6, 2.2, 2.2]})
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(left=0.09, right=0.91, top=0.97, bottom=0.07, hspace=0.4)
    ax_info.axis("off")
    art = {}
    art["title"] = ax_info.text(0.0, 0.95, "Firmwaren ajoitus ja kuorma", fontsize=14,
                                fontweight="bold", color=TEXT, transform=ax_info.transAxes)
    for i, key in enumerate(["line1", "line2", "line3", "line4", "line5"]):
        art[key] = ax_info.text(0.0, 0.72 - i * 0.19, "", fontsize=11.5, color=TEXT,
                                transform=ax_info.transAxes)
    art["note"] = ax_info.text(0.0, -0.22, "", fontsize=9, color=MUTED, transform=ax_info.transAxes)

    style_axis(ax_load)
    (art["load"],) = ax_load.plot([], [], color=LOAD_COLOR, linewidth=1.6)
    ax_load.set_xlim(-DIAG_WINDOW_S / 60, 0)
    ax_load.set_ylim(0, 100)
    ax_load.set_ylabel("suoritinkuorma (%)", color=MUTED)
    ax_load.set_title("Kuinka suuren osan ajasta pääsilmukka tekee työtä (loput on odotusta)",
                      fontsize=10, color=MUTED, loc="left")

    style_axis(ax_lat)
    (art["latency"],) = ax_lat.plot([], [], color=LATENCY_COLOR, linewidth=1.6,
                                    drawstyle="steps-post", label="suurin viive tikistä työhön")
    art["limit"] = ax_lat.axhline(TICK_MS, color=ERROR_EDGE, linewidth=1.2, linestyle="--",
                                  label=f"raja: yksi tikki ({TICK_MS} ms)")
    ax_lat.set_xlim(-DIAG_WINDOW_S / 60, 0)
    ax_lat.set_ylim(0, TICK_MS * 1.3)
    ax_lat.set_ylabel("viive (ms)", color=MUTED)
    ax_lat.set_xlabel("aika (min, 0 = nyt)", color=MUTED)
    ax_lat.legend(loc="upper left", facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=9)
    return fig, ax_load, ax_lat, art


def make_system_updater(state, ax_load, ax_lat, art):
    def update():
        now = time.time()
        s = state.system_snapshot(now)
        diag = s["diag"]
        rel = [(d[0] - now) / 60 for d in diag]
        art["load"].set_data(rel, [d[4] / 10 for d in diag])
        lat_ms = [d[3] / 1000 for d in diag]
        art["latency"].set_data(rel, lat_ms)
        ax_lat.set_ylim(0, max(TICK_MS * 1.3, max(lat_ms, default=0) * 1.2))

        art["line1"].set_text(f"Firmware käännetty: {s['firmware'] or '–'}    "
                              f"Telemetria: {fmt_num(s['rate'], 1)} riviä/s (tavoite 5)")
        fresh = diag and now - diag[-1][0] <= DIAG_STALE_S
        if not diag or not fresh:
            waited = s["connected_at"] is not None and now - s["connected_at"] > DIAG_STALE_S
            for key in ("line2", "line3", "line4"):
                art[key].set_text("")
            art["line2"].set_text("Ajoitusmittareita (#diag) ei tule." if waited else
                                  "Odotetaan ajoitusmittareita...")
            art["line3"].set_text("Firmware on vanhempi kuin Timer1-versio tai ei tue D-komentoa."
                                  if waited else "")
        else:
            last = diag[-1]
            missed = last[2] - diag[0][2]
            loads = [d[4] / 10 for d in diag]
            art["line2"].set_text(f"Timer1-tikki {TICK_MS} ms:    ohitetut tikit {missed} kpl, "
                                  f"suurin viive tikistä työhön {fmt_num(max(lat_ms), 2)} ms "
                                  f"(viimeiset {max(1, round((last[0] - diag[0][0]) / 60))} min)")
            art["line3"].set_text(f"Suoritinkuorma: nyt {fmt_num(loads[-1], 1)} %, keskiarvo "
                                  f"{fmt_num(sum(loads) / len(loads), 1)} %, suurin "
                                  f"{fmt_num(max(loads), 1)} %  ->  vapaata "
                                  f"{fmt_num(100 - sum(loads) / len(loads), 1)} %, "
                                  "jonka unitila voi korvata")
            if last[5] is None:
                art["line4"].set_text("PIR-viive: firmware ei vielä mittaa sitä")
            else:
                art["line4"].set_text(f"PIR-viive liikkeestä käsittelyyn: suurin "
                                      f"{fmt_num(last[6] / 1000, 2)} ms, liikkeitä {last[5]} "
                                      "(käynnistyksestä)")
        latest = s["latest"]
        if latest is None or latest["mw"] is None:
            ina = "ei tietoa"
        elif latest["mw"] < 0:
            ina = "ei vastaa"
        else:
            ina = "toimii"
        art["line5"].set_text(f"INA219: {ina}    {format_mcu_energy(state.mcu_energy)}")
        art["note"].set_text("Mittarit laskee firmware itse Timer1:n 4 µs:n tarkkuudella ja lähettää "
                             "kerran sekunnissa. Virallinen todiste: validointi/validoi_ajoitus.py.")

    return update


def main():
    parser = argparse.ArgumentParser(description="Luxlampun PC-dashboard")
    parser.add_argument("komento", nargs="?", choices=["stop"],
                        help="stop: sulje käynnissä oleva dashboard")
    parser.add_argument("--port", help="sarjaportti, esim. COM3 tai /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--demo", action="store_true", help="testidata ilman Arduinoa")
    parser.add_argument("--pitoaika", type=int, default=180,
                        help="sekunnit ilman liikettä ennen sammumista (1–3600, oletus 180)")
    parser.add_argument("--paivitys", type=float, default=UPDATE_S,
                        help=f"näkymän päivitysväli sekunteina (0,2–60, oletus {UPDATE_S:g})")
    parser.add_argument("--ki", type=int, help="säätimen kerroin 1–50 (oletus: Arduinon oma, 3)")
    parser.add_argument("--log", help="CSV-tiedosto, johon data tallennetaan "
                        "(oletus mittaukset/mittaus_<aika>.csv, demossa mittaukset/demo_<aika>.csv)")
    parser.add_argument("--ei-historiaa", action="store_true",
                        help="älä lataa aiempia mittauksia mittaukset/mittaus_*.csv-tiedostoista")
    args = parser.parse_args()
    if args.komento == "stop":
        sys.exit(send_stop())
    if args.log is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        name = f"{'demo' if args.demo else 'mittaus'}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        args.log = os.path.join(LOG_DIR, name)
    if not 1 <= args.pitoaika <= 3600:
        sys.exit("--pitoaika pitää olla 1–3600 sekuntia")
    if not 0.2 <= args.paivitys <= 60:
        sys.exit("--paivitys pitää olla 0,2–60 sekuntia")
    if args.ki is not None and not 1 <= args.ki <= 50:
        sys.exit("--ki pitää olla 1–50")

    state = State()
    if not start_control_server(state):
        print(f"Varoitus: portti {CONTROL_PORT} on varattu (toinen dashboard käynnissä?), "
              "joten tätä dashboardia ei voi sulkea komennolla 'dashboard.py stop'.")
    state.hold_s = args.pitoaika
    state.ki = args.ki
    now = time.time()
    sim = None
    if args.demo:
        # Demossa kolmen tunnin simuloitu historia, jotta pitkät aikavälit näkyvät heti
        sim = DemoSim(now - 3 * 3600)
        sim.hold = state.hold_s
        t = now - 3 * 3600
        while t < now:
            state.add(t, sim.step(t), history=True)
            t += 0.2
    elif not args.ei_historiaa:
        paths = [p for p in glob.glob(os.path.join(LOG_DIR, "mittaus_*.csv"))
                 if os.path.getmtime(p) >= now - LONG_S]
        if paths:
            count = load_history(state, paths, now)
            print(f"Ladattiin {count} aiempaa näytettä {len(paths)} tiedostosta (viimeiset 12 h).")
    logfile = open(args.log, "w", newline="", buffering=1)
    log = csv.writer(logfile)
    log.writerow(["aika", "pir", "valo", "kirkkaus_pct", "tavoite", "tila",
                  "jannite_mV", "virta_mA", "teho_mW"])
    log_dir = os.path.dirname(os.path.abspath(args.log))
    energy_name = os.path.basename(args.log).replace("mittaus_", "energia_").replace("demo_", "energia_demo_")
    if energy_name == os.path.basename(args.log):
        energy_name = "energia_" + energy_name
    energy_file = open(os.path.join(log_dir, energy_name), "w", newline="", buffering=1)
    state.energy_log = csv.writer(energy_file)
    state.energy_log.writerow(["time", "line"])

    if args.demo:
        target, targs = demo_reader, (state, log, sim)
    else:
        port = args.port or find_port()
        target, targs = serial_reader, (port, args.baud, state, log)
    threading.Thread(target=target, args=targs, daemon=True).start()

    run_window(state, args.paivitys)
    logfile.close()
    energy_file.close()


def run_window(state, interval_s):
    """Ikkuna, jossa on välilehdet Lamppu ja Järjestelmä. Vain näkyvä välilehti piirretään."""
    import tkinter as tk
    from tkinter import ttk
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    root = tk.Tk()
    root.title("Luxlamppu - dashboard")
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", GRID)], foreground=[("selected", TEXT)])
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    tabs = []
    fig, ax, ax_b, ax_p, art = build_figure(state)
    sys_fig, ax_load, ax_lat, sys_art = build_system_figure()
    for name, figure, updater in (
            ("Lamppu", fig, make_updater(state, ax, ax_b, ax_p, art)),
            ("Järjestelmä", sys_fig, make_system_updater(state, ax_load, ax_lat, sys_art))):
        frame = tk.Frame(notebook, bg=BG)
        notebook.add(frame, text=name)
        canvas = FigureCanvasTkAgg(figure, master=frame)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        tabs.append((canvas, updater))
    connect_controls(state, art)      # liukusäädin ja painike tarvitsevat kaavion piirtoalustan

    def tick():
        if state.stop_requested.is_set():
            root.destroy()            # mainloop palaa ja main() sulkee lokin
            return
        now = time.time()
        sync_settings(state, state.snapshot(now), now)   # myös silloin, kun Lamppu ei ole näkyvissä
        canvas, updater = tabs[notebook.index("current")]
        updater()
        canvas.draw_idle()
        root.after(int(interval_s * 1000), tick)

    notebook.bind("<<NotebookTabChanged>>", lambda _e: (tabs[notebook.index("current")][1](),
                                                         tabs[notebook.index("current")][0].draw_idle()))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.after(100, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
