/*
 * Test of energy_logic.h on a PC.
 *
 *   gcc -std=c99 -Wall -Wextra -I.. test_energy_logic.c -lm -o test_energy && ./test_energy
 *
 * The reference is the same integral in double precision. The fixed-point integral must
 * not lose energy to rounding, must book energy into the right hour and must close hours
 * and days at the right time.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "energy_logic.h"

static int failures = 0;

#define CHECK(cond, ...) do { if (!(cond)) { printf("  FAIL: " __VA_ARGS__); printf("\n"); failures++; } } while (0)

/* Constant power for one hour in 200 ms steps: the result must be exact. */
static void test_constant_power(void) {
    Energy e; energy_init(&e);
    energy_set_clock(&e, 10 * 3600);
    uint8_t ev = 0;
    for (int i = 0; i < 18000; i++) ev |= energy_add(&e, 200, 1000);   // 1 W for 1 h
    printf("1 W for 1 h: hour %u, %lu uWh (expected 1000000)\n",
           e.done_hour, (unsigned long)e.done_uwh);
    CHECK(ev & ENERGY_HOUR_DONE, "hour 10 was not closed");
    CHECK(e.done_hour == 10, "closed hour %u, expected 10", e.done_hour);
    CHECK(e.done_uwh == 1000000UL, "energy %lu uWh, expected 1000000", (unsigned long)e.done_uwh);
    CHECK(e.hours_mwh[10] == 1000, "hours_mwh[10] = %u", e.hours_mwh[10]);
    CHECK(energy_hour(&e) == 11 && e.hour_uwh == 0, "the next hour did not start empty");
}

/* Small power: 1 mW x 200 ms = 200 uJ is less than 1 uWh (3600 uJ). Without the carried
   remainder every sample would round to zero. */
static void test_small_power_remainder(void) {
    Energy e; energy_init(&e);
    for (int i = 0; i < 9000; i++) energy_add(&e, 200, 1);              // 1 mW for 30 min
    printf("1 mW for 30 min: %lu uWh (expected 500)\n", (unsigned long)e.day_uwh);
    CHECK(e.day_uwh == 500, "lost energy to rounding: %lu uWh", (unsigned long)e.day_uwh);
}

/* Varying power and jittery sample intervals against a double-precision reference. */
static void test_random_vs_reference(void) {
    Energy e; energy_init(&e);
    energy_set_clock(&e, 0);
    double ref_uwh = 0;
    srand(1);
    for (int i = 0; i < 200000; i++) {
        uint32_t dt = 190 + rand() % 21;                 // 190 ... 210 ms
        int32_t mw = rand() % 4500;                      // 0 ... 4.5 W
        ref_uwh += mw * (double)dt / 3600.0;
        energy_add(&e, dt, mw);
    }
    double got = e.day_uwh + e.rem_uj / 3600.0;
    printf("random power, %.1f h: %lu uWh vs reference %.1f uWh\n",
           e.clock_ms / 3.6e6, (unsigned long)e.day_uwh, ref_uwh);
    CHECK(fabs(got - ref_uwh) < 0.01, "differs from the reference by %.3f uWh", got - ref_uwh);
    uint32_t sum = e.hour_uwh;
    for (int h = 0; h < energy_hour(&e); h++) sum += e.hours_mwh[h] * 1000UL;
    CHECK(labs((long)sum - (long)e.day_uwh) <= 500L * energy_hour(&e),
          "hours do not add up to the day total");
}

/* Missing INA219 (mw = -1) and too long gaps are counted as gap time, not energy. */
static void test_gaps(void) {
    Energy e; energy_init(&e);
    energy_set_clock(&e, 3600);
    energy_add(&e, 200, -1);
    energy_add(&e, 5000, 1000);
    energy_add(&e, 200, 1000);
    CHECK(e.gap_ms == 5200, "gap %lu ms, expected 5200", (unsigned long)e.gap_ms);
    CHECK(e.hour_uwh == 55, "energy %lu uWh, expected 55", (unsigned long)e.hour_uwh);  // 200 ms x 1 W
}

/* Midnight: hour 23 and the day are closed, and the clock wraps to 00:00. */
static void test_midnight(void) {
    Energy e; energy_init(&e);
    energy_set_clock(&e, 86400 - 10);                    // 23:59:50
    uint8_t ev = 0;
    for (int i = 0; i < 100; i++) ev |= energy_add(&e, 200, 360);      // 20 s at 360 mW
    printf("midnight: events 0x%02x, day %lu uWh, clock %lu s\n", ev,
           (unsigned long)e.done_day_uwh, (unsigned long)(e.clock_ms / 1000));
    CHECK((ev & (ENERGY_HOUR_DONE | ENERGY_DAY_DONE)) == (ENERGY_HOUR_DONE | ENERGY_DAY_DONE),
          "hour and day were not closed");
    CHECK(e.done_hour == 23, "closed hour %u, expected 23", e.done_hour);
    CHECK(e.done_day_uwh == 1000, "day %lu uWh, expected 1000 (10 s x 360 mW)",
          (unsigned long)e.done_day_uwh);
    CHECK(e.clock_ms == 10000, "clock %lu ms, expected 10000", (unsigned long)e.clock_ms);
    CHECK(e.day_uwh == 1000 && e.hours_mwh[23] == 0, "new day did not start fresh");
}

/* Clock corrections from the dashboard. */
static void test_set_clock(void) {
    Energy e; energy_init(&e);
    /* Before the first sync: energy stays, uptime hours are cleared, nothing is reported */
    for (int i = 0; i < 18000 * 2; i++) energy_add(&e, 200, 100);       // 2 h uptime, 100 mW
    uint32_t before = e.day_uwh;
    uint8_t ev = energy_set_clock(&e, 14 * 3600 + 120);
    CHECK(ev == 0 && e.synced, "first sync reported events");
    CHECK(e.day_uwh == before && e.hours_mwh[0] == 0, "first sync lost energy or kept uptime hours");
    CHECK(energy_hour(&e) == 14, "hour %u, expected 14", energy_hour(&e));
    /* Small correction inside the hour */
    ev = energy_set_clock(&e, 14 * 3600 + 100);
    CHECK(ev == 0 && e.clock_ms == (14 * 3600UL + 100) * 1000, "small correction failed");
    /* Forwards past the end of the hour closes it */
    energy_add(&e, 200, 3600);
    ev = energy_set_clock(&e, 15 * 3600 + 2);
    CHECK((ev & ENERGY_HOUR_DONE) && e.done_hour == 14, "forward correction did not close hour 14");
    /* Backwards into the previous hour: nothing is closed */
    ev = energy_set_clock(&e, 15 * 3600 - 2);
    CHECK(ev == 0 && energy_hour(&e) == 14, "backward correction closed an hour");
    /* Forwards over midnight closes the hour and the day */
    energy_set_clock(&e, 86400 - 1);
    ev = energy_set_clock(&e, 1);
    CHECK(ev == (ENERGY_HOUR_DONE | ENERGY_DAY_DONE), "correction over midnight: events 0x%02x", ev);
}

int main(void) {
    test_constant_power();
    test_small_power_remainder();
    test_random_vs_reference();
    test_gaps();
    test_midnight();
    test_set_clock();
    printf("sizeof(Energy) = %u bytes\n", (unsigned)sizeof(Energy));
    if (failures) {
        printf("%d FAILURES\n", failures);
        return 1;
    }
    printf("ALL TESTS PASSED\n");
    return 0;
}
