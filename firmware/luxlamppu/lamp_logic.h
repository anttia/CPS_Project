/*
 * Lampun logiikka ilman laitteistoa: valon suodatus, I-säädin, tilakone ja animaatiot.
 *
 * Tiedosto on tavallista C:tä, joten sen voi kääntää ja testata myös PC:llä
 * (test/test_lamp_logic.c). Arduino-ohjelma luxlamppu.ino lukee anturit, kutsuu
 * lamp_step()-funktiota säätövälein ja piirtää nauhan lamp_led_pm()-funktion mukaan.
 *
 * Tilat:
 *   LAMP_ON       säätö pitää valoanturin lukeman tavoitteessa
 *   LAMP_GOODBYE  sammumisanimaatio, kun liikettä ei ole havaittu pitoaikaan
 *   LAMP_OFF      nauha pois, odotetaan liikettä
 *   LAMP_WELCOME  heräämisanimaatio, kun liike havaitaan uudelleen
 *
 * Animaatiot (30 s): heräämisessä valo leviää nauhan keskeltä reunoille ja jokainen
 * LED kirkastuu pehmeästi, kun valon etureuna ohittaa sen. Sammuminen on sama
 * käänteisenä: LEDit himmenevät reunoilta alkaen, ja keskikohta sammuu viimeisenä.
 * Jos liike havaitaan kesken sammumisen, herääminen jatkuu samasta kohdasta, joten
 * nauha ei välähdä pimeäksi.
 */
#ifndef LAMP_LOGIC_H
#define LAMP_LOGIC_H

#include <stdint.h>

enum { LAMP_OFF = 0, LAMP_ON = 1, LAMP_GOODBYE = 2, LAMP_WELCOME = 3 };

#define LAMP_DEADBAND     3       // säädin ei reagoi tätä pienempään virheeseen (ADC-yksikköä)
#define LAMP_ANIM_MS      30000UL // animaation kesto (herääminen ja sammuminen)
#define LAMP_ANIM_MIN_PM  300     // herääminen vähintään 30 %:n kirkkauteen
#define LAMP_ANIM_EDGE_PM 400     // valon etureunan leveys promilleina nauhan puolikkaasta:
                                  // kuinka pitkällä matkalla LED kirkastuu nollasta täyteen

typedef struct {
    uint8_t  state;
    uint16_t target;          // tavoite valoanturin yksiköissä (0–1023)
    uint32_t hold_ms;         // pitoaika ilman liikettä ennen sammumista
    uint8_t  ki;              // säätimen kerroin: promillea kirkkautta per ADC-yksikkö per askel
    int16_t  level_pm;        // säätimen kirkkaus promilleina (0–1000 = 0–100 %)
    int32_t  light_f16;       // suodatettu valoanturin lukema × 16; -1 = ei vielä luettu
    uint32_t last_motion_ms;
    uint32_t anim_start_ms;
    int16_t  anim_level_pm;   // animaation kirkkaus promilleina
} Lamp;

static inline void lamp_init(Lamp *l, uint32_t now) {
    l->state = LAMP_ON;
    l->target = 150;
    l->hold_ms = 180000UL;
    l->ki = 3;
    l->level_pm = 0;
    l->light_f16 = -1;
    l->last_motion_ms = now;
    l->anim_start_ms = 0;
    l->anim_level_pm = 0;
}

static inline uint16_t lamp_light(const Lamp *l) {
    return l->light_f16 < 0 ? 0 : (uint16_t)(l->light_f16 / 16);
}

/* Aloittaa animaation. Sammuminen lähtee nykyisestä kirkkaudesta, herääminen
   vähintään LAMP_ANIM_MIN_PM:n kirkkauteen. */
static inline void lamp_start_anim(Lamp *l, uint8_t state, uint32_t now) {
    l->state = state;
    l->anim_start_ms = now;
    if (state == LAMP_GOODBYE) l->anim_level_pm = l->level_pm;
    else l->anim_level_pm = l->level_pm > LAMP_ANIM_MIN_PM ? l->level_pm : LAMP_ANIM_MIN_PM;
}

/* Sammuminen keskeytyy liikkeeseen: herääminen jatkuu samasta vaiheesta ja samalla
   kirkkaudella, joten nauhan valo ei hyppää. */
static inline void lamp_reverse_goodbye(Lamp *l, uint32_t now) {
    uint32_t elapsed = now - l->anim_start_ms;
    if (elapsed > LAMP_ANIM_MS) elapsed = LAMP_ANIM_MS;
    l->state = LAMP_WELCOME;
    l->anim_start_ms = now - (LAMP_ANIM_MS - elapsed);
}

/* Liike havaittu: pitoaika alkaa alusta, ja sammuva tai pimeä lamppu alkaa herätä.
   Firmware kutsuu tätä heti, kun PIR:n keskeytys on ilmoittanut liikkeen alkaneen, eikä
   odota seuraavaa säätöaskelta. lamp_step() kutsuu tätä niin kauan kuin PIR näkee liikettä. */
static inline void lamp_motion(Lamp *l, uint32_t now) {
    l->last_motion_ms = now;
    if (l->state == LAMP_GOODBYE) lamp_reverse_goodbye(l, now);
    else if (l->state == LAMP_OFF) lamp_start_anim(l, LAMP_WELCOME, now);
}

/* Animaation vaihe promilleina: 0 = nauha pimeä, 1000 = koko nauha animaation kirkkaudessa. */
static inline int32_t lamp_anim_phase(const Lamp *l, uint32_t now) {
    uint32_t t = now - l->anim_start_ms;
    if (t > LAMP_ANIM_MS) t = LAMP_ANIM_MS;
    int32_t p = (int32_t)(t * 1000UL / LAMP_ANIM_MS);
    return l->state == LAMP_WELCOME ? p : 1000 - p;
}

/* Kutsutaan säätövälein (100 ms). pir = 1 kun liikettä havaitaan. */
static inline void lamp_step(Lamp *l, uint32_t now, uint8_t pir, uint16_t light_raw) {
    // Eksponenttisuodin (a = 1/4) tasoittaa anturin kohinan ja LEDien PWM-välkkeen
    if (l->light_f16 < 0) l->light_f16 = (int32_t)light_raw * 16;
    else l->light_f16 += ((int32_t)light_raw * 16 - l->light_f16) / 4;
    if (pir) l->last_motion_ms = now;

    switch (l->state) {
    case LAMP_ON:
        if (now - l->last_motion_ms > l->hold_ms) {
            lamp_start_anim(l, LAMP_GOODBYE, now);
            break;
        }
        {
            // I-säädin kuolleella alueella: kirkkautta muutetaan virheeseen verrannollisesti
            int32_t err = (int32_t)l->target - (int32_t)lamp_light(l);
            if (err > LAMP_DEADBAND || err < -LAMP_DEADBAND) {
                int32_t level = l->level_pm + (int32_t)l->ki * err;
                if (level < 0) level = 0;
                if (level > 1000) level = 1000;     // rajoitus = anti-windup
                l->level_pm = (int16_t)level;
            }
        }
        break;
    case LAMP_GOODBYE:
        if (pir) lamp_motion(l, now);
        else if (now - l->anim_start_ms >= LAMP_ANIM_MS) l->state = LAMP_OFF;
        break;
    case LAMP_OFF:
        if (pir) lamp_motion(l, now);
        break;
    case LAMP_WELCOME:
        if (now - l->anim_start_ms >= LAMP_ANIM_MS) {
            l->level_pm = l->anim_level_pm;     // säätö jatkaa animaation kirkkaudesta
            l->state = LAMP_ON;
        }
        break;
    }
}

/* LEDin i kirkkaus promilleina hetkellä now (n = LEDien määrä).
   Animaatiossa valon etureuna kulkee keskeltä reunojen yli. LED alkaa kirkastua,
   kun etureuna saavuttaa sen, ja on täysin kirkas, kun etureuna on edennyt
   LAMP_ANIM_EDGE_PM:n verran pidemmälle. Kirkastuminen on neliöllinen, jotta se
   näyttää silmälle tasaiselta. 30 s:n animaatiossa keskimmäinen LED kirkastuu
   noin 9 s:ssa ja valo saavuttaa reunat noin 21 s:n kohdalla. */
static inline int16_t lamp_led_at(const Lamp *l, int32_t reach, uint16_t i, uint16_t n) {
    int32_t d = 2 * (int32_t)i - (int32_t)(n - 1);          // etäisyys keskeltä × 2
    if (d < 0) d = -d;
    d = n > 1 ? d * 1000 / (int32_t)(n - 1) : 0;            // 0 = keskellä, 1000 = reunalla
    int32_t f = (reach - d) * 1000 / LAMP_ANIM_EDGE_PM;
    if (f <= 0) return 0;
    if (f > 1000) f = 1000;
    f = f * f / 1000;
    return (int16_t)((int32_t)l->anim_level_pm * f / 1000);
}

/* Valon etureunan paikka promilleina nauhan puolikkaasta (sama koko kehykselle). */
static inline int32_t lamp_anim_reach(const Lamp *l, uint32_t now) {
    return lamp_anim_phase(l, now) * (1000 + LAMP_ANIM_EDGE_PM) / 1000;
}

static inline int16_t lamp_led_pm(const Lamp *l, uint16_t i, uint16_t n, uint32_t now) {
    if (l->state == LAMP_ON) return l->level_pm;
    if (l->state == LAMP_OFF) return 0;
    return lamp_led_at(l, lamp_anim_reach(l, now), i, n);
}

/* Koko nauhan kirkkaudet kerralla taulukkoon out[n]: sama tulos kuin lamp_led_pm()
   jokaiselle LEDille, mutta nopeampi. Etureunan paikka lasketaan kerran kehystä kohden,
   ja koska nauha on symmetrinen keskikohdan suhteen, LEDit i ja n-1-i saavat saman arvon
   yhdellä laskulla. Palauttaa nauhan keskimääräisen kirkkauden (= lamp_display_pm).
   Syy: 8-bittinen AVR jakaa 32-bittisiä lukuja ohjelmallisesti, ja LED kerrallaan
   laskettuna animaatiokehys vei yli 10 ms, jolloin Timer1:n tikkejä jäi käsittelemättä. */
static inline int16_t lamp_render_pm(const Lamp *l, uint16_t n, uint32_t now, int16_t *out) {
    if (n == 0) return 0;
    if (l->state == LAMP_ON || l->state == LAMP_OFF) {
        int16_t v = l->state == LAMP_ON ? l->level_pm : 0;
        for (uint16_t i = 0; i < n; i++) out[i] = v;
        return v;
    }
    int32_t reach = lamp_anim_reach(l, now);
    int32_t sum = 0;
    for (uint16_t i = 0; i <= (n - 1) / 2; i++) {
        uint16_t j = n - 1 - i;
        int16_t v = lamp_led_at(l, reach, i, n);
        out[i] = out[j] = v;
        sum += (i == j) ? v : 2 * (int32_t)v;
    }
    return (int16_t)(sum / n);
}

/* Nauhan keskimääräinen kirkkaus promilleina: ilmoitetaan dashboardille. */
static inline int16_t lamp_display_pm(const Lamp *l, uint16_t n, uint32_t now) {
    if (l->state == LAMP_ON) return l->level_pm;
    if (l->state == LAMP_OFF || n == 0) return 0;
    int32_t sum = 0;
    for (uint16_t i = 0; i < n; i++) sum += lamp_led_pm(l, i, n, now);
    return (int16_t)(sum / n);
}

#endif
