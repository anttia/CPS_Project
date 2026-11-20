/*
 * Luxlamppu, vaihe 1: PIR- ja valoanturin luku ja lähetys PC:lle USB:n kautta.
 *
 * Kytkennät (Arduino Uno R3):
 *   Grove PIR    SIG -> D2,  VCC -> 5V, GND -> GND
 *   Grove Light  SIG -> A0,  VCC -> 5V, GND -> GND
 *
 * Lähettää viisi kertaa sekunnissa rivin "pir,valo", esim. "1,523".
 *   pir  = 1 kun liikettä havaitaan, muuten 0
 *   valo = valoanturin ADC-lukema 0–1023
 * Sarjaportin nopeus 9600 baudia.
 *
 * Koodi käyttää rekistereitä suoraan ilman Arduinon funktioita, mutta sen voi
 * ladata Arduino IDE:llä tavallisena sketsinä.
 */
#ifndef F_CPU
#define F_CPU 16000000UL
#endif
#include <avr/io.h>
#include <util/delay.h>
#include <stdlib.h>

static void uart_init(void) {
    UBRR0  = 103;                             // 16 MHz / (16 * 9600) - 1
    UCSR0B = (1 << TXEN0);
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);   // 8N1
}
static void uart_putc(char c) { while (!(UCSR0A & (1 << UDRE0))) ; UDR0 = c; }
static void uart_puts(const char *s) { while (*s) uart_putc(*s++); }
static void uart_putu(uint16_t v) { char b[6]; utoa(v, b, 10); uart_puts(b); }

static void adc_init(void) {
    ADMUX  = (1 << REFS0);                                             // referenssi AVcc, kanava ADC0 (A0)
    ADCSRA = (1 << ADEN) | (1 << ADPS2) | (1 << ADPS1) | (1 << ADPS0); // jakaja 128 -> 125 kHz
    DIDR0  = (1 << ADC0D);                                             // A0:n digitaalipuskuri pois
}
static uint16_t adc_read(void) {
    ADCSRA |= (1 << ADSC);
    while (ADCSRA & (1 << ADSC)) ;
    return ADC;
}

int main(void) {
    DDRB |= (1 << PB5);                       // kortin L-merkkivalo (D13) lähdöksi
    DDRD &= ~(1 << PD2);                      // PIR (D2) sisääntuloksi
    uart_init();
    adc_init();

    for (;;) {
        uint8_t pir = (PIND & (1 << PD2)) ? 1 : 0;
        if (pir) PORTB |= (1 << PB5); else PORTB &= ~(1 << PB5);   // L-valo näyttää PIR-tilan

        uint16_t sum = 0;                     // keskiarvo neljästä näytteestä vähentää kohinaa
        for (uint8_t i = 0; i < 4; i++) sum += adc_read();
        uint16_t light = sum / 4;

        uart_putu(pir);
        uart_putc(',');
        uart_putu(light);
        uart_puts("\r\n");
        _delay_ms(200);
    }
}
