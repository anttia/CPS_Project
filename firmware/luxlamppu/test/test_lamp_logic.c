/*
 * lamp_logic.h:n testi PC:llä simuloidussa huoneessa.
 *
 *   gcc -std=c99 -Wall -Wextra -I.. test_lamp_logic.c -lm -o test && ./test
 *
 * Simuloitu valoanturi: lukema = taustavalo + g * LED-kirkkaus (%) + kohina,
 * missä g on lampun vahvistus (ADC-yksikköä per kirkkausprosentti).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "lamp_logic.h"

#define N_LEDS 39
static int failures = 0;

#define CHECK(cond, ...) do { if (!(cond)) { printf("  VIRHE: " __VA_ARGS__); printf("\n"); failures++; } } while (0)

static double avg_pct(const Lamp *l, uint32_t now) {
    long sum = 0;
    for (int i = 0; i < N_LEDS; i++) sum += lamp_led_pm(l, i, N_LEDS, now);
    return sum / (double)N_LEDS / 10.0;
}

static uint16_t sensor(double ambient, double g, double pct) {
    double v = ambient + g * pct + ((rand() % 5) - 2);      // kohina ±2
    if (v < 0) v = 0;
    if (v > 1023) v = 1023;
    return (uint16_t)v;
}

/* Säätö: asettuuko lukema tavoitteeseen ja pysyykö se siinä värähtelemättä?
   Tavoite valitaan niin, että se saavutetaan 60 %:n kirkkaudella. */
static void test_control(double g) {
    Lamp l; lamp_init(&l, 0);
    l.target = (uint16_t)(30 + 60 * g);
    double lo = 1e9, hi = -1e9;
    int settled_at = -1;
    for (uint32_t t = 0; t <= 60000; t += 100) {
        uint16_t s = sensor(30, g, avg_pct(&l, t));
        lamp_step(&l, t, 1, s);
        int err = (int)lamp_light(&l) - (int)l.target;
        if (settled_at < 0 && abs(err) <= LAMP_DEADBAND + 2) settled_at = (int)t;
        if (t >= 40000) { if (l.level_pm < lo) lo = l.level_pm; if (l.level_pm > hi) hi = l.level_pm; }
    }
    double swing = (hi - lo) / 10.0;
    printf("g = %.1f, tavoite %u: asettui %.1f s:ssa, lopussa kirkkaus %.1f %% (heilunta %.1f %%-yks.), lukema %u\n",
           g, l.target, settled_at / 1000.0, l.level_pm / 10.0, swing, lamp_light(&l));
    CHECK(settled_at >= 0 && settled_at <= 15000, "g=%.1f ei asettunut 15 s:ssa", g);
    CHECK(abs((int)lamp_light(&l) - (int)l.target) <= LAMP_DEADBAND + 3, "g=%.1f lukema %u ei tavoitteessa", g, lamp_light(&l));
    CHECK(swing <= 5.0, "g=%.1f kirkkaus heiluu %.1f %%-yks.", g, swing);
}

/* Tavoite, jota lamppu ei pysty saavuttamaan: kirkkaus jää 100 %:iin ja palaa heti, kun tavoite lasketaan. */
static void test_unreachable(void) {
    Lamp l; lamp_init(&l, 0);
    for (uint32_t t = 0; t <= 30000; t += 100) {
        if (t == 20000) l.target = 60;
        lamp_step(&l, t, 1, sensor(30, 0.5, avg_pct(&l, t)));
        if (t == 19900) {
            printf("saavuttamaton tavoite 150 (maksimi noin 80): kirkkaus %.1f %%\n", l.level_pm / 10.0);
            CHECK(l.level_pm == 1000, "kirkkaus ei ole 100 %%");
        }
    }
    printf("tavoite laskettu 60:een: kirkkaus %.1f %%, lukema %u\n", l.level_pm / 10.0, lamp_light(&l));
    CHECK(abs((int)lamp_light(&l) - 60) <= LAMP_DEADBAND + 3, "ei palautunut tavoitteeseen (anti-windup)");
}

/* Taustavalo nousee yli tavoitteen: lampun pitää sammua säädöllä. */
static void test_bright_room(void) {
    Lamp l; lamp_init(&l, 0);
    double ambient = 30;
    for (uint32_t t = 0; t <= 60000; t += 100) {
        if (t == 30000) ambient = 220;                  // kattovalot päälle
        lamp_step(&l, t, 1, sensor(ambient, 2.0, avg_pct(&l, t)));
    }
    printf("kattovalot päälle: kirkkaus %.1f %%, lukema %u\n", l.level_pm / 10.0, lamp_light(&l));
    CHECK(l.level_pm == 0, "lamppu ei sammunut kirkkaassa huoneessa");
}

/* Pitoaika, sammumis- ja heräämisanimaatio. */
static void test_presence(void) {
    Lamp l; lamp_init(&l, 0);
    l.hold_ms = 30000;
    uint32_t goodbye = 0, off = 0, welcome = 0, on = 0;
    uint8_t prev = l.state;
    for (uint32_t t = 0; t <= 200000; t += 100) {
        uint8_t pir = (t < 60000 || t >= 150000) ? 1 : 0;   // ihminen palaa 150 s:n kohdalla
        lamp_step(&l, t, pir, sensor(30, 2.0, avg_pct(&l, t)));
        if (l.state != prev) {
            if (l.state == LAMP_GOODBYE) goodbye = t;
            if (l.state == LAMP_OFF) off = t;
            if (l.state == LAMP_WELCOME) welcome = t;
            if (l.state == LAMP_ON) on = t;
            prev = l.state;
        }
    }
    printf("liike loppui 60,0 s: sammumisanimaatio %.1f s, pois %.1f s; liike taas 150,0 s: herääminen %.1f s, säätö %.1f s\n",
           goodbye / 1000.0, off / 1000.0, welcome / 1000.0, on / 1000.0);
    CHECK(goodbye >= 90000 && goodbye <= 90200, "sammumisanimaatio väärään aikaan");
    CHECK(off >= goodbye + LAMP_ANIM_MS && off <= goodbye + LAMP_ANIM_MS + 200, "nauha ei sammunut animaation jälkeen");
    CHECK(welcome == 150000, "herääminen ei alkanut liikkeestä");
    CHECK(on >= welcome + LAMP_ANIM_MS && on <= welcome + LAMP_ANIM_MS + 200, "säätö ei jatkunut animaation jälkeen");
    CHECK(l.state == LAMP_ON, "lamppu ei ole päällä lopussa");
}

/* Animaation kuvio: valo leviää keskeltä reunoille ja kirkastuu pehmeästi. */
static void test_animation(void) {
    Lamp l; lamp_init(&l, 0);
    l.level_pm = 500;
    lamp_start_anim(&l, LAMP_WELCOME, 0);
    int lit_start = 0, lit_mid = 0, full_end = 0;
    for (int i = 0; i < N_LEDS; i++) {
        lit_start += lamp_led_pm(&l, i, N_LEDS, 0) > 0;
        lit_mid += lamp_led_pm(&l, i, N_LEDS, LAMP_ANIM_MS / 2) > 0;
        full_end += lamp_led_pm(&l, i, N_LEDS, LAMP_ANIM_MS) == 500;
    }
    int center = lamp_led_pm(&l, N_LEDS / 2, N_LEDS, LAMP_ANIM_MS / 2) > 0;
    int edge = lamp_led_pm(&l, 0, N_LEDS, LAMP_ANIM_MS / 2) > 0;
    printf("heräämisanimaatio: palavia LEDejä alussa %d, puolivälissä %d, lopussa täysin kirkkaita %d / %d\n",
           lit_start, lit_mid, full_end, N_LEDS);
    CHECK(lit_start == 0 && full_end == N_LEDS && lit_mid > 0 && lit_mid < N_LEDS, "kuvio ei etene");
    CHECK(center && !edge, "puolivälissä keskikohdan pitäisi palaa ja reunan ei");

    /* Pehmeys: yksikään LED ei saa muuttua 20 ms:n ruutujen välillä paljon, ja nauhan
       keskikirkkaus kasvaa koko ajan. Tulostetaan keskikirkkaus 5 s:n välein. */
    int max_step = 0, monotonic = 1, prev_avg = -1;
    printf("keskikirkkaus (%% animaation kirkkaudesta):");
    for (uint32_t t = 0; t <= LAMP_ANIM_MS; t += 20) {
        for (int i = 0; i < N_LEDS; i++) {
            int step = lamp_led_pm(&l, i, N_LEDS, t + 20 > LAMP_ANIM_MS ? LAMP_ANIM_MS : t + 20) - lamp_led_pm(&l, i, N_LEDS, t);
            if (abs(step) > max_step) max_step = abs(step);
        }
        int avg = lamp_display_pm(&l, N_LEDS, t);
        if (avg < prev_avg) monotonic = 0;
        prev_avg = avg;
        if (t % 5000 == 0) printf(" %u s: %d", t / 1000, avg * 100 / 500);
    }
    printf("\nsuurin muutos yhdessä LEDissä 20 ms:ssa: %d promillea\n", max_step);
    CHECK(max_step <= 5, "kirkkaus muuttuu liian jyrkästi");
    CHECK(monotonic, "keskikirkkaus ei kasva tasaisesti");
}

/* Liike kesken sammumisen: herääminen jatkuu samasta kohdasta ilman hyppyä. */
static void test_reverse(void) {
    Lamp l; lamp_init(&l, 0);
    l.level_pm = 600;
    lamp_start_anim(&l, LAMP_GOODBYE, 0);
    uint32_t t = LAMP_ANIM_MS / 3;
    int before = lamp_display_pm(&l, N_LEDS, t);
    lamp_step(&l, t, 1, 100);
    int after = lamp_display_pm(&l, N_LEDS, t);
    int later = lamp_display_pm(&l, N_LEDS, t + 2000);
    printf("liike sammumisen kolmanneksella: kirkkaus %d -> %d promillea, 2 s myöhemmin %d\n", before, after, later);
    CHECK(l.state == LAMP_WELCOME, "ei siirtynyt heräämiseen");
    CHECK(abs(after - before) <= 2, "kirkkaus hyppäsi");
    CHECK(later > after, "kirkkaus ei kasva heräämisessä");
}

/* lamp_render_pm() laskee koko nauhan kerralla nopeammin; tuloksen pitää olla täsmälleen
   sama kuin lamp_led_pm():llä LED kerrallaan, kaikissa tiloissa ja eri nauhan pituuksilla. */
static void test_render_matches(void) {
    printf("Nopea koko nauhan laskenta = LED kerrallaan\n");
    uint16_t lengths[] = {1, 2, 3, 38, 39, 60};
    uint8_t states[] = {LAMP_OFF, LAMP_ON, LAMP_GOODBYE, LAMP_WELCOME};
    int16_t out[60];
    long compared = 0;
    for (int a = 0; a < 6; a++) {
        uint16_t n = lengths[a];
        for (int b = 0; b < 4; b++) {
            Lamp l;
            lamp_init(&l, 0);
            l.level_pm = 640;
            lamp_start_anim(&l, states[b], 0);
            l.state = states[b];
            for (uint32_t t = 0; t <= LAMP_ANIM_MS; t += 250) {
                int16_t avg = lamp_render_pm(&l, n, t, out);
                for (uint16_t i = 0; i < n; i++, compared++)
                    CHECK(out[i] == lamp_led_pm(&l, i, n, t),
                          "n=%u tila=%u t=%lu LED %u: %d != %d", n, states[b], (unsigned long)t, i,
                          out[i], lamp_led_pm(&l, i, n, t));
                CHECK(avg == lamp_display_pm(&l, n, t), "n=%u t=%lu keskiarvo %d != %d",
                      n, (unsigned long)t, avg, lamp_display_pm(&l, n, t));
            }
        }
    }
    printf("  verrattu %ld LED-arvoa\n", compared);
}

/* lamp_motion(): PIR:n keskeytys herättää lampun heti, ilman säätöaskelta. */
static void test_motion_event(void) {
    Lamp l; lamp_init(&l, 0);
    l.hold_ms = 30000;
    l.state = LAMP_OFF;
    lamp_motion(&l, 5000);
    printf("liike pimeässä: tila %u, animaatio alkoi %lu ms\n", l.state, (unsigned long)l.anim_start_ms);
    CHECK(l.state == LAMP_WELCOME && l.anim_start_ms == 5000, "pimeä lamppu ei alkanut herätä heti");

    lamp_init(&l, 0);
    l.hold_ms = 30000;
    lamp_motion(&l, 25000);                         // säätötilassa liike vain nollaa pitoajan
    lamp_step(&l, 50000, 0, 100);
    CHECK(l.state == LAMP_ON, "pitoaika ei alkanut alusta liikkeestä");
    lamp_step(&l, 55100, 0, 100);
    CHECK(l.state == LAMP_GOODBYE, "sammuminen ei alkanut pitoajan jälkeen");

    lamp_motion(&l, 56000);                         // sammumisen aikana liike kääntää animaation
    CHECK(l.state == LAMP_WELCOME, "sammuva lamppu ei alkanut herätä");
}

int main(void) {
    srand(1);
    double gains[] = {0.5, 1.0, 2.0, 4.0, 6.0};
    for (int i = 0; i < 5; i++) test_control(gains[i]);
    test_unreachable();
    test_bright_room();
    test_presence();
    test_animation();
    test_reverse();
    test_render_matches();
    test_motion_event();
    printf(failures ? "\n%d testiä epäonnistui\n" : "\nKaikki testit läpäisty\n", failures);
    return failures ? 1 : 0;
}
