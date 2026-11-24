/*
 * protocol.h:n testi PC:llä: tarkistussumman muoto ja virheiden havaitseminen.
 *
 *   gcc -std=c99 -Wall -Wextra -I.. test_protocol.c -o test_protocol && ./test_protocol
 *
 * Tulostaa lopuksi esimerkkirivin, jonka dashboardin testi tarkistaa samalla tavalla.
 */
#include <stdio.h>
#include <string.h>
#include "protocol.h"

static int failures = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("  VIRHE: %s\n", msg); failures++; } } while (0)

int main(void) {
    char line[80] = "1,150,42,150,1,4987,412,2050,17";
    uint8_t x = frame_xor(line);
    frame_close(line);
    char expected[8];
    snprintf(expected, sizeof expected, "*%02X", x);
    printf("kehys: %s\n", line);
    CHECK(strstr(line, expected) == line + strlen(line) - 3, "tarkistussumma ei ole rivin lopussa");

    /* Jokainen yksittäinen bittivirhe datassa muuttaa summan. */
    int flips = 0, caught = 0;
    const char *data = "1,150,42,150,1,4987,412,2050,17";
    for (size_t i = 0; i < strlen(data); i++)
        for (int b = 0; b < 7; b++) {             // ASCII: bitit 0–6
            char bad[80];
            strcpy(bad, data);
            bad[i] ^= (char)(1 << b);
            if (bad[i] == '\0') continue;
            flips++;
            if (frame_xor(bad) != x) caught++;
        }
    printf("yksittäisiä bittivirheitä %d, havaittiin %d\n", flips, caught);
    CHECK(flips == caught, "kaikkia yksittäisiä bittivirheitä ei havaittu");

    /* Kaksi samaa bittiä eri tavuissa kumoavat toisensa: XOR ei havaitse tätä. */
    char two[80];
    strcpy(two, data);
    two[0] ^= 2; two[2] ^= 2;
    printf("kaksi samaa bittiä eri tavuissa: %s\n", frame_xor(two) == x ? "ei havaittu (XOR:n rajoitus)" : "havaittu");

    printf(failures ? "\n%d testiä epäonnistui\n" : "\nKaikki testit läpäisty\n", failures);
    return failures ? 1 : 0;
}
