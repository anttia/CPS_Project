"""
Konsepti 1 (Timer1-tikki ja keskeytys): ajoituksen validointi.

Skripti avaa lampun sarjaportin, kytkee validointirivit päälle komennolla D,1 ja kerää
mittausta halutun ajan. Lopuksi se tulostaa tulokset ja hyväksymiskriteerit ja tallentaa
raakalokin, jonka voi analysoida myöhemmin uudelleen (--tiedosto).

Laite lähettää kerran sekunnissa rivin
    #diag,tikit,myöhästyneet,suurin_viive_us,kuorma_promillea
ja viisi kertaa sekunnissa tavallisen telemetriarivin.

Mitä tarkistetaan:
  1. Tikin taajuus PC:n kelloon verrattuna (tavoite 100 Hz, Unon resonaattori ±0,5 %).
     Tikkilaskurin kasvu sovitetaan suoraksi PC:n aikaleimoja vasten, joten sarjaportin
     viiveen vaihtelu keskiarvoistuu pois.
  2. Telemetrian tahti (tavoite 5 riviä/s) ja rivien välien vaihtelu (hajonta, 99. persentiili,
     suurin). Tämä mitataan PC:n kellolla, joten se toimii myös vanhalla millis()-firmwarella:
     aja skripti ensin päähaaran firmwarella (ennen) ja sitten uudella (jälkeen).
  3. Ohitetut tikit: pääsilmukan pitää ehtiä käsitellä jokainen tikki (tavoite 0).
  4. Suurin viive tikistä työn alkuun (tavoite < 10 ms eli alle yhden tikin: jokainen tehtävä
     alkaa sen tikin aikana, jolle se kuuluu). Lepotilassa viive on noin 10–20 µs;
     animaation aikana edellisen kehyksen laskenta voi viivästyttää seuraavaa tikkiä.
  5. Suoritinkuorma: kuinka suuren osan ajasta pääsilmukka tekee työtä. Loppuosa on aikaa,
     jonka seuraava konsepti (unitilat) voi nukkua.

Käyttö:
    python validoi_ajoitus.py                    # etsii portin, mittaa 10 min
    python validoi_ajoitus.py --port COM3 --kesto 120
    python validoi_ajoitus.py --pitoaika 20      # animaatiot mukaan: poistu ja palaa PIR:n eteen
    python validoi_ajoitus.py --tiedosto validointi_ajoitus_2026-11-24_1200.log
    simavr -m atmega328p -f 16000000 luxlamppu.ino.elf | python validoi_ajoitus.py --simavr
"""
import argparse
import re
import sys
import time
from datetime import datetime

TICK_HZ = 100.0
TELEMETRY_HZ = 5.0
MAX_RATE_ERROR_PPM = 5000      # Unon keraaminen resonaattori: ±0,5 %
MAX_LATENCY_US = 10000         # työn pitää alkaa saman tikin aikana, jolle se kuuluu


def parse_diag(line):
    """'#diag,1200,0,12,55' -> (tikit, myöhästyneet, viive_us, kuorma_pm) tai None."""
    line = line.strip()
    if not line.startswith("#diag,"):
        return None
    try:
        ticks, late, lat, busy = (int(v) for v in line.split(",")[1:5])
    except ValueError:
        return None
    return ticks, late, lat, busy


def is_telemetry(line):
    parts = line.strip().split(",")
    return len(parts) == 8 and all(re.fullmatch(r"-?\d+", p) for p in parts)


def fit_slope(xs, ys):
    """Pienimmän neliösumman suoran kulmakerroin."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None


def analyze(records, wall_clock=True):
    """records: lista (pc_aika_s, rivi). Palauttaa (tulokset, kriteerit)."""
    diags = [(t, d) for t, line in records if (d := parse_diag(line))]
    telem = [t for t, line in records if is_telemetry(line)]
    res, checks = {}, []
    if wall_clock and len(telem) >= 3:
        # Telemetriarivien välit PC:n kellolla: toimii myös vanhalla firmwarella, jossa ei ole
        # #diag-rivejä, joten tästä saadaan ennen/jälkeen-vertailu.
        gaps = sorted((b - a) * 1000 for a, b in zip(telem, telem[1:]))
        mean = sum(gaps) / len(gaps)
        res["telemetria_hz"] = 1000 / mean
        res["vali_ka_ms"] = mean
        res["vali_hajonta_ms"] = (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5
        res["vali_p99_ms"] = gaps[min(len(gaps) - 1, int(0.99 * len(gaps)))]
        res["vali_max_ms"] = gaps[-1]
        checks.append(("Telemetria 5 riviä/s ±2 %", abs(res["telemetria_hz"] / TELEMETRY_HZ - 1) <= 0.02,
                       f"{res['telemetria_hz']:.3f} riviä/s"))
    if len(diags) < 2:
        checks.append(("Validointirivejä (#diag) vähintään 2 (vanha firmware: ei ole)", False,
                       f"{len(diags)} kpl"))
        return res, checks

    res["diag_rivit"] = len(diags)
    res["myohastyneet"] = diags[-1][1][1] - diags[0][1][1]
    res["viive_max_us"] = max(d[2] for _, d in diags[1:])
    loads = [d[3] / 10 for _, d in diags[1:]]
    res["kuorma_ka_pct"] = sum(loads) / len(loads)
    res["kuorma_max_pct"] = max(loads)
    res["vapaa_pct"] = 100 - res["kuorma_ka_pct"]

    if wall_clock:
        rate = fit_slope([t for t, _ in diags], [d[0] for _, d in diags])
        res["kesto_s"] = diags[-1][0] - diags[0][0]
        res["tikki_hz"] = rate
        res["virhe_ppm"] = (rate / TICK_HZ - 1) * 1e6
        checks.append((f"Tikin taajuus {TICK_HZ:.0f} Hz ±{MAX_RATE_ERROR_PPM / 1e4:.1f} %",
                       abs(res["virhe_ppm"]) <= MAX_RATE_ERROR_PPM,
                       f"{rate:.4f} Hz ({res['virhe_ppm']:+.0f} ppm)"))
    else:
        steps = [b[1][0] - a[1][0] for a, b in zip(diags, diags[1:])]
        checks.append(("Validointirivi 100 tikin välein", all(s == 100 for s in steps),
                       f"välit {min(steps)}–{max(steps)} tikkiä"))

    checks.append(("Ohitettuja tikkejä 0", res["myohastyneet"] == 0, f"{res['myohastyneet']} kpl"))
    checks.append((f"Suurin viive tikistä työhön < {MAX_LATENCY_US} µs",
                   res["viive_max_us"] < MAX_LATENCY_US, f"{res['viive_max_us']} µs"))
    return res, checks


def report(res, checks, out=sys.stdout):
    print("\nTulokset", file=out)
    for key, value in res.items():
        print(f"  {key:16s} {value:.4f}" if isinstance(value, float) else f"  {key:16s} {value}", file=out)
    if "kuorma_ka_pct" in res:
        print(f"\n  Pääsilmukka tekee työtä keskimäärin {res['kuorma_ka_pct']:.1f} % ajasta "
              f"(enintään {res['kuorma_max_pct']:.1f} %). Loput {res['vapaa_pct']:.1f} % on "
              f"odotusta, jonka unitila voi korvata.", file=out)
    print("\nHyväksymiskriteerit", file=out)
    for name, ok, value in checks:
        print(f"  [{'OK ' if ok else 'HYL'}] {name}: {value}", file=out)
    passed = all(ok for _, ok, _ in checks)
    print("\nKAIKKI KRITEERIT TÄYTTYVÄT" if passed else "\nKAIKKI KRITEERIT EIVÄT TÄYTY", file=out)
    return passed


def measure(port, baud, duration, log_path, hold_s=None):
    import serial
    records = []
    with serial.Serial(port, baud, timeout=1) as ser, open(log_path, "w", encoding="utf-8") as log:
        time.sleep(2.5)                               # Uno käynnistyy uudelleen, kun portti avataan
        ser.reset_input_buffer()
        ser.write(b"D,1\n")
        if hold_s:
            ser.write(f"H,{hold_s}\n".encode("ascii"))   # lyhyt pitoaika: animaatiot mukaan mittaukseen
        start = time.monotonic()
        print(f"Mitataan {duration} s portista {port}, loki: {log_path}")
        try:
            while time.monotonic() - start < duration:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                t = time.monotonic() - start
                records.append((t, line))
                log.write(f"{t:.4f} {line}\n")
                if line.startswith("#diag"):
                    print(f"\r  {t:6.0f} s  {line}   ", end="", flush=True)
        except KeyboardInterrupt:
            print("\nKeskeytetty, analysoidaan kerätty data.")
        finally:
            ser.write(b"D,0\n")
    return records


def read_log(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for row in f:
            t, _, line = row.strip().partition(" ")
            records.append((float(t), line))
    return records


def read_simavr(stream):
    """simavr tulostaa UARTin rivit väreillä ja '..'-päätteellä; aikaleimoja ei ole."""
    records = []
    for row in stream:
        line = re.sub(r"\x1b\[[0-9;]*m", "", row).strip().rstrip(".")
        if line:
            records.append((0.0, line))
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--kesto", type=float, default=600, help="mittauksen kesto sekunteina")
    ap.add_argument("--pitoaika", type=int, help="aseta lampun pitoaika (s), esim. 20, jotta "
                    "sammumis- ja heräämisanimaatiot tulevat mittaukseen")
    ap.add_argument("--tiedosto", help="analysoi aiemmin tallennettu loki")
    ap.add_argument("--simavr", action="store_true", help="lue simavr:n tuloste stdinistä")
    args = ap.parse_args()

    if args.simavr:
        records, wall = read_simavr(sys.stdin), False
    elif args.tiedosto:
        records, wall = read_log(args.tiedosto), True
    else:
        port = args.port
        if not port:
            sys.path.insert(0, __file__.rsplit("validointi", 1)[0] + "dashboard")
            from dashboard import find_port
            port = find_port()
        log_path = datetime.now().strftime("validointi_ajoitus_%Y-%m-%d_%H%M.log")
        records, wall = measure(port, args.baud, args.kesto, log_path, args.pitoaika), True

    res, checks = analyze(records, wall_clock=wall)
    sys.exit(0 if report(res, checks) else 1)


if __name__ == "__main__":
    main()
