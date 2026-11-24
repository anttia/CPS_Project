"""Analyysin testi ilman laitetta: python test_validoi_ajoitus.py"""
import random
from validoi_ajoitus import analyze

random.seed(1)
records, ticks = [], 0
for s in range(1, 601):                         # 10 min, tikki 0,2 % liian nopea
    for k in range(5):
        records.append((s - 1 + k * 0.2 + random.uniform(0, 0.02), "0,10,50,150,1,5000,100,500"))
    ticks = round(s * 100 * 1.002)
    records.append((s + random.uniform(0, 0.02), f"#diag,{ticks},0,16,41"))
res, checks = analyze(records)
assert abs(res["virhe_ppm"] - 2000) < 100, res
assert all(ok for _, ok, _ in checks), checks

records.append((601.0, f"#diag,{ticks + 103},3,9500,812"))   # 3 ohitettua tikkiä
res, checks = analyze(records)
assert res["myohastyneet"] == 3 and not all(ok for _, ok, _ in checks)
print("validoi_ajoitus: analyysin testit läpäisty")

# Vanha firmware (ei #diag-rivejä): telemetrian välit lasketaan silti vertailua varten
old = [(i * 0.2 + (0.03 if i % 10 == 0 else 0), "0,10,50,150,2,5000,100,500") for i in range(1000)]
res, checks = analyze(old)
assert abs(res["telemetria_hz"] - 5) < 0.01 and res["vali_max_ms"] > 220, res
assert not all(ok for _, ok, _ in checks)
print("validoi_ajoitus: vanhan firmwaren vertailu läpäisty")

# Uusi telemetriamuoto (numero ja tarkistussumma) tunnistetaan samoin
from validoi_ajoitus import is_telemetry
assert is_telemetry("1,150,42,150,1,4987,412,2050,17*32") and is_telemetry("0,10,50,150,1,5000,100,500")
assert not is_telemetry("#diag,1200,0,12,41")
print("validoi_ajoitus: uusi telemetriamuoto tunnistetaan")
