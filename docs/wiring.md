# Wiring

Two things connect to the Pi: the button switch and the button lamp. Everything
else is LED power and data inside the bays.

## Overview

```
  fence post                       indoors, next to the Pi
  ┌──────────────┐                 ┌────────────────────────────┐
  │ dome button  │  4 core cable   │  Pi 5                      │
  │  switch   ───┼─────────────────┼─ GPIO17  (+ 2k2 to 3V3)    │
  │  common   ───┼─────────────────┼─ GND                       │
  │  lamp +   ───┼─────────────────┼─ 12V from buck converter   │
  │  lamp -   ───┼─────────────────┼─ MOSFET drain              │
  └──────────────┘                 │  GPIO18 ─ 220R ─ MOSFET    │
                                   └────────────────────────────┘

  bay windows (x2 floors)
  24V PSU ──┬── GLEDOPTO controller ── 4 data outputs ── 4 sections
            └── strip power, injected at both ends of each run
```

## The switch

The dome button is a plain microswitch. It pulls GPIO17 to ground when pressed.

- **Do not rely on the Pi's internal pull-up.** It is around 50k, and a 10 to
  20 metre run to the fence post is a decent aerial. Fit a **2.2k resistor from
  GPIO17 to 3V3** at the Pi end.
- Fit a **100nF ceramic capacitor across the switch terminals at the Pi end**
  (GPIO17 to GND). That plus the 60ms software debounce kills contact bounce
  and most induced noise.
- Use **twisted pair or shielded cable** for the switch pair. If you use
  shielded, ground the screen at the Pi end only.
- Keep the switch pair away from the LED data runs and the mains.
- If the run turns out to be longer than about 20 metres, or you get phantom
  triggers in the rain, swap the direct connection for an **optocoupler**
  (PC817 or similar): 12V down the cable through the button into the LED side
  of the opto, transistor side pulling GPIO17 down. Immune to the noise the
  direct connection is not.

Leave `button.pull_up` ticked in the web interface either way. The internal
pull-up does no harm alongside the external one.

## The lamp

The dome lamp is 12V and the Pi's GPIO is 3.3V at a few milliamps, so it needs
a switch between them.

**Revision to the enclosure spec.** The printed box has two M3 bosses for "a
buck module or a terminal block". You now need a buck converter, a MOSFET and
somewhere to land four cores, which will not fit. Put the electronics indoors
next to the Pi and send switched 12V up to the post. The outdoor box then
contains nothing but the button and the cable gland, which is also one less
thing to fail in the wet.

Indoors:

- **24V to 12V buck converter** fed from a spare 24V supply, or a small 12V
  supply of its own.
- **Logic level N-channel MOSFET** on the low side: IRLZ44N, IRLB8721, or a
  ready-made "MOSFET trigger switch" module, which saves the discrete parts.
  - Gate: GPIO18 through a **220R** resistor.
  - Gate to source: **10k pull-down**, so the lamp is off while the Pi boots.
  - Source: ground, **common with the Pi's ground**.
  - Drain: the lamp's negative.
- Lamp positive goes to 12V.

The Pi drives GPIO18 with PWM at 200Hz for the steady glow, the slow idle
pulse and the flashing. If you wire the lamp permanently to 12V instead, untick
**Lamp controlled by the Pi** in the Button tab and it simply stays on.

## Common ground

The Pi, the MOSFET source and the 12V supply must share a ground. If the LED
PSUs are separate supplies, their grounds should also be commoned back to the
controllers in a star from each PSU, as already planned.

## LED power

Unchanged from the build plan: a 24V PSU per floor, star topology from the PSU,
short data runs from controller to first pixel, and power injected at both ends
of each four strip daisy chain. 4 sections x 4 strips x 0.722m at 14W/m is
about 162W per floor at full white, so a 24V 10A supply per floor has
reasonable headroom.

## GPIO summary

| Pin (BCM) | Direction | Goes to |
|---|---|---|
| GPIO17 | input, pulled up | button switch to ground, 2.2k to 3V3, 100nF to ground |
| GPIO18 | output, PWM | 220R to MOSFET gate, 10k gate to ground |
| 3V3 | supply | the 2.2k pull-up |
| GND | common | button common, MOSFET source, 12V supply ground |

Both pins are configurable in the Button tab if you need to move them.
