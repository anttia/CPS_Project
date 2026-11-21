/*
 * LED-nauhan testi: kirkkaus vaihtelee 0–100 % ja väri vaihtuu jokaisen kierroksen jälkeen.
 *
 * Kytkennät (Arduino Uno R3 tai ATmega328PB Xplained Mini):
 *   nauhan DIN -> 330 ohm -> D6
 *   nauhan GND -> GND
 *   nauhan +5V -> Arduinon 5V (pieni virtabudjetti) tai oma 5 V:n virtalähde
 *                 (silloin virtalähteen - myös Arduinon GND:hen)
 *
 * "100 %" tarkoittaa suurinta kirkkautta, jonka virtabudjetti CURRENT_LIMIT_MA sallii
 * kyseiselle värille. Arduinon 5V-pinnistä riittää noin 350 mA, joten valkoinen jää
 * silloin noin 13 %:iin nauhan täydestä kirkkaudesta. Omalla virtalähteellä budjetin
 * voi nostaa virtalähteen kestoon asti.
 *
 * Koodi käyttää rekistereitä suoraan ilman Arduinon funktioita, mutta sen voi
 * ladata Arduino IDE:llä tavallisena sketsinä.
 */
#ifndef F_CPU
#define F_CPU 16000000UL
#endif
#include <avr/io.h>
#include <util/delay.h>

#define NUM_LEDS          40     // nauhan LEDien määrä
#define CURRENT_LIMIT_MA  350    // 350 = virta Arduinon 5V-pinnistä; esim. 1800 = oma 5 V / 2 A lähde
#define FADE_MS           2500   // kirkastumisen ja himmenemisen kesto
#define FRAME_MS          20     // päivitysväli

static uint8_t leds[NUM_LEDS * 3];   // jokaiselle LEDille G, R, B

static const uint8_t colors[][3] = { // R, G, B
    {255,   0,   0},   // punainen
    {  0, 255,   0},   // vihreä
    {  0,   0, 255},   // sininen
    {255, 255, 255},   // valkoinen
    {255, 140,   0},   // oranssi
    {  0, 255, 255},   // syaani
    {255,   0, 255},   // magenta
};
#define NUM_COLORS (sizeof colors / sizeof colors[0])

/* WS2812-protokolla: data D6 (PD6), 16 MHz */
static void ws_show(void) {
    uint8_t hi = PORTD | (1 << PD6);
    uint8_t lo = PORTD & ~(1 << PD6);
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
    _delay_us(300);                           // nollaus: linja alhaalla
}

/* Suurin kirkkaus (0–255), jolla värin virta pysyy budjetissa. Arvio: jokainen
   värikanava ottaa täydellä kirkkaudella 20 mA ja jokainen LED 1 mA lepovirtaa. */
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
    DDRD |= (1 << PD6);                       // data lähdöksi
    PORTD &= ~(1 << PD6);
    _delay_ms(100);

    const uint16_t steps = FADE_MS / FRAME_MS;
    uint8_t ci = 0;
    for (;;) {
        const uint8_t *c = colors[ci];
        uint8_t max_level = max_level_for(c);
        for (uint16_t s = 0; s <= 2 * steps; s++) {
            uint16_t p = (s <= steps) ? s : 2 * steps - s;           // 0 -> steps -> 0
            uint16_t linear = (uint32_t)p * 255 / steps;             // 0–255 = 0–100 %
            uint8_t perceived = (uint32_t)linear * linear / 255;     // silmälle tasainen muutos
            show_color(c, (uint16_t)perceived * max_level / 255);
            _delay_ms(FRAME_MS);
        }
        ci = (ci + 1) % NUM_COLORS;                                  // seuraava väri
    }
}
