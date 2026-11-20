# CPS-projekti: luxanturin ohjaama lamppu

Cyber-Physical Systems -kurssin lopputyö. Lamppu (osoitettava LED-nauha) pitää työpisteen valaistuksen tavoitearvossa valoanturin avulla ja sammuu, kun liiketunnistin ei havaitse ketään. Mittausdata kulkee USB:n kautta PC:n dashboardille.

## Rakenne

| Kansio | Sisältö |
| --- | --- |
| `firmware/valo_liike_usb/` | Arduino Uno R3: lukee PIR- ja valoanturin ja lähettää arvot USB:n kautta |
| `dashboard/` | PC-dashboard (Python): viimeisin havaittu liike, nykyinen valoisuus ja valoisuuskaavio |

## Kytkennät (Arduino Uno R3)

| Anturi | Johto | Unon pinni |
| --- | --- | --- |
| Grove PIR Motion Sensor v1.2 | keltainen (SIG) | D2 |
| Grove Light Sensor v1.3 | keltainen (SIG) | A0 |
| molemmat | punainen (VCC) / musta (GND) | 5V / GND |

Grove-johtojen valkoinen johdin (NC) jää kytkemättä. PIR:n jumpperi Re_trig-asentoon.

## Käyttö

1. **Firmware:** avaa `firmware/valo_liike_usb/valo_liike_usb.ino` Arduino IDE:ssä, valitse kortiksi Arduino Uno ja oikea portti, ja paina Upload. Koodi käyttää rekistereitä suoraan ilman Arduinon funktioita.
2. **Dashboard:**
   ```
   cd dashboard
   pip install -r requirements.txt
   python dashboard.py              # etsii Arduinon portin automaattisesti
   python dashboard.py --port COM3  # tai portti käsin
   python dashboard.py --demo       # testidata ilman Arduinoa
   ```
   Sulje Arduino IDE:n Serial Monitor ennen dashboardin käynnistystä: sama portti ei voi olla auki kahdessa ohjelmassa. Jos portti on varattu, puuttuu tai data lakkaa tulemasta, dashboard näyttää punaisen ilmoituksen ja yhdistää automaattisesti uudelleen, kun portti vapautuu.

Dashboard tallentaa kaiken vastaanotetun datan tiedostoon `mittaus_<päivämäärä>.csv`.

Dashboardin alareunan liukusäätimellä asetetaan tavoitekirkkaus anturin yksiköissä, ja se näkyy kaaviossa vihreänä katkoviivana. Painike "Käytä nykyistä lukemaa" asettaa tavoitteeksi anturin sen hetkisen lukeman. Tavoitetta ei vielä lähetetä Arduinolle; se otetaan käyttöön, kun lampun säätö lisätään firmwareen.

## Viestimuoto

Arduino lähettää viisi kertaa sekunnissa rivin `pir,valo`, esimerkiksi `1,523`:

- `pir`: 1 kun liikettä havaitaan, muuten 0
- `valo`: valoanturin ADC-lukema 0–1023 (neljän näytteen keskiarvo)

Sarjaportin nopeus on 9600 baudia (8N1).
