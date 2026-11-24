# CPS-projekti: luxanturin ohjaama lamppu

Cyber-Physical Systems -kurssin lopputyö. Lamppu (osoitettava LED-nauha) pitää työpisteen valaistuksen tavoitearvossa valoanturin avulla: LED-nauha on toimilaite, jonka kirkkautta säädin muuttaa, kunnes anturin lukema on tavoitteessa. Kun liiketunnistin ei ole havainnut ketään pitoaikaan (oletus 3 min), nauha sammuu 30 sekunnin animaatiolla: LEDit himmenevät pehmeästi reunoilta kohti keskikohtaa. Kun liike havaitaan uudelleen, valo leviää 30 sekunnissa keskeltä reunoille ja kirkastuu, minkä jälkeen säätö jatkuu. Jos liike havaitaan kesken sammumisen, nauha alkaa kirkastua samasta kohdasta. Animaation kesto on `lamp_logic.h`:n vakio `LAMP_ANIM_MS`. Mittausdata kulkee USB:n kautta PC:n dashboardille.

## Rakenne

| Kansio | Sisältö |
| --- | --- |
| `firmware/luxlamppu/` | Arduino Uno R3: suljettu valon säätö, PIR-sammutus ja -herätys animaatioilla, INA219-virtamittaus, ajoitus omalla Timer1-keskeytyksellä |
| `firmware/luxlamppu/lamp_logic.h` | lampun logiikka ilman laitteistoa: suodatus, I-säädin, tilakone, animaatiot |
| `firmware/luxlamppu/test/` | logiikan testi PC:llä simuloidussa huoneessa |
| `firmware/valo_liike_usb/` | varhainen testi: lukee vain PIR- ja valoanturin ja lähettää arvot USB:n kautta |
| `firmware/cps_test_led_strip/` | LED-nauhan testi: kirkkaus 0–100 % ja värin vaihto, data D6:ssa, virtabudjetti asetettavissa |
| `dashboard/` | PC-dashboard (Python): liike, valoisuus, LED-kirkkaus, jännite, virta, teho ja energia; valoisuuden tavoitteen ja pitoajan asetus |

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
   **Ilman Arduino IDE:tä:** `deploy.ps1` sulkee käynnissä olevan dashboardin, kääntää ja lataa firmwaren [arduino-cli:llä](https://arduino.github.io/arduino-cli/) (portti tunnistetaan automaattisesti) ja käynnistää dashboardin uudelleen, jolloin myös dashboardin uusin koodi on käytössä:
   ```
   .\deploy.ps1                          # luxlamppu
   .\deploy.ps1 -Sketch cps_blink_colors # muu sketsi firmware-kansiosta
   .\deploy.ps1 -NoDashboard             # vain firmware
   ```
   Tarvitaan kerran: `scoop install arduino-cli`, `arduino-cli core install arduino:avr` ja `arduino-cli lib install "Adafruit INA219"`. Dashboardin tilarivillä näkyy firmwaren käännösaika, josta näkee, että uusi versio on kortilla.
2. **Dashboard:**
   ```
   cd dashboard
   pip install -r requirements.txt
   python dashboard.py              # etsii Arduinon portin automaattisesti
   python dashboard.py --port COM3  # tai portti käsin
   python dashboard.py --demo       # testidata ilman Arduinoa (3 h valmiina historiana)
   python dashboard.py stop         # sulje käynnissä oleva dashboard toisesta päätteestä
   pythonw dashboard.py             # Windows: käynnistä ilman konsoli-ikkunaa
   python dashboard.py --paivitys 5 # näkymän päivitysväli sekunteina (oletus 2; data tallentuu silti 5 kertaa sekunnissa)
   ```
   `stop` lähettää käskyn käynnissä olevalle dashboardille paikallisen TCP-portin 47219 kautta ja odottaa, että se on sulkeutunut. Sen jälkeen sarjaportti on vapaa, esimerkiksi firmwaren lataamiseen.
   Sulje Arduino IDE:n Serial Monitor ennen dashboardin käynnistystä: sama portti ei voi olla auki kahdessa ohjelmassa. Jos portti on varattu, puuttuu tai data lakkaa tulemasta, dashboard näyttää punaisen ilmoituksen ja yhdistää automaattisesti uudelleen, kun portti vapautuu.

Dashboardin alareunan liukusäädin asettaa **tavoitteen valoanturin yksiköissä** (vihreä katkoviiva), ja Arduino säätää LED-nauhan kirkkautta (violetti käyrä) niin, että anturin lukema pysyy tavoitteessa. Painike "Käytä nykyistä lukemaa" asettaa tavoitteeksi anturin sen hetkisen lukeman. Dashboard lähettää tavoitteen ja pitoajan aina yhteyden alussa ja uudelleen, jos Arduinon ilmoittama tavoite ei vastaa liukusäädintä. "100 %" on suurin kirkkaus, jonka firmwaren virtabudjetti `CURRENT_LIMIT_MA` sallii valkoiselle.

Pitoajan ja säätimen kertoimen voi antaa käynnistyksessä, esim. `python dashboard.py --pitoaika 20 --ki 3`. Lyhyt pitoaika on kätevä animaatioiden testaamiseen.

## Ajoitus: Timer1 ja keskeytys

Firmware ei käytä Arduinon `millis()`-funktiota. Ajan perusyksikkö on oma 10 ms:n tikki Timer1:ltä:

- Timer1 on CTC-tilassa: laskuri kasvaa 4 µs:n askelin ja nollautuu, kun se saavuttaa OCR1A:n. Samalla syntyy vertailukeskeytys.
- Keskeytysrutiini `ISR(TIMER1_COMPA_vect)` vain kasvattaa `volatile`-tikkilaskuria. Pääsilmukka lukee laskurin `ATOMIC_BLOCK`issa ja ajaa tehtävät tikkien mukaan: säätö 10 tikin (100 ms), animaatio 2 tikin (20 ms) ja telemetria 20 tikin (200 ms) välein.
- Jakso: 16 MHz / 64 = 250 kHz → 4 µs per askel; 10 ms / 4 µs = 2500 askelta → OCR1A = 2499.

| Rekisteri | Arvo | Bitit ja syy |
| --- | --- | --- |
| TCCR1A | 0x00 | COM1A/B = 00: OC1A/OC1B-pinnit (D9, D10) irti ajastimesta; WGM11:10 = 00 |
| TCCR1B | 0x0B | WGM13:12 = 01 → tila 4 (CTC, TOP = OCR1A); CS12:10 = 011 → esijakaja 64 |
| OCR1A | 2499 | vertailuarvo: 2500 askelta × 4 µs = 10 ms |
| TCNT1 | 0 | laskuri nollataan ennen käynnistystä |
| TIFR1 | OCF1A = 1 | vanha vertailulippu nollataan kirjoittamalla 1 ennen keskeytyksen sallimista |
| TIMSK1 | 0x02 | OCIE1A = 1: vertailukeskeytys A päälle |
| SREG | I = 1 | globaali keskeytyslupa (Arduinon `init()` asettaa, `timer1_init()` palauttaa) |

Keskeytysrutiini on käännettynä 29 käskyä, noin 62 kellojaksoa vasteaikoineen, eli noin 4 µs joka 10 ms (0,04 % suoritinajasta). LED-nauhan päivitys estää keskeytykset noin 1,2 ms:ksi. Tikki ei silti katoa: vertailulippu jää odottamaan, ja keskeytysrutiini ajetaan heti päivityksen jälkeen. Pääsilmukka lukee laskuria eikä pelkkää lippua, joten myöhästynytkään tikki ei katoa.

Timer1:n uudelleenkäyttö tarkoittaa, että `analogWrite` D9:ssä ja D10:ssä ei toimi. Arduinon Timer0 jää toistaiseksi käyntiin, koska Wire-kirjaston aikakatkaisu käyttää sitä.

## Validointi

Jokaisella firmwaren muutoksella on laudalla tehtävä testi ja mittari, joka näyttää muutoksen hyödyn. Ne on kuvattu tiedostossa [VALIDOINTI.md](VALIDOINTI.md), ja skriptit ovat kansiossa `validointi/`. PR yhdistetään päähaaraan vasta laudalla tehdyn testin jälkeen.

Pinni D8 on varattu tikkimerkille: se vaihtaa tilaansa jokaisessa Timer1-keskeytyksessä (50 Hz).

## Logiikan testi

Lampun logiikkaa (`lamp_logic.h`) voi testata PC:llä ilman Arduinoa simuloidussa huoneessa, jossa anturin lukema on taustavalo plus LEDin vaikutus:

```
cd firmware/luxlamppu/test
gcc -std=c99 -Wall -Wextra -I.. test_lamp_logic.c -lm -o test && ./test
```

Testi tarkistaa, että säätö asettuu tavoitteeseen eri lampun vahvistuksilla värähtelemättä, palaa saavuttamattomasta tavoitteesta (anti-windup), sammuttaa lampun kirkkaassa huoneessa, ja että pitoaika ja animaatiot toimivat oikeaan aikaan.

Dashboard tallentaa kaiken vastaanotetun datan tiedostoon `mittaus_<päivämäärä>.csv` (demossa `demo_<päivämäärä>.csv`).

**Aikaväli:** kaavioiden aikavälin voi valita painikkeilla 2 min, 15 min, 1 h, 3 h tai 12 h. Tunnin ja sitä pidemmillä aikaväleillä kaaviot näyttävät 10 sekunnin jaksojen keskiarvot, ja tehokaavion vaalea alue näyttää jakson pienimmän ja suurimman tehon. Tehokaavion kulmassa on näkyvän jakson keskiteho, suurin teho ja energia. Dashboard lataa käynnistyessään saman kansion `mittaus_*.csv`-tiedostoista viimeisten 12 tunnin mittaukset, joten historia säilyy uudelleenkäynnistyksen yli (`--ei-historiaa` ohittaa latauksen). Katkot mittauksissa näkyvät katkoina käyrissä.

## Viestimuoto

Sarjaportin nopeus on 9600 baudia (8N1).

**Arduino → PC**, viisi kertaa sekunnissa: `pir,valo,kirkkaus,tavoite,tila,jannite_mV,virta_mA,teho_mW`, esimerkiksi `1,150,42,150,1,4987,412,2050`.

- `pir`: 1 kun liikettä havaitaan, muuten 0
- `valo`: suodatettu valoanturin ADC-lukema 0–1023
- `kirkkaus`: LED-nauhan keskimääräinen kirkkaus prosentteina (animaation aikana kaikkien LEDien keskiarvo)
- `tavoite`: säädön tavoite valoanturin yksiköissä
- `tila`: 0 = pois, 1 = säätö, 2 = sammumisanimaatio, 3 = heräämisanimaatio
- `jannite_mV`, `virta_mA`, `teho_mW`: INA219:n mittaama nauhan syöttö; -1, jos INA219 ei vastaa

Dashboard ymmärtää myös vanhat muodot `pir,valo` ja `pir,valo,kirkkaus,jannite_mV,virta_mA,teho_mW`.

**PC → Arduino:**

- `T,<0-1023>`: tavoite valoanturin yksiköissä
- `H,<1-3600>`: pitoaika sekunteina
- `K,<1-50>`: säätimen kerroin (promillea kirkkautta per ADC-yksikkö per 100 ms:n askel)
- `D,<0|1>`: ajoituksen validointirivi pois/päälle. Päällä ollessa Arduino lähettää kerran sekunnissa rivin `#diag,tikit,ohitetut,suurin_viive_us,kuorma_promillea`, jonka dashboard ohittaa (ks. [VALIDOINTI.md](VALIDOINTI.md))
