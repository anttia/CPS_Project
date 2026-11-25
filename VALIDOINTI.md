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
| 1 | Lataa PR:n firmware ja käynnistä dashboard. Liiku PIR:n edessä ja pysy sitten paikallasi. | Dashboardin "Motion"-kenttä ja liikealue kaaviossa seuraavat PIR:iä kuten ennenkin. |
| 2 | Aseta pitoajaksi 20 s (`python dashboard/dashboard.py --hold-time 20`). Poistu, odota että nauha sammuu, ja palaa. | Herääminen alkaa, kun PIR havaitsee liikkeen. Sama myös kesken sammumisanimaation: valo kääntyy kirkastumaan ilman välähdystä. |
| 3 | Kytke skooppi: kanava 1 PIR:n SIG (D2), kanava 2 D7, maa GND:hen. Laukaisu kanavan 1 nousevaan reunaan. Toista liike 20 kertaa ja kirjaa suurin viive (pysyvä näyttö tai kursorit). | D7 nousee jokaisella kerralla, ja viive on enintään yksi tikki (10 ms) plus konseptin 1 suurin viive tikistä työhön, eli käytännössä enintään noin 12 ms. |
| 4 | Aja ajoituksen validointi kuten konseptissa 1 (`validoi_ajoitus.py --kesto 600 --pitoaika 20`). | Kriteerit täyttyvät edelleen: uusi keskeytys ei aiheuta ohitettuja tikkejä. |

### 2) Hyöty: ennen/jälkeen

| Mittari | Ennen (pollaus) | Jälkeen (keskeytys) | Miksi sillä on väliä laitteelle |
| --- | --- | --- | --- |
| Viive PIR:n reunasta liikkeen käsittelyyn | 0–100 ms (säätöaskeleen jakso, laskettu) | *mitataan* skoopilla, odotus enintään 10 ms | Lamppu reagoi liikkeeseen kymmenesosassa ajasta. |
| Herätelähde unitilalle | ei ole | pin change -keskeytys | Power-down-unesta herättää vain keskeytys. Pollaus ei toimi, kun suoritin nukkuu. |

Ennen-viive on laskettu, koska vanhassa firmwaressa ei ole merkkipinniä: pollaus luki PIR:n kerran 100 ms:ssa, joten viive on reunan ajankohdasta riippuen 0–100 ms.

**Rehellinen arvio:** käyttäjä ei huomaa eroa, koska PIR itse reagoi liikkeeseen noin sekunnin viiveellä. Muutoksen hyöty on siinä, että liike käsitellään tapahtumana eikä kyselemällä, viive on mitattavissa, ja keskeytys on herätelähde, jota seuraava konsepti (unitilat) tarvitsee.

---

## Konsepti 3: viestiprotokolla, tarkistussumma ja rivin numero

**Mitä muuttui:** jokaisen telemetriarivin loppuun tulee rivin numero (0–255) ja tarkistussumma (`*HH`, tavujen XOR). Dashboard hylkää rivit, joiden summa ei täsmää, ja laskee numeroiden aukoista kadonneet rivit. Ennen virheellinen rivi tunnistettiin vain kenttien määrästä ja arvoalueista, eikä kadonneita rivejä huomattu lainkaan.

**Mittari:** dashboardin System-välilehden rivi "Serial link": tarkistetut, hylätyt ja kadonneet rivit tämän yhteyden aikana.

### Ilman laitetta (jo tehty)

| Testi | Tulos |
| --- | --- |
| `test_protocol.c`: jokainen yksittäinen bittivirhe esimerkkirivissä | 217/217 havaittu |
| `test_protocol.c`: kaksi samaa bittiä kahdessa eri tavussa | ei havaita (XOR:n tunnettu rajoitus) |
| `dashboard/test_protokolla.py`: ehjät, rikkoutuneet, kadonneet (myös 255 → 0 -kierto), uudelleenkäynnistys, vanha muoto | kaikki oikein |

### 1) Toimii: laudalla tehtävät askeleet

| # | Askel | Hyväksytty, kun |
| --- | --- | --- |
| 1 | Lataa firmware ja käynnistä dashboard. | Lamp-välilehti toimii kuten ennen. System-välilehdellä "Serial link" kasvaa noin 5 riviä sekunnissa. |
| 2 | Anna dashboardin olla käynnissä vähintään 1 h tavallisessa käytössä (liiku välillä PIR:n edessä). Ota kuvakaappaus System-välilehdestä. | Hylättyjä 0 ja kadonneita 0, tai poikkeamat selitetään (esim. USB-kaapelin irrotus). |
| 3 | Paina Unon reset-nappia dashboardin ollessa käynnissä. | Kadonneisiin tulee korkeintaan reset-hetken rivit (noin 2 s eli noin 10 riviä), ei satoja: numerointi alkaa alusta V-rivin kohdalla. |

### 2) Hyöty: ennen/jälkeen

| Mittari | Ennen | Jälkeen |
| --- | --- | --- |
| Rikkoutuneen rivin havaitseminen | vain kenttämäärä ja arvoalueet: yksittäinen muuttunut numero meni läpi | jokainen yksittäinen bittivirhe havaitaan |
| Kadonneet rivit | ei havaita | lasketaan numeroiden aukoista ja näytetään |
| Lopputyön ohjeen vaatimus (4.5 ja 7.4: virheentunnistus, kadonneet viestit) | ei täyttynyt | täyttyy, luvut raporttiin askeleesta 2 |

**Rehellinen arvio:** USB-sarjayhteydellä virheitä tulee harvoin, joten odotettu tulos on 0 hylättyä. Arvo on siinä, että se on nyt mitattu eikä oletettu, ja että dashboard ei näytä rikkoutunutta mittausta oikeana.

