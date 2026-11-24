/*
 * Telemetriarivin kehys: virheentunnistus sarjayhteydelle.
 *
 * Rivi on muotoa
 *     pir,valo,kirkkaus,tavoite,tila,jannite_mV,virta_mA,teho_mW,numero*HH
 * missä numero kasvaa joka rivillä (0–255, kiertää ympäri) ja HH on tarkistussumma
 * kahtena heksamerkkinä: kaikkien '*'-merkkiä edeltävien tavujen XOR, kuten GPS-
 * vastaanottimien NMEA-lauseissa. Dashboard hylkää rivin, jonka summa ei täsmää, ja
 * päättelee numeroiden aukoista kadonneet rivit.
 *
 * Tiedosto on tavallista C:tä, joten sen voi testata PC:llä (test/test_protocol.c).
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>
#include <string.h>

/* XOR merkkijonon kaikista tavuista. Yksikin muuttunut bitti muuttaa summaa. */
static inline uint8_t frame_xor(const char *s) {
    uint8_t x = 0;
    while (*s) x ^= (uint8_t)*s++;
    return x;
}

/* Lisää rivin loppuun '*' ja tarkistussumman. Puskurissa pitää olla tilaa 3 merkille. */
static inline void frame_close(char *line) {
    static const char hex[] = "0123456789ABCDEF";
    uint8_t x = frame_xor(line);
    char *p = line + strlen(line);
    p[0] = '*';
    p[1] = hex[x >> 4];
    p[2] = hex[x & 0x0F];
    p[3] = '\0';
}

#endif
