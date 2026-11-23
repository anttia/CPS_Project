/*
 * Luxlamppu: anturit, INA219-virtamittaus ja LED-nauhan kirkkaus USB-dashboardin kautta.
 *
 * Kytkennät (Arduino Uno R3):
 *   Grove PIR    SIG -> D2           Grove Light  SIG -> A0
 *   LED-nauha    DIN -> 330 ohm -> D6
 *   INA219       VCC -> 5V, GND -> GND, SDA -> A4, SCL -> A5
 *                VIN+ <- USB-breakoutin VBUS, VIN- -> LED-nauhan +5V
 *   Kaikkien osien GND yhteen (Arduino, USB-breakout, nauha, INA219).
 *
 * Lähettää viisi kertaa sekunnissa rivin
 *     pir,valo,kirkkaus,jannite_mV,virta_mA,teho_mW
 * esim. "1,182,40,4987,412,2050". Jos INA219 ei vastaa, sen kentät ovat -1.
 *
 * Vastaanottaa dashboardilta komennon
 *     B,<0-100>        LED-kirkkaus prosentteina (valkoinen)
 * Lähetetty "kirkkaus" on se arvo, jolla nauha oikeasti palaa, joten dashboard
 * näkee, onko komento mennyt perille.
 *
 * "100 %" on suurin kirkkaus, jonka virtabudjetti CURRENT_LIMIT_MA sallii valkoiselle.
 *
 * Valoanturi (ADC) ja LED-nauha (WS2812-protokolla) käyttävät rekistereitä suoraan.
 * INA219 luetaan toistaiseksi Adafruit INA219 -kirjastolla; oma I2C-ajuri tulee
 * vaiheessa 2. Asenna Arduino IDE:n Library Managerista: Adafruit INA219.
 */
#include <Wire.h>
#include <Adafruit_INA219.h>

#define NUM_LEDS          39     // nauhan LEDien määrä
#define CURRENT_LIMIT_MA  900    // virtabudjetti nauhalle: USB-breakout ja vähintään 1 A:n laturi
#define SEND_INTERVAL_MS  200    // mittausrivi viisi kertaa sekunnissa

static uint8_t leds[NUM_LEDS * 3];   // jokaiselle LEDille G, R, B
static uint8_t max_level;            // valkoisen taso (0–255), joka vastaa 100 %
static uint8_t brightness_pct = 0;   // nauhan nykyinen kirkkaus prosentteina

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

static void set_brightness(uint8_t pct) {
    if (pct > 100) pct = 100;
    uint8_t level = (uint16_t)pct * max_level / 100;
    for (uint16_t i = 0; i < sizeof leds; i++) leds[i] = level;   // valkoinen: G = R = B
    ws_show();
    brightness_pct = pct;
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

/* ---------- Komennot dashboardilta ---------- */
static void handle_command(const char *line) {
    if (line[0] == 'B' && line[1] == ',') {
        int pct = atoi(line + 2);
        if (pct >= 0 && pct <= 100) set_brightness((uint8_t)pct);
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
    set_brightness(0);
}

void loop() {
    static unsigned long last_send = 0;
    read_serial();

    if (millis() - last_send < SEND_INTERVAL_MS) return;
    last_send = millis();

    uint8_t pir = (PIND & (1 << PD2)) ? 1 : 0;
    uint16_t sum = 0;
    for (uint8_t i = 0; i < 4; i++) sum += adc_read();   // neljän näytteen keskiarvo
    uint16_t light = sum / 4;

    long mv = -1, ma = -1, mw = -1;
    if (ina_ok) {
        mv = lround(ina219.getBusVoltage_V() * 1000);
        ma = lround(ina219.getCurrent_mA());
        mw = lround(ina219.getPower_mW());
    }

    Serial.print(pir);            Serial.print(',');
    Serial.print(light);          Serial.print(',');
    Serial.print(brightness_pct); Serial.print(',');
    Serial.print(mv);             Serial.print(',');
    Serial.print(ma);             Serial.print(',');
    Serial.println(mw);
}
