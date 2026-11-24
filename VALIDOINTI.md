# Validointi

Jokaisella firmwaren muutoksella on kaksi testiä:

1. **Toimiiko se?** Askeleet, jotka tehdään laudalla, ja hyväksymiskriteerit numeroina.
2. **Onko siitä hyötyä?** Mittari, joka näyttää muutoksen vaikutuksen. Mittari valitaan niin, että tulos voidaan kirjoittaa raporttiin.

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

### Laudalla tehtävät askeleet

| # | Askel | Hyväksytty, kun |
| --- | --- | --- |
| 1 | Lataa firmware. Käynnistä dashboard ja seuraa sitä 2 min. | Data päivittyy tasaisesti (5 riviä/s), eikä punaista ilmoitusta tule. Tavoitteen liukusäädin muuttaa LEDien kirkkautta. |
| 2 | Sulje dashboard. Aja `python validointi/validoi_ajoitus.py --kesto 600 --pitoaika 20`. Pysy ensin poissa PIR:n näkyvistä, kunnes nauha on sammunut, ja palaa sitten. Toista pari kertaa. | Skripti tulostaa `KAIKKI KRITEERIT TÄYTTYVÄT`: tikin taajuus 100 Hz ± 0,5 %, telemetria 5 riviä/s ± 2 %, 0 ohitettua tikkiä ja suurin viive alle 10 ms. |
| 3 | Mittaa sekuntikellolla sammumisanimaation kesto (pitoaika on edelleen 20 s). | 30 s ± 1 s |
| 4 | Mittaa D8:n taajuus yleismittarin Hz-asteikolla tai oskilloskoopilla (maa GND:hen). | 50,0 Hz ± 0,5 %. Skoopilla jakso on 20 ms ja pulssisuhde 50 %. |
| 5 | Paina Unon reset-nappia, jolloin pitoaika palaa 180 s:iin, tai käynnistä dashboard, joka lähettää oman pitoaikansa. | – |

Skripti tallentaa raakalokin tiedostoon `validointi_ajoitus_<pvm>.log`. Lokin voi analysoida uudelleen komennolla `--tiedosto`.

### Hyöty: mitä mittaus näyttää

| Mittari | Ennen | Jälkeen | Merkitys |
| --- | --- | --- | --- |
| Ajoituksen lähde | `millis()`, Timer0-ylivuoto 1,024 ms:n välein, ohjelmallinen korjaus | Timer1 CTC, tarkalleen 2500 × 4 µs | Jakso tulee suoraan laitteistosta ja on laskettavissa. Tarkkuus riippuu vain resonaattorista (laudalla mitattu ppm). |
| Keskeytysrutiinin hinta | – | 29 käskyä, noin 62 kellojaksoa ≈ 4 µs / 10 ms = 0,04 % | Rutiini on lyhyt, joten työ tehdään pääsilmukassa. |
| Ohitetut tikit animaation aikana (simavr) | 153 kpl / 10 s, kuorma enintään 81 % | 0 kpl, kuorma enintään 36 % | Mittari paljasti, että animaatiokehyksen laskenta vei yli 10 ms. Korjauksena koko nauha lasketaan kerralla (`lamp_render_pm`), ja PC-testi todentaa, että tulos on bitilleen sama. |
| Suoritinkuorma säätötilassa (simavr) | ei mitattavissa | noin 4 % | Noin 96 % ajasta pääsilmukka vain odottaa. Seuraava konsepti (unitilat) korvaa tämän odotuksen unella. Tämä luku on unitilan säästön lähtötaso. |

Simulaattorin luvuissa valoanturi lukee 0 eikä INA219:ää ole, joten laudalla luvut ovat hieman suurempia (I2C-luku 5 kertaa sekunnissa).

### Simulaattorissa (ilman laitetta)

```
# käännä niin, että validointirivit ovat päällä heti käynnistyksestä
arduino-cli compile --fqbn arduino:avr:uno --build-property "compiler.cpp.extra_flags=-DDIAG_DEFAULT=1" --output-dir out firmware/luxlamppu
simavr -m atmega328p -f 16000000 out/luxlamppu.ino.elf > sim.out   # pysäytä noin 75 s:n jälkeen (Ctrl+C)
python validointi/validoi_ajoitus.py --simavr < sim.out
```

Simulaattori ei tunne PC:n kelloa, joten tikin taajuutta ei tarkisteta. Sen sijaan tarkistetaan, että validointirivi tulee tasan 100 tikin välein.
