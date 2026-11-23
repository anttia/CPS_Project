# CPS-projekti: luxanturin ohjaama lamppu

Cyber-Physical Systems -kurssin lopputyö. Lamppu (osoitettava LED-nauha) pitää työpisteen valaistuksen tavoitearvossa valoanturin avulla ja sammuu, kun liiketunnistin ei havaitse ketään. Mittausdata kulkee USB:n kautta PC:n dashboardille.

## Rakenne

| Kansio | Sisältö |
| --- | --- |
| `firmware/luxlamppu/` | Arduino Uno R3: PIR, valoanturi, INA219-virtamittaus ja LED-nauhan kirkkaus dashboardin komennolla |
| `firmware/valo_liike_usb/` | varhainen testi: lukee vain PIR- ja valoanturin ja lähettää arvot USB:n kautta |
| `firmware/cps_test_led_strip/` | LED-nauhan testi: kirkkaus 0–100 % ja värin vaihto, data D6:ssa, virtabudjetti asetettavissa |
| `dashboard/` | PC-dashboard (Python): liike, valoisuus, LED-kirkkaus, jännite, virta, teho ja energia; kirkkauden ohjaus |

## Kytkennät (Arduino Uno R3)

| Osa | Johto / pinni | Kytketään |
| --- | --- | --- |
| Grove PIR Motion Sensor v1.2 | keltainen (SIG) | D2 |
| Grove Light Sensor v1.3 | keltainen (SIG) | A0 |
| Grove-anturit | punainen (VCC) / musta (GND) | 5V / GND |
| LED-nauha | DIN | 330 Ω -> D6 |
| LED-nauha | +5V | INA219 VIN− |
| USB-breakout (nauhan virta) | VBUS | INA219 VIN+ |
| INA219 | VCC / GND | 5V / GND |
| INA219 | SDA / SCL | A4 / A5 |

Grove-johtojen valkoinen johdin (NC) jää kytkemättä. PIR:n jumpperi Re_trig-asentoon. Kaikkien osien GND kytketään yhteen (Arduino, USB-breakout, LED-nauha, INA219), mutta USB-breakoutin VBUS ei saa olla yhteydessä Arduinon 5V-pinniin.

## Käyttö

1. **Firmware:** asenna Arduino IDE:n Library Managerista *Adafruit INA219*. Avaa `firmware/luxlamppu/luxlamppu.ino`, aseta `NUM_LEDS` nauhan LEDien määräksi, valitse kortiksi Arduino Uno ja oikea portti, ja paina Upload. Valoanturi ja LED-nauha käyttävät rekistereitä suoraan; INA219 luetaan toistaiseksi kirjastolla.
2. **Dashboard:**
   ```
   cd dashboard
   pip install -r requirements.txt
   python dashboard.py              # etsii Arduinon portin automaattisesti
   python dashboard.py --port COM3  # tai portti käsin
   python dashboard.py --demo       # testidata ilman Arduinoa
   ```
   Sulje Arduino IDE:n Serial Monitor ennen dashboardin käynnistystä: sama portti ei voi olla auki kahdessa ohjelmassa. Jos portti on varattu, puuttuu tai data lakkaa tulemasta, dashboard näyttää punaisen ilmoituksen ja yhdistää automaattisesti uudelleen, kun portti vapautuu.

Dashboardin alareunan liukusäädin asettaa LED-nauhan kirkkauden (0–100 %). Kaavion violetti käyrä on Arduinon ilmoittama todellinen kirkkaus; dashboard lähettää komennon uudelleen, jos Arduino ei ole ottanut sitä käyttöön (esim. Arduinon uudelleenkäynnistyksen jälkeen). "100 %" on suurin kirkkaus, jonka firmwaren virtabudjetti `CURRENT_LIMIT_MA` sallii valkoiselle.

Dashboard tallentaa kaiken vastaanotetun datan tiedostoon `mittaus_<päivämäärä>.csv`.

## Viestimuoto

Sarjaportin nopeus on 9600 baudia (8N1).

**Arduino → PC**, viisi kertaa sekunnissa: `pir,valo,kirkkaus,jannite_mV,virta_mA,teho_mW`, esimerkiksi `1,182,40,4987,412,2050`.

- `pir`: 1 kun liikettä havaitaan, muuten 0
- `valo`: valoanturin ADC-lukema 0–1023 (neljän näytteen keskiarvo)
- `kirkkaus`: LED-nauhan nykyinen kirkkaus prosentteina
- `jannite_mV`, `virta_mA`, `teho_mW`: INA219:n mittaama nauhan syöttö; -1, jos INA219 ei vastaa

Dashboard ymmärtää myös vanhan muodon `pir,valo` (`firmware/valo_liike_usb`).

**PC → Arduino:** `B,<0-100>` asettaa LED-kirkkauden prosentteina.
