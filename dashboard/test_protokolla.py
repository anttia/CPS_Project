"""Viestiprotokollan testi ilman laitetta: python dashboard/test_protokolla.py

Tarkistaa, että dashboard hyväksyy ehjät rivit, hylkää rikkoutuneet, laskee kadonneet
rivit numeroiden aukoista ja ymmärtää vanhan muodon ilman tarkistussummaa. Esimerkkirivi
on sama kuin firmwaren testissä (firmware/luxlamppu/test/test_protocol.c).
"""
import csv
import io
import os
import sys

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard as d

log = csv.writer(io.StringIO())

# Sama kehys kuin firmwaren protocol.h tuottaa
assert d.split_frame("1,150,42,150,1,4987,412,2050,17*32\r\n") == ("1,150,42,150,1,4987,412,2050,17", "ok")
assert d.parse_line("1,150,42,150,1,4987,412,2050,17")["seq"] == 17

s = d.State()
s.set_connection("testi", connected=True)
d.process_line(s, "\x00\xff,4987,412*", log)                  # käynnistyksen roska: ei lasketa
sample = d.parse_line("1,150,42,150,1,4987,412,2050")
for seq in [250, 251, 252, 255, 0, 1]:                         # 253 ja 254 kadonneet, 255 -> 0 kiertää
    d.process_line(s, d.make_frame(sample, seq), log)
d.process_line(s, "1,150,42,150,1,4987,412,2051,2*32", log)   # data muuttunut, summa ei täsmää
d.process_line(s, d.make_frame(sample, 3)[:-1] + "0", log)     # summa itse rikki
d.process_line(s, "V,Nov 24 2026 20:00:00", log)              # Arduino käynnistyi uudelleen
d.process_line(s, d.make_frame(sample, 0), log)                # numerointi alusta: ei aukkoa
assert s.link["ok"] == 7 and s.link["lost"] == 2 and s.link["bad"] == 2, s.link

old = d.State()
old.set_connection("testi", connected=True)
d.process_line(old, "0,100,50,140,1,5000,100,500", log)        # vanha firmware ilman summaa
assert old.link["plain"] == 1 and old.latest["light"] == 100

print("protokolla: ehjät, rikkoutuneet ja kadonneet rivit tunnistetaan oikein")
