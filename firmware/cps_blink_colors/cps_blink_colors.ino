/*
 * LED-nauhan vilkkutesti: nauha vilkkuu (0,5 s päällä, 0,5 s pois) ja väri vaihtuu
 * jokaisella välähdyksellä. Nopea tapa nähdä, että lataus ja nauha toimivat.
 *
 * Kytkennät (Arduino Uno R3): nauhan DIN -> 330 ohm -> D6, GND yhteen.
 *
 * Kirkkaus rajataan virtabudjettiin CURRENT_LIMIT_MA kuten cps_test_led_strip-sketsissä.
 */
#ifndef F_CPU
#define F_CPU 16000000UL
#endif
#include <avr/io.h>
#include <util/delay.h>

#define NUM_LEDS          39     // nauhan LEDien määrä
#define CURRENT_LIMIT_MA  900    // 900 = USB-breakout ja laturi (vähintään 1 A); 350 = virta Arduinon 5V-pinnistä
#define ON_MS             500    // välähdyksen kesto
#define OFF_MS            500    // tauko välähdysten välissä

static uint8_t leds[NUM_LEDS * 3];   // jokaiselle LEDille G, R, B

static const uint8_t colors[][3] = { // R, G, B
    {255,   0,   0},   // punainen
    {  0, 255,   0},   // vihreä
    {  0,   0, 255},   // sininen
    {255, 140,   0},   // oranssi
    {  0, 255, 255},   // syaani
    {255,   0, 255},   // magenta
    {255, 255, 255},   // valkoinen
};
#define NUM_COLORS (sizeof colors / sizeof colors[0])

/* WS2812-protokolla: data D6 (PD6), 16 MHz */
static void ws_show(void) {
    uint8_t hi = PORTD | (1 << PD6);
    uint8_t lo = PORTD & ~(1 << PD6);
    for (uint16_t i = 0; i < sizeof leds; i++) {
        uint8_t b = leds[i], n = 8;
        asm volatile(
            "1: out %[port], %[hi]  \n\t"
            "   nop \n\t nop \n\t nop \n\t nop \n\t"
            "   sbrs %[b], 7        \n\t"
            "   out %[port], %[lo]  \n\t"
            "   nop \n\t nop \n\t nop \n\t nop \n\t nop \n\t nop \n\t"
            "   out %[port], %[lo]  \n\t"
            "   nop \n\t nop \n\t nop \n\t nop \n\t"
            "   lsl %[b]            \n\t"
            "   dec %[n]            \n\t"
            "   brne 1b             \n\t"
            : [b] "+r" (b), [n] "+r" (n)
            : [port] "I" (_SFR_IO_ADDR(PORTD)), [hi] "r" (hi), [lo] "r" (lo));
    }
    _delay_us(300);
}

/* Suurin kirkkaus (0–255), jolla värin virta pysyy budjetissa (20 mA/kanava, 1 mA lepo/LED). */
static uint8_t max_level_for(const uint8_t *c) {
    uint32_t idle_ma = NUM_LEDS;
    uint32_t full_ma = (uint32_t)NUM_LEDS * (20UL * (c[0] + c[1] + c[2]) / 255) + idle_ma;
    if (full_ma <= CURRENT_LIMIT_MA) return 255;
    if (CURRENT_LIMIT_MA <= idle_ma) return 0;
    return (uint8_t)((CURRENT_LIMIT_MA - idle_ma) * 255UL / (full_ma - idle_ma));
}

static void show_color(const uint8_t *c, uint8_t level) {
    for (uint8_t i = 0; i < NUM_LEDS; i++) {
        leds[3 * i]     = (uint16_t)c[1] * level / 255;   // G
        leds[3 * i + 1] = (uint16_t)c[0] * level / 255;   // R
        leds[3 * i + 2] = (uint16_t)c[2] * level / 255;   // B
    }
    ws_show();
}

int main(void) {
    DDRD |= (1 << PD6);
    PORTD &= ~(1 << PD6);
    _delay_ms(100);

    uint8_t ci = 0;
    for (;;) {
        const uint8_t *c = colors[ci];
        show_color(c, max_level_for(c));   // päälle
        _delay_ms(ON_MS);
        show_color(c, 0);                  // pois
        _delay_ms(OFF_MS);
        ci = (ci + 1) % NUM_COLORS;
    }
}
