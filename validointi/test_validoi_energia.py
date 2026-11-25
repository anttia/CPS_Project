"""Test of the analysis without a device: python test_validoi_energia.py"""
import math
import random
from validoi_energia import START_CLOCK_S, analyze


def simulate(duration=900.0, clock_rate=1.002, mcu_gain=1.0, eh=True):
    """A simulated MCU whose clock runs clock_rate times too fast. mcu_gain scales the energy
    the MCU counts (1.0 = correct)."""
    random.seed(2)
    records = [(0.0, ">D,1"), (0.0, f">C,{START_CLOCK_S}"), (0.05, f"#sync,-1,{START_CLOCK_S}")]
    day_uwh = hour_uwh = 0.0
    next_e = 10.0
    hour = START_CLOCK_S // 3600
    t = 0.0
    while t < duration:
        t += 0.2
        mw = int(800 + 600 * math.sin(t / 50))
        pc_t = t + random.uniform(0, 0.02)             # serial latency
        records.append((pc_t, f"1,140,40,150,1,5000,{mw // 5},{mw}"))
        u = mw * 0.2 * clock_rate / 3.6 * mcu_gain     # the MCU's 200 ms is a bit shorter
        clock = START_CLOCK_S + t * clock_rate
        if eh and int(clock // 3600) != hour:
            records.append((pc_t, f"EH,{hour},{int(hour_uwh)},0"))
            hour, hour_uwh = int(clock // 3600), 0.0
        day_uwh += u
        hour_uwh += u
        if t >= next_e:
            records.append((pc_t, f"E,{int(clock)},1,{int(day_uwh)},{int(hour_uwh)}"))
            records.append((pc_t, f"#diag,{int(t * 100)},0,16,43"))
            next_e += 10
    records.append((t, f">C,{START_CLOCK_S + round(t)}"))
    records.append((t + 0.05, f"#sync,{int(START_CLOCK_S + t * clock_rate)},{START_CLOCK_S + round(t)}"))
    return records


res, checks = analyze(simulate())
assert abs(res["ero_pct"]) < 0.5, res
assert res["eh_tunti"] == START_CLOCK_S // 3600 and abs(res["eh_myohassa_s"]) < 1.0, res
assert abs(res["kellon_virhe_ppm"] - 2000) < 1200, res     # 1 s resolution over 900 s
assert all(ok for _, ok, _ in checks), checks
print("validoi_energia: a correct MCU passes")

res, checks = analyze(simulate(mcu_gain=1.05))
assert not checks[0][1] and res["ero_pct"] > 4, res
print("validoi_energia: 5 % too much energy fails")

res, checks = analyze(simulate(clock_rate=1.01))
assert not checks[2][1], checks
print("validoi_energia: 1 % clock drift fails")

res, checks = analyze(simulate(eh=False))
assert not checks[1][1], checks
print("validoi_energia: missing EH line fails")

old = [(i * 0.2, "1,140,40,150,1,5000,100,500") for i in range(1000)]   # main firmware
res, checks = analyze(old)
assert not any(ok for _, ok, _ in checks)
print("validoi_energia: firmware without energy accounting fails all criteria")
