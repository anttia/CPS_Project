/*
 * Energy accounting for the LED strip, without hardware (tested on a PC in test/).
 *
 * The INA219 gives the strip power in mW five times per second. energy_add() integrates
 * it into energy in fixed point: power (mW) x time (ms) = energy in microjoules, and every
 * 3600 uJ is one microwatt-hour (uWh). The remainder is carried to the next sample, so no
 * energy is lost to rounding however small the power is. No floating point is needed.
 *
 * The energy is booked into the hour of the day in which it was used. The Uno has no
 * real-time clock, so the dashboard sends the time of day (command C,<seconds since
 * midnight>) and the clock then runs on the Timer1 ticks. Until the first C command the
 * clock starts from midnight and "synced" is 0, so the hours are uptime hours.
 *
 * Memory: about 70 bytes of SRAM (24 x 2 bytes for the hours of the day + counters).
 */
#ifndef ENERGY_LOGIC_H
#define ENERGY_LOGIC_H

#include <stdint.h>

#define ENERGY_HOURS      24
#define ENERGY_DAY_MS     86400000UL
#define ENERGY_HOUR_MS    3600000UL
#define ENERGY_UJ_PER_UWH 3600UL      // 1 uWh = 3.6 mJ = 3600 uJ
#define ENERGY_MAX_DT_MS  2000UL      // a longer gap between samples is not integrated

/* Events returned by energy_add() and energy_set_clock() */
#define ENERGY_HOUR_DONE  0x01        // an hour of the day ended: see done_hour / done_uwh
#define ENERGY_DAY_DONE   0x02        // midnight passed: see done_day_uwh

typedef struct {
    uint32_t clock_ms;                // time of day, ms since midnight (0 ... 86399999)
    uint8_t  synced;                  // 1 = clock set by the dashboard (C command)
    uint32_t rem_uj;                  // energy not yet a whole uWh (0 ... 3599 uJ)
    uint32_t hour_uwh;                // energy of the current hour so far
    uint32_t day_uwh;                 // energy since midnight
    uint32_t gap_ms;                  // time without a valid power reading in this hour
    uint16_t hours_mwh[ENERGY_HOURS]; // energy of each finished hour today (mWh)
    /* Result of the last ENERGY_HOUR_DONE / ENERGY_DAY_DONE event */
    uint8_t  done_hour;
    uint32_t done_uwh;
    uint32_t done_gap_ms;
    uint32_t done_day_uwh;
} Energy;

static inline void energy_init(Energy *e) {
    e->clock_ms = 0;
    e->synced = 0;
    e->rem_uj = 0;
    e->hour_uwh = 0;
    e->day_uwh = 0;
    e->gap_ms = 0;
    for (uint8_t h = 0; h < ENERGY_HOURS; h++) e->hours_mwh[h] = 0;
    e->done_hour = 0;
    e->done_uwh = 0;
    e->done_gap_ms = 0;
    e->done_day_uwh = 0;
}

static inline uint8_t energy_hour(const Energy *e) {
    return (uint8_t)(e->clock_ms / ENERGY_HOUR_MS);
}

/* Closes the current hour: stores it in hours_mwh and in the done_* fields. */
static inline void energy_close_hour(Energy *e) {
    uint8_t h = energy_hour(e);
    uint32_t mwh = (e->hour_uwh + 500) / 1000;
    e->hours_mwh[h] = mwh > 0xFFFF ? 0xFFFF : (uint16_t)mwh;
    e->done_hour = h;
    e->done_uwh = e->hour_uwh;
    e->done_gap_ms = e->gap_ms;
    e->hour_uwh = 0;
    e->gap_ms = 0;
}

/* Starts a new day at midnight: the finished day total goes to done_day_uwh. */
static inline void energy_new_day(Energy *e) {
    e->done_day_uwh = e->day_uwh;
    e->day_uwh = 0;
    for (uint8_t h = 0; h < ENERGY_HOURS; h++) e->hours_mwh[h] = 0;
}

/* Advances the clock by dt_ms and books the energy of power mw over that time into the
   current hour. mw < 0 means no valid reading (INA219 missing): the time is counted as a
   gap and no energy is added. Returns ENERGY_HOUR_DONE / ENERGY_DAY_DONE flags.
   The whole sample is booked into the hour in which it started (at most 2 s error).
   The caller calls this every 200 ms, so at most one hour can end per call. */
static inline uint8_t energy_add(Energy *e, uint32_t dt_ms, int32_t mw) {
    uint8_t events = 0;
    if (dt_ms > ENERGY_MAX_DT_MS) {
        e->gap_ms += dt_ms;               // too long a gap: the power is unknown
    } else if (mw < 0) {
        e->gap_ms += dt_ms;
    } else {
        /* max 2000 ms x 65535 mW = 1.3e8 uJ: fits in 32 bits together with the remainder */
        e->rem_uj += (uint32_t)mw * dt_ms;
        uint32_t uwh = e->rem_uj / ENERGY_UJ_PER_UWH;
        e->rem_uj -= uwh * ENERGY_UJ_PER_UWH;
        e->hour_uwh += uwh;
        e->day_uwh += uwh;
    }
    uint8_t hour = energy_hour(e);
    e->clock_ms += dt_ms;
    if (e->clock_ms >= ENERGY_DAY_MS || e->clock_ms / ENERGY_HOUR_MS != hour) {
        uint32_t later = e->clock_ms;
        e->clock_ms = (uint32_t)hour * ENERGY_HOUR_MS;    // close the hour the sample started in
        energy_close_hour(e);
        events |= ENERGY_HOUR_DONE;
        if (later >= ENERGY_DAY_MS) {
            later -= ENERGY_DAY_MS;
            energy_new_day(e);
            events |= ENERGY_DAY_DONE;
        }
        e->clock_ms = later % ENERGY_DAY_MS;
    }
    return events;
}

/* Sets the time of day (seconds since midnight) from the dashboard.
   - Before the first sync the uptime hours are not real hours: the energy so far stays
     in the day total and moves into the new current hour, and no hour is reported.
   - A small correction inside the same hour only moves the clock.
   - A correction forwards past the end of the hour closes that hour (ENERGY_HOUR_DONE),
     and past midnight also the day. A correction backwards into an earlier hour keeps
     counting into the new hour without closing anything. */
static inline uint8_t energy_set_clock(Energy *e, uint32_t sec_of_day) {
    uint8_t events = 0;
    uint32_t new_ms = (sec_of_day % 86400UL) * 1000UL;
    uint8_t old_hour = energy_hour(e);
    uint8_t new_hour = (uint8_t)(new_ms / ENERGY_HOUR_MS);
    if (e->synced && new_hour != old_hour) {
        int8_t diff = (int8_t)new_hour - (int8_t)old_hour;
        /* forwards: e.g. 10 -> 11, or 23 -> 0 over midnight (diff -23) */
        if (diff == 1 || diff == -23) {
            energy_close_hour(e);
            events |= ENERGY_HOUR_DONE;
            if (diff == -23) {
                energy_new_day(e);
                events |= ENERGY_DAY_DONE;
            }
        }
    }
    if (!e->synced) {
        for (uint8_t h = 0; h < ENERGY_HOURS; h++) e->hours_mwh[h] = 0;   // uptime hours
    }
    e->clock_ms = new_ms;
    e->synced = 1;
    return events;
}

#endif
