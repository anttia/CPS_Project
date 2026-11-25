"""
Energy budget, step 1 (energy accounting on the MCU): validation on the board.

The script opens the lamp's serial port, sets the MCU clock to two minutes before the end
of an hour (C command), records the telemetry and the energy lines, and at the end sends
the correct clock again so that the #sync reply shows how far the MCU clock has drifted.
It saves a raw log that can be analysed again later (--tiedosto).

What is checked:
  1. Accuracy: the energy counted by the MCU (change of day_uWh in the E lines) against the
     PC's own integral of the telemetry power (the same rule as the dashboard: power x time
     since the previous line, gaps over 2 s not integrated). Criterion: difference <= 2 %.
  2. The hour closes at the right time: an EH line for the right hour arrives within 2 s of
     the moment the MCU clock passes the hour, and the hour counter starts again from zero.
  3. Clock drift of the MCU between two C commands, in ppm. The Uno's ceramic resonator is
     about +-0.5 %, so the criterion is |drift| <= 5000 ppm. The dashboard re-syncs every
     10 minutes, so the hour boundaries are off by at most about 3 s.
  4. CPU load (from #diag lines), for comparison with main using validoi_ajoitus.py.

Usage:
    python validoi_energia.py                     # finds the port, measures 15 min
    python validoi_energia.py --port COM3 --kesto 900
    python validoi_energia.py --tiedosto validointi_energia_2026-11-26_1200.log
"""
import argparse
import sys
import time
from datetime import datetime

MAX_ENERGY_ERROR_PCT = 2.0
MAX_DRIFT_PPM = 5000
MAX_HOUR_LATE_S = 2.0
PC_MAX_GAP_S = 2.0
LEAD_S = 120                      # the MCU clock is set this long before the end of an hour
START_CLOCK_S = 10 * 3600 - LEAD_S   # 09:58:00


def parse_telemetry_mw(line):
    parts = line.split(",")
    if len(parts) != 8:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    return values[7]


def parse_e(line):
    """'E,clock_s,synced,day_uWh,hour_uWh' -> tuple of ints, else None."""
    parts = line.split(",")
    if parts[0] != "E" or len(parts) != 5:
        return None
    try:
        return tuple(int(p) for p in parts[1:])
    except ValueError:
        return None


def parse_fields(line, prefix, n):
    parts = line.split(",")
    if parts[0] != prefix or len(parts) != n + 1:
        return None
    try:
        return tuple(int(p) for p in parts[1:])
    except ValueError:
        return None


def analyze(records):
    """records: list of (pc_time_s, line). Returns (results, checks)."""
    res, checks = {}, []
    sync_sent = [(t, line) for t, line in records if line.startswith(">C,")]
    es = [(t, e) for t, line in records if (e := parse_e(line))]
    synced = [(t, e) for t, e in es if e[1] == 1]

    # 1. Accuracy: MCU energy vs the PC's integral over the same span (first to last synced E line)
    if len(synced) >= 2:
        t0, e0 = synced[0]
        t1, e1 = synced[-1]
        # The MCU day total only resets at midnight, which this measurement does not cross
        mcu_uwh = e1[2] - e0[2]
        pc_uwh, last_t = 0.0, None
        for t, line in records:
            mw = parse_telemetry_mw(line)
            if mw is None:
                continue
            if last_t is not None and t0 < t <= t1 and mw >= 0 and t - last_t <= PC_MAX_GAP_S:
                pc_uwh += mw * (t - last_t) * 1000 / 3600
            last_t = t
        res["mcu_mwh"] = mcu_uwh / 1000
        res["pc_mwh"] = pc_uwh / 1000
        res["kesto_min"] = (t1 - t0) / 60
        if pc_uwh > 0:
            err = (mcu_uwh - pc_uwh) / pc_uwh * 100
            res["ero_pct"] = err
            checks.append((f"MCU energy vs PC integral (<= {MAX_ENERGY_ERROR_PCT} %)",
                           abs(err) <= MAX_ENERGY_ERROR_PCT, f"{err:+.2f} %"))
        else:
            checks.append(("MCU energy vs PC integral", False,
                           "no power in the telemetry (INA219 missing or strip off?)"))
    else:
        checks.append(("MCU energy vs PC integral", False, "fewer than 2 synced E lines"))

    # 2. The hour closes when the MCU clock passes it
    ehs = [(t, eh) for t, line in records if (eh := parse_fields(line, "EH", 3))]
    if sync_sent and ehs:
        t_sync, line = sync_sent[0]
        clock0 = int(line.split(",")[1])
        expected = t_sync + (3600 - clock0 % 3600)
        t_eh, (hour, uwh, gap_s) = ehs[0]
        late = t_eh - expected
        res["eh_tunti"] = hour
        res["eh_mwh"] = uwh / 1000
        res["eh_aukko_s"] = gap_s
        res["eh_myohassa_s"] = late
        after = [e for t, e in synced if t > t_eh]
        restarted = bool(after) and after[0][3] < uwh
        checks.append(("EH line for the right hour at the right time "
                       f"(+-{MAX_HOUR_LATE_S:.0f} s)",
                       hour == clock0 // 3600 and abs(late) <= MAX_HOUR_LATE_S and restarted,
                       f"hour {hour}, {late:+.1f} s, hour counter restarted: {restarted}"))
    else:
        checks.append(("EH line at the end of the hour", False, "no EH line"))

    # 3. Clock drift between the first and the last C command
    syncs = [(t, s) for t, line in records if (s := parse_fields(line, "#sync", 2))]
    if len(sync_sent) >= 2 and len(syncs) >= 2 and syncs[-1][1][0] >= 0:
        t_a, line_a = sync_sent[0]
        t_b, line_b = sync_sent[-1]
        set_a = int(line_a.split(",")[1])
        set_b = int(line_b.split(",")[1])
        mcu_before_b = syncs[-1][1][0]
        elapsed_pc = set_b - set_a
        elapsed_mcu = mcu_before_b - set_a
        if elapsed_pc > 0:
            drift = (elapsed_mcu - elapsed_pc) / elapsed_pc * 1e6
            res["kellon_virhe_s"] = elapsed_mcu - elapsed_pc
            res["kellon_virhe_ppm"] = drift
            # whole seconds at both ends: +-1 s resolution
            res["resoluutio_ppm"] = 1e6 / elapsed_pc
            checks.append((f"MCU clock drift (<= {MAX_DRIFT_PPM} ppm)",
                           abs(drift) <= MAX_DRIFT_PPM + res["resoluutio_ppm"],
                           f"{drift:+.0f} ppm ({elapsed_mcu - elapsed_pc:+d} s in {elapsed_pc} s)"))
    else:
        checks.append(("MCU clock drift", False, "no second C command / #sync reply"))

    # 4. CPU load
    loads = []
    for _, line in records:
        d = parse_fields(line, "#diag", 4)
        if d:
            loads.append(d[3] / 10)
    if loads:
        res["kuorma_ka_pct"] = sum(loads) / len(loads)
        res["kuorma_max_pct"] = max(loads)
    return res, checks


def report(res, checks, out=sys.stdout):
    print("\nResults", file=out)
    for key, value in res.items():
        print(f"  {key:16s} {value:.4f}" if isinstance(value, float) else f"  {key:16s} {value}",
              file=out)
    print("\nAcceptance criteria", file=out)
    for name, ok, value in checks:
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {value}", file=out)
    passed = all(ok for _, ok, _ in checks)
    print("\nALL CRITERIA MET" if passed else "\nNOT ALL CRITERIA MET", file=out)
    return passed


def measure(port, baud, duration, log_path, hold_s=None):
    import serial
    records = []
    with serial.Serial(port, baud, timeout=1) as ser, open(log_path, "w", encoding="utf-8") as log:
        def send(line, t):
            ser.write((line + "\n").encode("ascii"))
            records.append((t, ">" + line))          # commands are logged with a '>' prefix
            log.write(f"{t:.4f} >{line}\n")

        time.sleep(2.5)                               # the Uno restarts when the port opens
        ser.reset_input_buffer()
        start = time.monotonic()
        send("D,1", 0.0)
        if hold_s:
            send(f"H,{hold_s}", 0.0)
        send(f"C,{START_CLOCK_S}", 0.0)
        print(f"Measuring {duration:.0f} s on {port}, log: {log_path}")
        print(f"MCU clock set to {START_CLOCK_S // 3600:02d}:{START_CLOCK_S // 60 % 60:02d}; "
              f"the hour should close after {LEAD_S} s.")
        try:
            while time.monotonic() - start < duration:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if not line:
                    continue
                t = time.monotonic() - start
                records.append((t, line))
                log.write(f"{t:.4f} {line}\n")
                if line.startswith(("E,", "EH,", "#sync")):
                    print(f"\r  {t:6.0f} s  {line}          ", end="\n" if line[1] == "H" else "",
                          flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted, analysing the data collected so far.")
        finally:
            t = time.monotonic() - start
            send(f"C,{START_CLOCK_S + round(t)}", t)  # the true clock: #sync shows the drift
            end = time.monotonic() + 3
            while time.monotonic() < end:
                line = ser.readline().decode("ascii", errors="ignore").strip()
                if line:
                    tt = time.monotonic() - start
                    records.append((tt, line))
                    log.write(f"{tt:.4f} {line}\n")
            ser.write(b"D,0\n")
    print()
    return records


def read_log(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for row in f:
            t, _, line = row.strip().partition(" ")
            if line:
                records.append((float(t), line))
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--kesto", type=float, default=900, help="measurement length in seconds "
                    f"(at least {LEAD_S + 60})")
    ap.add_argument("--pitoaika", type=int, help="lamp hold time (s), e.g. 20, so that the "
                    "animations are part of the measurement")
    ap.add_argument("--tiedosto", help="analyse a saved log")
    args = ap.parse_args()

    if args.tiedosto:
        records = read_log(args.tiedosto)
    else:
        port = args.port
        if not port:
            sys.path.insert(0, __file__.rsplit("validointi", 1)[0] + "dashboard")
            from dashboard import find_port
            port = find_port()
        log_path = datetime.now().strftime("validointi_energia_%Y-%m-%d_%H%M.log")
        records = measure(port, args.baud, max(args.kesto, LEAD_S + 60), log_path, args.pitoaika)

    res, checks = analyze(records)
    sys.exit(0 if report(res, checks) else 1)


if __name__ == "__main__":
    main()
