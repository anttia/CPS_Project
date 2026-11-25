/*
 * Luxlamppu: valoanturi ohjaa LED-nauhaa suljetulla säädöllä, PIR sammuttaa ja
 * sytyttää lampun animaatioilla, ja INA219 mittaa nauhan tehon.
 *
 * Kytkennät (Arduino Uno R3):
 *   Grove PIR    SIG -> D2           Grove Light  SIG -> A0
 *   LED-nauha    DIN -> 330 ohm -> D6
 *   INA219       VCC -> 5V, GND -> GND, SDA -> A4, SCL -> A5
 *                VIN+ <- USB-breakoutin VBUS, VIN- -> LED-nauhan +5V
 *   Kaikkien osien GND yhteen (Arduino, USB-breakout, nauha, INA219).
 *
 * Toiminta (lamp_logic.h):
 *   - säätö pitää valoanturin lukeman tavoitteessa: LED-nauha on toimilaite
 *   - pitoajan viimeiset 15 s (komento W) nauha on meripihkan värinen merkkinä siitä,
 *     että lamppu sammuu pian; liike palauttaa valkoisen heti
 *   - kun liikettä ei ole havaittu pitoaikaan (oletus 3 min), nauha sammuu 30 s:n
 *     animaatiolla: LEDit himmenevät reunoilta kohti keskikohtaa
 *   - kun liike havaitaan uudelleen, valo leviää keskeltä reunoille ja kirkastuu 30 s:ssa,
 *     minkä jälkeen säätö jatkuu
 *
 * PIR luetaan pin change -keskeytyksellä (D2 = PCINT18): liikkeen alku käsitellään
 * seuraavalla 10 ms:n tikillä eikä vasta 100 ms:n säätöaskeleella. D7 nousee ylös, kun
 * pääsilmukka on käsitellyt liikkeen, ja laskee, kun PIR:n signaali laskee (validointia varten).
 *
 * Lähettää viisi kertaa sekunnissa rivin
 *     pir,valo,kirkkaus,tavoite,tila,jannite_mV,virta_mA,teho_mW,numero*HH
 * missä tila on 0 = pois, 1 = säätö, 2 = sammumisanimaatio, 3 = heräämisanimaatio.
 * Jos INA219 ei vastaa, sen kentät ovat -1. numero (0–255) kasvaa joka rivillä, ja HH on
 * tarkistussumma (ks. protocol.h): dashboard hylkää rikkoutuneet rivit ja laskee kadonneet.
 * Käynnistyessään se lähettää kerran rivin  V,<käännöspäivä> <käännösaika>,
 * josta dashboard näkee, mikä firmwaren versio kortilla on.
 *
 * Vastaanottaa dashboardilta komennot:
 *     T,<0-1023>    tavoite valoanturin yksiköissä
 *     H,<1-3600>    pitoaika sekunteina
 *     K,<1-50>      säätimen kerroin (promillea kirkkautta per ADC-yksikkö per askel)
 *     W,<0-600>     varoitusvärin kesto sekunteina ennen sammumista (0 = ei varoitusta)
 *
 *     D,<0|1>       ajoituksen validointirivi pois/päälle (ks. alla)
 *
 * Validointi (ks. VALIDOINTI.md): komennolla D,1 laite lähettää kerran sekunnissa rivin
 *     #diag,tikit,myöhästyneet,suurin_viive_us,kuorma_promillea
 * Dashboard ohittaa #-rivit; dashboard/validoi_ajoitus.py analysoi ne. Lisäksi D8 (PB0)
 * vaihtaa tilaa joka tikillä, joten siinä näkyy 50 Hz:n kanttiaalto skoopilla tai
 * yleismittarin taajuusasteikolla.
 *
 * "100 %" on suurin kirkkaus, jonka virtabudjetti CURRENT_LIMIT_MA sallii valkoiselle.
 *
 * Ajoitus tulee omasta Timer1-ajastimesta (CTC-tila, 10 ms:n tikki), ei Arduinon
 * millis()-funktiosta. Keskeytysrutiini vain kasvattaa tikkilaskuria; pääsilmukka
 * lukee laskurin atomisesti ja ajaa säädön, animaation ja telemetrian sen mukaan.
 *
 * Valoanturi (ADC) ja LED-nauha (WS2812-protokolla) käyttävät rekistereitä suoraan.
 * INA219 luetaan toistaiseksi Adafruit INA219 -kirjastolla; oma I2C-ajuri tulee
 * vaiheessa 2. Asenna Arduino IDE:n Library Managerista: Adafruit INA219.
 */
#include <avr/interrupt.h>
#include <util/atomic.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include "lamp_logic.h"
#include "protocol.h"

#define NUM_LEDS          39     // nauhan LEDien määrä
#define CURRENT_LIMIT_MA  900    // virtabudjetti nauhalle: USB-breakout ja vähintään 1 A:n laturi
#define TICK_MS           10     // Timer1:n tikki: ohjelman ajan perusyksikkö
#define CONTROL_MS        100    // säätöväli
#define FRAME_MS          20     // animaation päivitysväli
#define SEND_INTERVAL_MS  200    // mittausrivi viisi kertaa sekunnissa

// Tehtävien välit tikkeinä. Jokaisen välin pitää olla tikin monikerta.
#define CONTROL_TICKS     (CONTROL_MS / TICK_MS)
#define FRAME_TICKS       (FRAME_MS / TICK_MS)
#define SEND_TICKS        (SEND_INTERVAL_MS / TICK_MS)
#if (CONTROL_MS % TICK_MS) || (FRAME_MS % TICK_MS) || (SEND_INTERVAL_MS % TICK_MS)
#error "Tehtävien välien pitää olla TICK_MS:n monikertoja"
#endif

// Timer1: 16 MHz / esijakaja 64 = 250 kHz, eli yksi askel 4 µs.
// 10 ms = 2500 askelta, ja laskuri nollautuu vertailuarvon jälkeen: OCR1A = 2500 - 1.
#define TIMER1_PRESCALER  64UL
#define TIMER1_TOP        (F_CPU / TIMER1_PRESCALER * TICK_MS / 1000UL - 1)
#if TIMER1_TOP > 65535
#error "TIMER1_TOP ei mahdu 16-bittiseen OCR1A-rekisteriin"
#endif
#define STEPS_PER_TICK    (TIMER1_TOP + 1)   // 2500 Timer1-askelta (4 µs) per tikki

#define TICK_MARK_PIN     PB0    // D8: ISR vaihtaa tilaa joka tikillä (validointia varten)
#define MOTION_MARK_PIN   PD7    // D7: ylhäällä, kun pääsilmukka on käsitellyt liikkeen (validointia varten)
#define DIAG_TICKS        100    // validointirivi kerran sekunnissa

static uint8_t leds[NUM_LEDS * 3];   // jokaiselle LEDille G, R, B
static uint8_t max_level;            // valkoisen taso (0–255), joka vastaa 100 %
static Lamp lamp;
static uint8_t last_pir = 0;         // PIR:n taso pääsilmukan viimeisimmällä tikillä

Adafruit_INA219 ina219;              // osoite 0x40
static bool ina_ok = false;

/* ---------- Ajastin: Timer1 CTC, 10 ms:n tikki ---------- */

/* Tikkilaskuri on jaettu keskeytysrutiinin ja pääsilmukan kesken:
   - volatile: kääntäjä lukee arvon muistista joka kerta eikä pidä sitä rekisterissä,
     koska keskeytys voi muuttaa sitä milloin tahansa.
   - volatile ei tee lukemisesta atomista: 8-bittinen AVR lukee 32-bittisen arvon
     neljänä tavuna, ja keskeytys voi osua lukujen väliin (revitty luku). Siksi
     pääsilmukka lukee laskurin vain ticks_now()-funktiolla keskeytykset estettyinä.
   - laskuri on parempi kuin pelkkä lippu: jos pääsilmukka myöhästyy (esim. LED-nauhan
     päivitys estää keskeytykset noin 1,2 ms), tikkejä ei katoa. */
static volatile uint32_t tick_count = 0;

/* Vertailukeskeytys 10 ms:n välein. Rutiini on tarkoituksella lyhyt: se vain kirjaa
   tikin, ja varsinainen työ tehdään pääsilmukassa. OCF1A-lippu nollautuu laitteistossa,
   kun keskeytysrutiini käynnistyy. */
ISR(TIMER1_COMPA_vect) {
    tick_count++;
    PINB = (1 << TICK_MARK_PIN);              // PINx:ään kirjoitettu 1 vaihtaa pinnin tilaa
}

/* Timer1 CTC-tilaan Lab 2:n järjestyksessä. Arduinon init() on asettanut Timer1:n
   PWM-tilaan (analogWrite D9/D10); tämä korvaa asetukset kokonaan, joten D9:n ja
   D10:n analogWrite ei ole käytössä. Arduinon Timer0 (millis) jää toistaiseksi
   päälle Wire-kirjaston aikakatkaisua varten, mutta ohjelma ei käytä sitä. */
static void timer1_init(void) {
    uint8_t sreg = SREG;
    cli();
    TCCR1B = 0;                               // 1. pysäytä ajastin: CS12:CS10 = 000
    TCCR1A = 0;                               // 2. OC1A/OC1B irti pinneistä, WGM11:10 = 00
    TCCR1B = (1 << WGM12);                    //    WGM13:12 = 01 -> tila 4, CTC, TOP = OCR1A
    TCNT1  = 0;                               // 3. laskuri tunnettuun arvoon
    OCR1A  = TIMER1_TOP;                      // 4. 2499 -> jakso 2500 × 4 µs = 10 ms
    TIFR1  = (1 << OCF1A);                    // 5. vanha lippu pois kirjoittamalla 1
    TIMSK1 = (1 << OCIE1A);                   // 6. vertailukeskeytys A päälle
    TCCR1B |= (1 << CS11) | (1 << CS10);      // 7. esijakaja 64 (CS12:CS10 = 011): käynnistyy
    SREG = sreg;                              // 8. globaali keskeytyslupa takaisin
}

/* Tikkien määrä käynnistyksestä. ATOMIC_BLOCK estää keskeytykset lukemisen ajaksi
   (cli) ja palauttaa SREG:n entiseen tilaan, joten luku ei voi revetä. */
static uint32_t ticks_now(void) {
    uint32_t t;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        t = tick_count;
    }
    return t;
}

/* Aika käynnistyksestä Timer1-askelina (4 µs): tikit × 2500 + TCNT1. Jos vertailu on
   juuri tapahtunut mutta keskeytysrutiini ei ole vielä ehtinyt (keskeytykset estetty),
   TCNT1 on jo nollautunut ja OCF1A-lippu on päällä: silloin tikkejä on yksi enemmän. */
static uint32_t steps_now(void) {
    uint32_t t;
    uint16_t c;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        c = TCNT1;
        t = tick_count;
        if ((TIFR1 & (1 << OCF1A)) && c < STEPS_PER_TICK / 2) t++;
    }
    return t * STEPS_PER_TICK + c;
}

/* ---------- PIR: pin change -keskeytys D2:ssa (PD2 = PCINT18) ---------- */

/* Pin change -keskeytys laukeaa pinnin molemmista reunoista, joten rutiini lukee pinnin
   tason ja kirjaa vain nousevan reunan eli liikkeen alun. Pääsilmukka käsittelee sen
   seuraavalla tikillä, enintään 10 ms myöhemmin; pollaus huomasi liikkeen vasta 100 ms:n
   säätöaskeleella. Pin change valittiin INT0:n sijaan, koska Power-down-unesta herättää
   vain pin change tai INT0:n tasolaukaisu, ja PIR:n lepotaso on matala. */
static volatile uint8_t pir_level = 0;   // PIR:n taso viimeisimmän muutoksen jälkeen
static volatile uint8_t pir_rose = 0;    // liike alkoi, eikä pääsilmukka ole vielä käsitellyt sitä

ISR(PCINT2_vect) {
    uint8_t level = (PIND & (1 << PD2)) ? 1 : 0;
    if (level && !pir_level) pir_rose = 1;
    pir_level = level;
}

/* Sama järjestys kuin Lab 2:ssa: pinni, lähtötaso, pinnimaski, vanha lippu pois, ryhmä päälle. */
static void pir_init(void) {
    uint8_t sreg = SREG;
    cli();
    DDRD &= ~(1 << PD2);                      // PD2 sisääntuloksi
    PORTD &= ~(1 << PD2);                     // ei sisäistä ylösvetoa: Grove PIR ajaa linjaa itse
    pir_level = (PIND & (1 << PD2)) ? 1 : 0;  // lähtötaso ennen keskeytyksen sallimista
    PCMSK2 |= (1 << PCINT18);                 // ryhmästä 2 (PORTD) vain D2
    PCIFR = (1 << PCIF2);                     // vanha lippu pois kirjoittamalla 1
    PCICR |= (1 << PCIE2);                    // pin change -ryhmä 2 päälle
    SREG = sreg;
}

/* ---------- Validointi: ajoituksen mittarit ---------- */
#ifndef DIAG_DEFAULT
#define DIAG_DEFAULT 0                // 1 = validointirivit päällä käynnistyksestä (simulaattori)
#endif
static bool     diag_on = DIAG_DEFAULT;
static uint32_t diag_late = 0;        // kertaa, kun pääsilmukka ohitti tikin kokonaan
static uint32_t diag_lat_max = 0;     // suurin viive tikistä työn alkuun (askelia, 4 µs)
static uint32_t diag_busy = 0;        // työhön kulunut aika jakson aikana (askelia)
static uint32_t diag_window = 0;      // jakson alkutikki

static void diag_reset(uint32_t t) {
    diag_lat_max = 0;
    diag_busy = 0;
    diag_window = t;
}

static void send_diag(uint32_t t) {
    uint32_t span = (t - diag_window) * STEPS_PER_TICK;
    Serial.print(F("#diag,"));
    Serial.print(t);                                Serial.print(',');
    Serial.print(diag_late);                        Serial.print(',');
    Serial.print(diag_lat_max * 4);                 Serial.print(',');   // askeleet -> µs
    Serial.println(span ? diag_busy * 1000UL / span : 0);
}

/* ---------- LED-nauha: WS2812-protokolla, data D6 (PD6), 16 MHz ---------- */
static void ws_show(void) {
    uint8_t hi = PORTD | (1 << PD6);
    uint8_t lo = PORTD & ~(1 << PD6);
    uint8_t sreg = SREG;
    cli();                                    // keskeytys kesken bitin rikkoisi ajoituksen.
                                              // Timer1:n lippu jää odottamaan, ja tikki
                                              // käsitellään heti tämän jälkeen (myöhässä ≤ 1,2 ms)
    for (uint16_t i = 0; i < sizeof leds; i++) {
        uint8_t b = leds[i], n = 8;
        asm volatile(
            "1: out %[port], %[hi]  \n\t"     // bitti alkaa: linja ylös
            "   nop \n\t nop \n\t nop \n\t nop \n\t"
            "   sbrs %[b], 7        \n\t"     // ykkösbitti: hyppää seuraavan yli
            "   out %[port], %[lo]  \n\t"     // nollabitti: alas 375 ns:n jälkeen
            "   nop \n\t nop \n\t nop \n\t nop \n\t nop \n\t nop \n\t"
            "   out %[port], %[lo]  \n\t"     // ykkösbitti: alas 812 ns:n jälkeen
            "   nop \n\t nop \n\t nop \n\t nop \n\t"
            "   lsl %[b]            \n\t"
            "   dec %[n]            \n\t"
            "   brne 1b             \n\t"
            : [b] "+r" (b), [n] "+r" (n)
            : [port] "I" (_SFR_IO_ADDR(PORTD)), [hi] "r" (hi), [lo] "r" (lo));
    }
    SREG = sreg;
    delayMicroseconds(300);                   // nollaus: linja alhaalla
}

/* Valkoisen suurin taso, jolla nauhan virta pysyy budjetissa. Arvio: jokainen
   värikanava ottaa täydellä kirkkaudella 20 mA ja jokainen LED 1 mA lepovirtaa. */
static uint8_t compute_max_level(void) {
    uint32_t idle_ma = NUM_LEDS;
    uint32_t full_ma = (uint32_t)NUM_LEDS * 60 + idle_ma;
    if (full_ma <= CURRENT_LIMIT_MA) return 255;
    if (CURRENT_LIMIT_MA <= idle_ma) return 0;
    return (uint8_t)((CURRENT_LIMIT_MA - idle_ma) * 255UL / (full_ma - idle_ma));
}

// Värit kanavittain (G, R, B) 1/256-osina: kerroin 256 = täysi. Kertolasku ja siirto >> 8
// on 8-bittiselle AVR:lle paljon nopeampi kuin jakolasku, joka hidastaisi animaatiota.
static const uint16_t WHITE_GRB[3] = {256, 256, 256};
static const uint16_t WARN_GRB[3]  = {115, 256, 0};    // meripihka: punainen täysi, vihreä 45 %, ei sinistä
static int16_t led_pm[NUM_LEDS];     // LEDien kirkkaudet promilleina (lamp_render_pm)
static int16_t display_pm = 0;       // nauhan keskimääräinen kirkkaus viimeisimmästä piirrosta

/* Piirtää nauhan lampun tilan mukaan. Palauttaa true, jos jokin LED muuttui.
   lamp_render_pm laskee koko nauhan kerralla; LED kerrallaan laskettuna animaatiokehys
   vei yli 10 ms ja Timer1:n tikkejä jäi käsittelemättä (ks. VALIDOINTI.md). */
static bool render(uint32_t now) {
    bool changed = false;
    display_pm = lamp_render_pm(&lamp, NUM_LEDS, now, led_pm);
    // Väri kanavittain: valkoinen, tai meripihka varoituksena
    // siitä, että lamppu sammuu pian. Meripihka ottaa vähemmän virtaa kuin valkoinen,
    // joten virtabudjetti pysyy voimassa.
    const uint16_t *color = lamp_warning(&lamp, now) ? WARN_GRB : WHITE_GRB;
    int16_t prev_pm = -1;
    uint8_t g = 0, r = 0, b = 0;
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        if (led_pm[i] != prev_pm) {           // sama arvo kuin edellisellä: ei laskentaa
            prev_pm = led_pm[i];
            uint16_t level = (uint32_t)prev_pm * max_level / 1000;   // 0–255
            g = (level * color[0]) >> 8;
            r = (level * color[1]) >> 8;
            b = (level * color[2]) >> 8;
        }
        uint8_t *led = &leds[3 * i];
        if (led[0] != g || led[1] != r || led[2] != b) changed = true;
        led[0] = g; led[1] = r; led[2] = b;
    }
    if (changed) ws_show();                   // nauha päivitetään vain muutoksista
    return changed;
}

/* ---------- Valoanturi: ADC0 (A0) ---------- */
static void adc_init(void) {
    ADMUX  = (1 << REFS0);                                             // referenssi AVcc, kanava ADC0
    ADCSRA = (1 << ADEN) | (1 << ADPS2) | (1 << ADPS1) | (1 << ADPS0); // jakaja 128 -> 125 kHz
    DIDR0  = (1 << ADC0D);                                             // A0:n digitaalipuskuri pois
}

static uint16_t adc_read(void) {
    ADCSRA |= (1 << ADSC);
    while (ADCSRA & (1 << ADSC)) ;
    return ADC;
}

/* 32 näytteen keskiarvo noin 3,3 ms:n ajalta: tasoittaa LEDien oman PWM-välkkeen. */
static uint16_t read_light(void) {
    uint32_t sum = 0;
    for (uint8_t i = 0; i < 32; i++) sum += adc_read();
    return sum / 32;
}

/* ---------- Komennot dashboardilta ---------- */
static void handle_command(const char *line) {
    if (line[1] != ',') return;
    long v = atol(line + 2);
    switch (line[0]) {
    case 'T': if (v >= 0 && v <= 1023) lamp.target = (uint16_t)v; break;
    case 'H': if (v >= 1 && v <= 3600) lamp.hold_ms = (uint32_t)v * 1000UL; break;
    case 'K': if (v >= 1 && v <= 50) lamp.ki = (uint8_t)v; break;
    case 'W': if (v >= 0 && v <= 600) lamp.warn_ms = (uint32_t)v * 1000UL; break;
    case 'D': diag_on = (v != 0); diag_reset(ticks_now()); break;
    }
}

static void read_serial(void) {
    static char buf[16];
    static uint8_t len = 0;
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (len > 0) {
                buf[len] = '\0';
                handle_command(buf);
                len = 0;
            }
        } else if (len < sizeof buf - 1) {
            buf[len++] = c;
        } else {
            len = 0;                          // liian pitkä rivi: hylätään
        }
    }
}

static void send_telemetry(void) {
    long mv = -1, ma = -1, mw = -1;
    if (ina_ok) {
        mv = lround(ina219.getBusVoltage_V() * 1000);
        ma = lround(ina219.getCurrent_mA());
        mw = lround(ina219.getPower_mW());
    }
    static uint8_t seq = 0;                   // rivin numero: aukoista näkee kadonneet rivit
    long fields[] = { last_pir, lamp_light(&lamp), (display_pm + 5) / 10, lamp.target,
                      lamp.state, mv, ma, mw, seq++ };
    char line[72];                            // 9 kenttää × enintään 7 merkkiä + "*HH"
    char *p = line;
    for (uint8_t i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        if (i) *p++ = ',';
        ltoa(fields[i], p, 10);
        p += strlen(p);
    }
    frame_close(line);                        // lisää "*HH"
    Serial.println(line);
}

void setup() {
    Serial.begin(9600);
    DDRD |= (1 << PD6);                       // nauhan data lähdöksi
    PORTD &= ~(1 << PD6);
    DDRD |= (1 << MOTION_MARK_PIN);           // liikemerkki D7 lähdöksi
    PORTD &= ~(1 << MOTION_MARK_PIN);
    DDRB |= (1 << TICK_MARK_PIN);             // tikkimerkki D8 lähdöksi
    adc_init();

    Wire.begin();
    Wire.setWireTimeout(25000, true);         // I2C ei jää jumiin, jos INA219 puuttuu
    ina_ok = ina219.begin();
    Wire.setWireTimeout(25000, true);

    max_level = compute_max_level();
    timer1_init();
    pir_init();
    lamp_init(&lamp, ticks_now() * TICK_MS);
    ws_show();                                // kaikki LEDit pois

    Serial.print(F("V,"));                    // käännösaika: dashboard näyttää, mikä versio on kortilla
    Serial.print(F(__DATE__));
    Serial.print(' ');
    Serial.println(F(__TIME__));
}

/* Pääsilmukka etenee Timer1:n tikeissä. Ajat lasketaan tikkien erotuksena etumerkittömällä
   aritmetiikalla. lamp_logic.h käyttää millisekunteja: now = tikit × 10 ms. Arvo
   kiertää ympäri noin 49,7 päivän välein kuten millis(), mutta erotukset pysyvät
   oikeina, koska lamp_logic.h vertaa vain aikojen erotuksia. */
void loop() {
    static uint32_t last_tick = 0, last_control = 0, last_frame = 0, last_send = 0;
    read_serial();

    uint32_t t = ticks_now();
    if (t == last_tick) return;               // ei uutta tikkiä: ei tehtävää
    uint32_t work_start = steps_now();
    if (t - last_tick > 1) diag_late++;       // välissä oli tikki, jota ei ehditty käsitellä
    last_tick = t;
    uint32_t now = t * TICK_MS;
    uint32_t latency = work_start - t * STEPS_PER_TICK;
    if (latency > diag_lat_max) diag_lat_max = latency;

    // PIR: keskeytys on kirjannut liikkeen alun, ja se käsitellään heti tällä tikillä.
    // Lippu luetaan ja nollataan samassa atomisessa lohkossa, jottei uusi reuna katoa välistä.
    uint8_t rose;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        rose = pir_rose;
        pir_rose = 0;
        last_pir = pir_level;
    }
    if (rose) {
        lamp_motion(&lamp, now);              // herätys tai pitoajan nollaus heti
        render(now);
        PORTD |= (1 << MOTION_MARK_PIN);
    }
    if (!last_pir) PORTD &= ~(1 << MOTION_MARK_PIN);

    if (t - last_control >= CONTROL_TICKS) {
        last_control = t;
        lamp_step(&lamp, now, last_pir, read_light());
        render(now);
    }
    if ((lamp.state == LAMP_GOODBYE || lamp.state == LAMP_WELCOME) && t - last_frame >= FRAME_TICKS) {
        last_frame = t;
        render(now);                          // animaatio etenee 50 kertaa sekunnissa
    }
    if (t - last_send >= SEND_TICKS) {
        last_send = t;
        send_telemetry();
    }
    if (diag_on) {
        diag_busy += steps_now() - work_start;
        if (t - diag_window >= DIAG_TICKS) {
            send_diag(t);
            diag_reset(t);
        }
    }
}
