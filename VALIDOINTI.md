# Validointi

Jokaisen firmwaren muutoksen testi todistaa kaksi asiaa erikseen:

1. **Muutos toimii niin kuin pitää.** Laudalla tehdään askeleet, joilla on numeeriset hyväksymiskriteerit.
2. **Muutoksesta on hyötyä laitteelle.** Sama mittaus tehdään **ennen** (päähaaran firmware) ja **jälkeen** (PR:n firmware), ja ero kirjataan numeroina. Jos hyötyä ei voi vielä mitata laitteen toiminnasta, sanotaan suoraan, mitä muutos mahdollistaa ja millä mittauksella se todennetaan myöhemmin.

Ennen laudalla tehtävää testiä muutos tarkistetaan kahdella tavalla. Logiikka testataan PC:llä (`firmware/luxlamppu/test/`). Firmware ajetaan simulaattorissa (simavr), kun se on mahdollista. **PR:ää ei yhdistetä päähaaraan ennen kuin se on testattu laudalla.** Laudalla testaaja kirjaa tulokset PR:ään ja raportin validointitaulukkoon (luku 5.5).

Kaikki validointityökalut ovat kansiossa `validointi/`. Jokaisella skriptillä on oma testinsä, jonka voi ajaa ilman laitetta.

---

## Konsepti 1: Timer1-tikki ja keskeytys (PR #1)

**Mitä muuttui:** ajoitus tulee Timer1:n 10 ms:n vertailukeskeytyksestä eikä Arduinon `millis()`-funktiosta. Keskeytysrutiini kasvattaa `volatile`-laskuria, ja pääsilmukka lukee laskurin atomisesti.

**Mittarit firmwaressa:**
- Komento `D,1` kytkee päälle rivin `#diag,tikit,ohitetut,suurin_viive_us,kuorma_promillea`, joka lähetetään kerran sekunnissa. `D,0` kytkee sen pois. Dashboard ohittaa #-rivit.
  - *ohitetut:* kuinka monta kertaa pääsilmukka ei ehtinyt käsitellä tikkiä ennen seuraavaa
  - *suurin_viive_us:* suurin aika tikistä siihen, kun pääsilmukka alkaa käsitellä sitä. Aika mitataan TCNT1:stä 4 µs:n tarkkuudella.
  - *kuorma:* kuinka suuren osan ajasta pääsilmukka tekee työtä. Loppu on odotusta.
- Pinni **D8 (PB0)** vaihtaa tilaansa jokaisessa keskeytyksessä. Tuloksena on 50 Hz:n kanttiaalto, jossa 10 ms on ylhäällä ja 10 ms alhaalla.

### 1) Toimii: laudalla tehtävät askeleet

| # | Askel | Hyväksytty, kun |
| --- | --- | --- |
| 1 | Lataa PR:n firmware. Käynnistä dashboard ja seuraa sitä 2 min. | Data päivittyy tasaisesti (5 riviä/s), eikä punaista ilmoitusta tule. Tavoitteen liukusäädin muuttaa LEDien kirkkautta. |
| 2 | Sulje dashboard. Aja `python validointi/validoi_ajoitus.py --kesto 600 --pitoaika 20`. Pysy ensin poissa PIR:n näkyvistä, kunnes nauha on sammunut, ja palaa sitten. Toista koko mittauksen ajan (noin 1 min jaksoissa). Tallenna tuloste nimellä `jalkeen.txt`. | `KAIKKI KRITEERIT TÄYTTYVÄT`: tikin taajuus 100 Hz ± 0,5 %, telemetria 5 riviä/s ± 2 %, 0 ohitettua tikkiä ja suurin viive alle 10 ms. |
| 3 | Mittaa sekuntikellolla sammumisanimaation kesto (pitoaika on edelleen 20 s). | 30 s ± 1 s |
| 4 | Mittaa D8:n taajuus yleismittarin Hz-asteikolla tai oskilloskoopilla (maa GND:hen). | 50,0 Hz ± 0,5 %. Skoopilla jakso on 20 ms ja pulssisuhde 50 %. |
| 5 | Paina Unon reset-nappia, jolloin pitoaika palaa 180 s:iin. | – |

Skripti tallentaa raakalokin tiedostoon `validointi_ajoitus_<pvm>.log`. Lokin voi analysoida uudelleen komennolla `--tiedosto`.

### 2) Hyöty: ennen/jälkeen

**Laudalla:** lataa päähaaran (`main`) firmware ja toista askel 2 samalla tavalla. Vanhassa firmwaressa ei ole #diag-rivejä, joten skripti ilmoittaa niistä HYL. Telemetrian välit se kuitenkin mittaa PC:n kellolla. Tallenna tuloste nimellä `ennen.txt` ja täytä taulukko:

| Mittari | Ennen (`main`, millis) | Jälkeen (PR #1) | Miksi sillä on väliä laitteelle |
| --- | --- | --- | --- |
| Telemetrian välin hajonta ja suurin väli (`vali_hajonta_ms`, `vali_max_ms`), animaatiot mukana | *mitataan* | *mitataan* | Kun pääsilmukka ei jää jumiin animaation laskentaan, mittaukset ja säätö tapahtuvat ajallaan. Ennuste: vanhassa firmwaressa animaation aikana yksi kehys ja telemetrian keskiarvo laskettiin kumpikin LED kerrallaan, eli 78 kertaa raskas laskenta, joten välit venyvät. |
| Animaation kesto sekuntikellolla | *mitataan* | *mitataan* | Animaation pitää kestää suunnitellut 30 s, eikä sen pidä nykiä. |
| Suoritinkuorma säätötilassa ja animaatiossa | ei mitattavissa | *mitataan* (simavr: ~4 % / enintään 36 %) | Uusi mittari. Vapaa osuus (~96 %) on se aika, jonka seuraava konsepti (unitilat) voi nukkua. |
| Ohitetut tikit animaation aikana | – | 0 (simavr) | |

**Simulaattorissa (jo tehty):**

| Mittari | Ennen nopeutusta | Jälkeen | Merkitys |
| --- | --- | --- | --- |
| Ohitetut tikit animaation aikana | 153 kpl noin 10 s:ssa | 0 kpl | Mittari paljasti, että animaatiokehyksen laskenta vei yli 10 ms. Korjauksena koko nauha lasketaan kerralla (`lamp_render_pm`), ja PC-testi todentaa, että tulos on bitilleen sama. |
| Suoritinkuorma animaation aikana | enintään 81 % | enintään 36 % | Laskenta on yli kaksi kertaa kevyempi, joten animaatio pysyy 50 kehyksessä sekunnissa. |
| Keskeytysrutiinin hinta | – | 29 käskyä, noin 62 kellojaksoa ≈ 4 µs / 10 ms = 0,04 % | Rutiini on lyhyt, joten työ tehdään pääsilmukassa. |

Simulaattorin luvuissa valoanturi lukee 0 eikä INA219:ää ole, joten laudalla luvut ovat hieman suurempia (I2C-luku 5 kertaa sekunnissa).

**Rehellinen arvio:** ajastin itsessään ei muuta lampun käytöstä käyttäjän silmin. Sen hyöty laitteelle on kolmenlainen. Ensinnäkin ajoitus on mitattavissa ja todennettavissa. Toiseksi mittaus löysi ja auttoi korjaamaan animaation ylikuorman. Kolmanneksi ajastin on herätelähde, jota unitila tarvitsee. Unitilan energiahyöty mitataan konseptissa 2 (Lab 3:n menetelmällä).

### Simulaattorissa (ilman laitetta)

```
# käännä niin, että validointirivit ovat päällä heti käynnistyksestä
arduino-cli compile --fqbn arduino:avr:uno --build-property "compiler.cpp.extra_flags=-DDIAG_DEFAULT=1" --output-dir out firmware/luxlamppu
simavr -m atmega328p -f 16000000 out/luxlamppu.ino.elf > sim.out   # pysäytä noin 75 s:n jälkeen (Ctrl+C)
python validointi/validoi_ajoitus.py --simavr < sim.out
```

Simulaattori ei tunne PC:n kelloa, joten tikin taajuutta ei tarkisteta. Sen sijaan tarkistetaan, että validointirivi tulee tasan 100 tikin välein.

---

## Konsepti 2: PIR pin change -keskeytyksellä

**Mitä muuttui:** PIR:iä ei enää lueta pollaamalla 100 ms:n säätöaskeleella. Pinnin D2 muutos käynnistää keskeytyksen (`PCINT2_vect`), joka kirjaa liikkeen alun. Pääsilmukka herättää lampun seuraavalla 10 ms:n tikillä (`lamp_motion()`).

**Mittari firmwaressa:** pinni **D7 (PD7)** nousee, kun pääsilmukka on käsitellyt PIR:n nousevan reunan, ja laskee, kun PIR:n signaali laskee. Viive PIR:n reunasta D7:n reunaan on liikkeen käsittelyn viive.

### 1) Toimii: laudalla tehtävät askeleet

| # | Askel | Hyväksytty, kun |
| --- | --- | --- |
| 1 | Lataa PR:n firmware ja käynnistä dashboard. Liiku PIR:n edessä ja pysy sitten paikallasi. | Dashboardin "Liikettä"-kenttä ja liikealue kaaviossa seuraavat PIR:iä kuten ennenkin. |
| 2 | Aseta pitoajaksi 20 s (`python dashboard/dashboard.py --pitoaika 20`). Poistu, odota että nauha sammuu, ja palaa. | Herääminen alkaa, kun PIR havaitsee liikkeen. Sama myös kesken sammumisanimaation: valo kääntyy kirkastumaan ilman välähdystä. |
| 3 | Kytke skooppi: kanava 1 PIR:n SIG (D2), kanava 2 D7, maa GND:hen. Laukaisu kanavan 1 nousevaan reunaan. Toista liike 20 kertaa ja kirjaa suurin viive (pysyvä näyttö tai kursorit). | D7 nousee jokaisella kerralla, ja viive on enintään yksi tikki (10 ms) plus konseptin 1 suurin viive tikistä työhön, eli käytännössä enintään noin 12 ms. |
| 4 | Aja ajoituksen validointi kuten konseptissa 1 (`validoi_ajoitus.py --kesto 600 --pitoaika 20`). | Kriteerit täyttyvät edelleen: uusi keskeytys ei aiheuta ohitettuja tikkejä. |

### 2) Hyöty: ennen/jälkeen

| Mittari | Ennen (pollaus) | Jälkeen (keskeytys) | Miksi sillä on väliä laitteelle |
| --- | --- | --- | --- |
| Viive PIR:n reunasta liikkeen käsittelyyn | 0–100 ms (säätöaskeleen jakso, laskettu) | *mitataan* skoopilla, odotus enintään 10 ms | Lamppu reagoi liikkeeseen kymmenesosassa ajasta. |
| Herätelähde unitilalle | ei ole | pin change -keskeytys | Power-down-unesta herättää vain keskeytys. Pollaus ei toimi, kun suoritin nukkuu. |

Ennen-viive on laskettu, koska vanhassa firmwaressa ei ole merkkipinniä: pollaus luki PIR:n kerran 100 ms:ssa, joten viive on reunan ajankohdasta riippuen 0–100 ms.

**Rehellinen arvio:** käyttäjä ei huomaa eroa, koska PIR itse reagoi liikkeeseen noin sekunnin viiveellä. Muutoksen hyöty on siinä, että liike käsitellään tapahtumana eikä kyselemällä, viive on mitattavissa, ja keskeytys on herätelähde, jota seuraava konsepti (unitilat) tarvitsee.


## Energy budget, step 1: energy accounting on the MCU

**What changed:** the firmware integrates the INA219 power into energy per hour of the day in fixed point (`energy_logic.h`). The dashboard sends the time of day with `C,<seconds since midnight>`, because the Uno has no real-time clock. The device reports `E` (every 10 s), `EH` (end of hour), `ED` (end of day) and `#sync` (after each `C`) lines; see the message format in the README.

**Metrics in the firmware:** the `E` lines give the energy counted by the MCU, and `#sync,<clock before>,<new clock>` shows how far the MCU clock has drifted since the previous `C` command.

### Before the board (done)

| Check | Result |
| --- | --- |
| `test_energy_logic.c`: 1 W for 1 h in 200 ms steps | exactly 1 000 000 µWh, booked into the right hour |
| 1 mW for 30 min (each sample is less than 1 µWh) | 500 µWh: the carried remainder loses nothing to rounding |
| Random power 0–4.5 W and 190–210 ms intervals for 11 h vs a double-precision reference | difference < 0.01 µWh |
| Midnight, missing INA219, clock corrections forwards/backwards/over midnight | hour and day close correctly; gaps are counted as gap time, not energy |
| `avr-g++` build for the Uno (Arduino core 1.8 + Adafruit INA219) | data 719 → 809 B of 2048 B (+90 B), flash 12.9 → 14.6 KB |
| simavr, `#diag` lines | 0 skipped ticks, CPU load 4.2 % (unchanged), `E` line every 10 s |
| `validointi/test_validoi_energia.py` (analysis without a device) | a correct MCU passes; 5 % energy error, 1 % clock drift and a missing `EH` line fail |

### 1) It works: steps on the board

| # | Step | Accepted when |
| --- | --- | --- |
| 1 | Upload the PR firmware and start the dashboard. Open the Järjestelmä tab. | Within 10 s the INA219 line shows "MCU energy today: …" and the MCU clock equals the PC clock (± 1 min, not "not synced"). |
| 2 | Close the dashboard (`python dashboard/dashboard.py stop`). Run `python validointi/validoi_energia.py --kesto 900 --pitoaika 20`. Leave the PIR's view until the strip turns off, then come back; repeat during the measurement so that both animations and control are included. Save the output as `energia_jalkeen.txt`. | `ALL CRITERIA MET`: MCU energy vs PC integral within 2 %, `EH` line for hour 9 within ± 2 s of the moment the MCU clock passes 10:00, clock drift ≤ 5000 ppm. |
| 3 | Run the timing validation as in concept 1 (`validoi_ajoitus.py --kesto 600 --pitoaika 20`). | The criteria are still met: the energy accounting does not cause skipped ticks. |
| 4 | Press the Uno's reset button. | The `E` lines start again from 0 and "not synced" until the dashboard reconnects. This is expected in step 1; EEPROM persistence is step 2. |

The MCU measures time with its resonator, so its energy can differ from the PC integral by up to the clock error (± 0.5 %). The rest of the 2 % margin covers the PC's serial timing jitter.

### 2) Benefit: before/after

| Metric | Before (`main`) | After (this PR) | Why it matters for the device |
| --- | --- | --- | --- |
| Where the strip energy is counted | only in the dashboard, while it runs | on the MCU, per hour of the day, also without the dashboard | The budget of step 2 must be enforced on the MCU, which needs its own energy meter. |
| Accuracy of the MCU energy vs the PC integral | – | *measured* (criterion ≤ 2 %) | Shows that the fixed-point integral can be trusted for budget decisions. |
| MCU clock drift without a sync | – | *measured* in ppm | Tells how often the dashboard must send the clock: at 5000 ppm, 10 min between syncs gives at most 3 s error at an hour boundary. |
| CPU load | ~4 % (simavr) | 4.2 % (simavr), *measured on the board* | The new work must not take time from control and animation. |
| SRAM | 719 B | 809 B | Leaves about 1.2 KB for the budget tables and the stack. |

**Honest assessment:** this step does not change how the lamp behaves. The dashboard already showed the energy. The benefit is that the device now knows its own energy use per hour of the day with a measured accuracy, which is what the budget in step 2 needs. Because the counters are in SRAM, they are lost on every restart. That is the measurable reason for EEPROM persistence in step 2.
