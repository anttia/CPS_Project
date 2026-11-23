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
 *   - kun liikettä ei ole havaittu pitoaikaan (oletus 3 min), nauha sammuu animaatiolla
 *   - kun liike havaitaan uudelleen, nauha syttyy animaatiolla ja säätö jatkuu
 *
 * Lähettää viisi kertaa sekunnissa rivin
 *     pir,valo,kirkkaus,tavoite,tila,jannite_mV,virta_mA,teho_mW
 * missä tila on 0 = pois, 1 = säätö, 2 = sammumisanimaatio, 3 = heräämisanimaatio.
 * Jos INA219 ei vastaa, sen kentät ovat -1.
 *
 * Vastaanottaa dashboardilta komennot:
 *     T,<0-1023>    tavoite valoanturin yksiköissä
 *     H,<1-3600>    pitoaika sekunteina
 *     K,<1-50>      säätimen kerroin (promillea kirkkautta per ADC-yksikkö per askel)
 *
 * "100 %" on suurin kirkkaus, jonka virtabudjetti CURRENT_LIMIT_MA sallii valkoiselle.
 *
 * Valoanturi (ADC) ja LED-nauha (WS2812-protokolla) käyttävät rekistereitä suoraan.
 * INA219 luetaan toistaiseksi Adafruit INA219 -kirjastolla; oma I2C-ajuri tulee
 * vaiheessa 2. Asenna Arduino IDE:n Library Managerista: Adafruit INA219.
 */
#include <Wire.h>
#include <Adafruit_INA219.h>
#include "lamp_logic.h"

#define NUM_LEDS          39     // nauhan LEDien määrä
#define CURRENT_LIMIT_MA  900    // virtabudjetti nauhalle: USB-breakout ja vähintään 1 A:n laturi
#define CONTROL_MS        100    // säätöväli
#define FRAME_MS          20     // animaation päivitysväli
#define SEND_INTERVAL_MS  200    // mittausrivi viisi kertaa sekunnissa

static uint8_t leds[NUM_LEDS * 3];   // jokaiselle LEDille G, R, B
static uint8_t max_level;            // valkoisen taso (0–255), joka vastaa 100 %
static Lamp lamp;
static uint8_t last_pir = 0;

Adafruit_INA219 ina219;              // osoite 0x40
static bool ina_ok = false;

/* ---------- LED-nauha: WS2812-protokolla, data D6 (PD6), 16 MHz ---------- */
static void ws_show(void) {
    uint8_t hi = PORTD | (1 << PD6);
    uint8_t lo = PORTD & ~(1 << PD6);
    uint8_t sreg = SREG;
    cli();                                    // Arduinon ajastinkeskeytys rikkoisi ajoituksen
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

/* Piirtää nauhan lampun tilan mukaan. Palauttaa true, jos jokin LED muuttui. */
static bool render(uint32_t now) {
    bool changed = false;
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        uint8_t level = (uint32_t)lamp_led_pm(&lamp, i, NUM_LEDS, now) * max_level / 1000;
        if (leds[3 * i] != level) changed = true;
        leds[3 * i] = leds[3 * i + 1] = leds[3 * i + 2] = level;   // valkoinen: G = R = B
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
    Serial.print(last_pir);                         Serial.print(',');
    Serial.print(lamp_light(&lamp));                Serial.print(',');
    Serial.print((lamp_display_pm(&lamp) + 5) / 10); Serial.print(',');
    Serial.print(lamp.target);                      Serial.print(',');
    Serial.print(lamp.state);                       Serial.print(',');
    Serial.print(mv);                               Serial.print(',');
    Serial.print(ma);                               Serial.print(',');
    Serial.println(mw);
}

void setup() {
    Serial.begin(9600);
    DDRD |= (1 << PD6);                       // nauhan data lähdöksi
    PORTD &= ~(1 << PD6);
    DDRD &= ~(1 << PD2);                      // PIR sisääntuloksi
    adc_init();

    Wire.begin();
    Wire.setWireTimeout(25000, true);         // I2C ei jää jumiin, jos INA219 puuttuu
    ina_ok = ina219.begin();
    Wire.setWireTimeout(25000, true);

    max_level = compute_max_level();
    lamp_init(&lamp, millis());
    ws_show();                                // kaikki LEDit pois
}

void loop() {
    static uint32_t last_control = 0, last_frame = 0, last_send = 0;
    uint32_t now = millis();
    read_serial();

    if (now - last_control >= CONTROL_MS) {
        last_control = now;
        last_pir = (PIND & (1 << PD2)) ? 1 : 0;
        lamp_step(&lamp, now, last_pir, read_light());
        render(now);
    }
    if ((lamp.state == LAMP_GOODBYE || lamp.state == LAMP_WELCOME) && now - last_frame >= FRAME_MS) {
        last_frame = now;
        render(now);                          // animaatio etenee 50 kertaa sekunnissa
    }
    if (now - last_send >= SEND_INTERVAL_MS) {
        last_send = now;
        send_telemetry();
    }
}
