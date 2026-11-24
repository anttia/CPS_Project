# Ohjeet Claudelle

Tämä on Cyber-Physical Systems -kurssin lopputyö (luxlamppu). Repo on julkinen, ja opettaja arvioi sen commit-historian.

## Commitien tekijä

Ennen ensimmäistä committia jokaisessa istunnossa aseta tekijäksi Antti, ei Claude:

```
git config user.name "Antti"
git config user.email "987098+anttia@users.noreply.github.com"
```

Tarkista asetus komennolla `git config user.email` ennen committia. Sähköposti on GitHubin noreply-osoite, joka yhdistää commitit Antin tiliin (anttia) ilman, että oikea sähköposti näkyy julkisessa repossa. Älä käytä mitään muuta sähköpostiosoitetta.

Jätä commit-viestin loppuun `Co-Authored-By`-rivi Claudesta, jotta tekoälyn osuus näkyy.

## Haarat ja pull requestit

Jos `main` on suojattu tai push hylätään, tee muutokset omaan haaraan ja avaa pull request. Antti katselmoi ja yhdistää sen itse. Älä koskaan force pushaa `main`iin.

## Tarkistukset ennen committia

- Logiikan testi: `cd firmware/luxlamppu/test && gcc -std=c99 -Wall -Wextra -I.. test_lamp_logic.c -lm -o test && ./test`
- Firmware kääntyy vain Arduino IDE:ssä; tässä ympäristössä sen voi tarkistaa `avr-g++`:lla ja Arduino-otsikoiden tynkätiedostoilla.
- Dashboard: `python dashboard/dashboard.py --demo`

Kommentit, README ja commit-viestit kirjoitetaan suomeksi.
