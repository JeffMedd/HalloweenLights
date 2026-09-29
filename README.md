# Halloween window display

A slot machine across eight bay window sashes. A visitor presses the illuminated
dome button on the fence post, the button starts flashing, the eight sections
hop randomly and slow to a stop on one of them, which flashes and plays its own
sound. Between rounds the windows run a playlist of Halloween effects.

Everything runs on a Raspberry Pi 5. The two WLED controllers, one per floor,
are dumb pixel pushers fed over Wi-Fi, so nothing needs cabling between floors.

## How it hangs together

```
   dome button ──► Pi 5 ──► engine (50 fps) ──► DDP over Wi-Fi ──► WLED, ground   ──► DL1 DL2 DR1 DR2
                     │                     └──► DDP over Wi-Fi ──► WLED, upstairs ──► UL1 UL2 UR1 UR2
                     ├──► button lamp (PWM through a MOSFET)
                     ├──► audio: ambient bed, tick, sting, landing stab, winner sound
                     └──► web interface on port 8080
```

The Pi models the house pixel by pixel: every sash, every strip round it, and
where each pixel physically sits. The game, the idle effects and the test tools
all draw onto that model, and the Pi streams the result to both controllers
over DDP, WLED's realtime protocol.

A round runs through six states, all configurable:

| State | What happens |
|---|---|
| `idle` | the idle playlist, lamp glowing and slowly pulsing |
| `cycle` | sections hop, slowing on an ease-out curve, lamp flashing fast |
| `flash` | the winner flashes |
| `hold` | the winner sits solid while its sound plays out |
| `fade` | the winner fades back into the idle effect |
| `cooldown` | presses ignored, so nobody can retrigger mid-finale |

## Install on the Pi

```bash
mv ~/Downloads/halloween-window-display ~/halloween-window-display
cd ~/halloween-window-display
bash install.sh
loginctl enable-linger $USER
sudo usermod -aG gpio $USER
sudo systemctl restart halloween-display
```

Run `install.sh` as yourself, not with sudo; it asks for sudo where it needs it.
The web interface is then at `http://<pi-address>:8080`.

### Updating an existing install

Your settings live in `config.json`, which is not in the download, so copying a
new version over the top keeps them. Configs from earlier versions are
upgraded automatically on load.

```bash
cd ~/Downloads
unzip -o halloween-window-display.zip
cp -r halloween-window-display/* ~/halloween-window-display/
sudo apt-get install -y python3-numpy python3-pygame
sudo systemctl restart halloween-display
~/halloween-window-display/.venv/bin/python ~/halloween-window-display/tools/selftest.py
```

Useful commands:

```bash
sudo systemctl status halloween-display
journalctl -u halloween-display -f
```

## Set it up in this order

1. **[docs/wled-setup.md](docs/wled-setup.md)**: configure both controllers,
   including the fallback preset that keeps the windows lit if the Pi dies.
2. **[docs/wiring.md](docs/wiring.md)**: the button switch and lamp.
3. **Hardware tab**: the two controller IP addresses, then **Check controllers**.
   It reports whether each is reachable, its LED count against what the Pi
   sends, its Wi-Fi signal, and whether the current limiter is on.
4. **Test tab**: work through the wiring checks below.
5. **Sections tab**: colours, names, winner sounds, and the strip layout if the
   pixel walk showed your wiring differs from the default.
6. **Effects tab**: build the playlist that runs between rounds.
7. **Timing** and **Audio** tabs: pacing and sound.

## The web interface

The house is drawn as it looks from the street: the upstairs bay above the
ground bay, the left window's sashes 1 (upper) and 2 (lower) beside the right
window's. Every pixel is drawn in the colour it is showing.

- **Play**: live view, the trigger button, what the playlist is doing, and a
  status strip. Click a sash to light it for five seconds.
- **Test**: take over the display for wiring and testing (below).
- **Effects**: preview effects, tune them, and build playlists (below).
- **Sections**: per-sash colour, flash colour, winner sound, odds weight, size,
  and the strips in data order.
- **Timing**: the cycle and the finale.
- **Audio**, **Button**, **Hardware**: as they say.

Changes to settings need **Save changes** at the top. Previews and test mode
do not.

## Test mode

Anything you switch on in the Test tab puts the display into test mode. The
game is paused, the idle effects stop, and the button only reports presses. A
banner at the bottom of the screen says so, with an exit button, and it exits
by itself after 30 minutes with nothing touched.

**Switching things on and off.** Everything latches: it stays lit until you
switch it off.

- Click a **strip** on the house drawing to toggle that one strip.
- Click **inside a sash** to toggle the whole sash.
- The buttons toggle the **whole house**, a **bay**, a **window**, a **sash**,
  or a **single strip** (T, R, B, L for top, right, bottom, left). A group that
  is partly lit shows a dashed outline; pressing it lights the rest.
- The **brush** sets the colour, brightness and pattern for whatever you switch
  on next. **Apply to everything lit** restyles what is already on.

Patterns: solid; every other pixel; **first pixel green, last pixel red**, which
shows the data direction of each strip at a glance; a brightness ramp along the
strip; a single-pixel chase; and a once-a-second blink.

**A/B compare: do you need channels?** Fit an aluminium channel and diffuser to
one sash, say UL1, and leave UL2 bare. Pick UL1 as A and UL2 as B, same colour,
and press **Show A and B**. Then go across the road after dark. Try:

- Solid at 100%: how even does each look as a line of light?
- Every other pixel: exaggerates the dotting, so the difference is obvious.
- Both at about 30%: roughly the brightness of the idle effects, which is where
  most people will see the display most of the time.

Each side has its own colour, brightness and pattern, so you can also compare
two brightnesses or two colours on the same kind of strip.

**Wiring sequences.** Each runs across everything, or only across what is lit
if you tick the box, and a line under the house says what you should be seeing.

| Sequence | Checks |
|---|---|
| Pixel walk | Data direction and pixel count, one pixel at a time |
| Strips one at a time | Which physical strip is which |
| Sashes one at a time | Controller outputs and section order |
| Colour order check | Red, green, blue, white: catches a wrong colour order |
| Brightness ramp | Smooth dimming, no flicker |
| Floor sync flash | Both floors flash together, so Wi-Fi timing is even |
| Full white load test | The supplies hold at full load; watch the far ends for a yellow tinge from voltage drop |

**Button and lamp.** Presses are counted on screen and flash the lamp, which
checks the switch wiring from the post without starting a round. A slider
drives the lamp directly.

**Power estimate.** Live watts and amps per floor from what is being sent,
against each supply's rating. Full white is about 162W per floor with 52 pixels
per sash at 0.78W each.

## Effects and playlists

Sixteen animated effects plus solid and off. Every effect knows where each pixel
sits on the house, so the spatial ones really move across the windows.

| Effect | What it does |
|---|---|
| Breathe | Slow pulse, each window a beat behind the last |
| Candlelight | Warm restless flicker |
| Fireflies | Dim glow with points fading in and out |
| Lightning storm | Stormy sky; strikes hit a sash, a window, a floor or the house, with a real multi-flash decay |
| Heartbeat | Lub-dub in blood red, rippling out from the middle of the house |
| Ghost drift | Soft pale shapes wandering window to window |
| Hellfire | Flames rise from the bottom of every sash, embers at the top |
| Toxic cauldron | Green simmer, bubbles rising up the sides and popping at the top |
| Marquee chase | Bands of colour marching round each frame |
| Colour wave | The palette flowing across the house, four directions |
| Eyes in the dark | Pairs of eyes open along the window edges, blink, and close |
| Searchlight | A beam sweeping across, or a lighthouse above the roof |
| Haunted tour | A comet round each sash in turn, snaking round the house |
| Poltergeist | Windows snapping on and off; now and then the whole house stutters |
| Blood drip | Ooze along the tops, drips running down the sides to pool at the bottom |
| Bat flight | Dark bat shapes flapping across a glowing sky |

**Previewing.** Click an effect and it plays on the drawing of the house, not
on the house itself. Every setting has a control; changes show in the preview
straight away. **Show on the house** puts it on the real windows, until you go
back or for a set time. **Preview playlist** plays the whole playlist on the
drawing, crossfades and all.

**Playlists.** Add the effect you are previewing with its settings, how long it
plays and how long it fades into the next. Reorder, switch items on and off,
click an item's name to edit it, shuffle, and keep several playlists. Two come
ready made: *Halloween night* and *Quiet glow*. **Use between rounds** picks the
one that plays between games. **Idle brightness** dims all of them together.

During a round the playlist keeps running behind the game, dimmed by the
*Losing sections during a round* setting on the Timing tab.

## Sounds

`tools/make_sounds.py` synthesises a starter set: a tick, a button sting, a
landing stab, a looping ambient bed and eight winner sounds. They are crude but
they let you tune levels and timing on day one. Replace them by uploading real
files in the Audio tab. WAV gives the tightest timing.

**Latency offset.** Every sound fires ahead of its visual cue by this amount.
Wired output needs 0; Bluetooth usually needs 150 to 250ms. Because nothing can
play before the press, the lights are held back by the offset instead, so the
sting sounds on the press and every tick lands on its hop.

## Testing without any hardware

```bash
python3 tools/selftest.py                  # 130-odd offline checks
python3 -m display --no-audio              # the whole app, web interface and all
python3 tools/ddp_listen.py --seconds 5    # watch the pixel data
```

The self test covers the geometry, DDP packing and chunking, colour order and
RGBW, the power estimate, every effect, the playlist runner and crossfades,
test mode patterns and sequences, config migration, the game state machine and
the audio schedule.

## Configuration notes

`config.json` is written by the web interface. Hand editing works too; the app
validates and clamps everything on load.

- **Strips** are listed per sash in data order, each with its side, pixel count
  and whether it runs against the clockwise direction. The default is four
  strips of 13 running clockwise from the top left. This only changes the
  drawing, the spatial effects and the strip tests, never which pixel is which
  on the wire; if the pixel walk shows a different order, change it here.
- **Section pixel totals** must match WLED's LED count per output: 52 by default.
- **`timing.steps: 0`** fits the hop count to the cycle length. A 5 second cycle
  gives 18 hops from 68 to 679ms.
- **`render.gamma`** makes dim colours very dim: at gamma 2.2 an input of 5%
  comes out as zero. Raise brightness rather than lowering gamma.

## Troubleshooting

**No lights at all.** Hardware tab, Check controllers. Then make sure WLED's
brightness is 255 and its current limiter is off.

**Some sections lit, some not.** Almost always the LED output order in WLED not
matching the section order in the Hardware tab. Use *Sashes one at a time*.

**Wrong colours.** Run the *Colour order check*, then fix the order in WLED.

**A strip looks back to front on the drawing.** Run the *Pixel walk*, then tick
*Reversed* for that strip on the Sections tab.

**Stutter.** Check `fps` and `late_frames` on the Hardware tab. If fps holds it
is Wi-Fi; drop the frame rate to 30. *Floor sync flash* shows whether one floor
lags the other.

**Button does nothing.** In test mode, press it and watch the counter. Nothing
means wiring; the Button card on the Play tab shows `mock` if the Pi could not
claim the GPIO pin, usually missing `gpio` group membership.

**No sound.** The Audio card shows the mixer status. On a system service the
usual cause is `XDG_RUNTIME_DIR` or `enable-linger`.

**Everything lit but nothing happening.** That is the WLED fallback preset: the
Pi stopped streaming. Check the service.

## Layout

```
display/
  config.py       schema, defaults, validation, migration, atomic save
  geometry.py     where every pixel is; groups; the house layout
  effects.py      the idle effects and the cycle pacing curve
  playlist.py     playlist runner with crossfades
  testmode.py     latching strip control, patterns, wiring sequences
  diagnostics.py  asks each WLED controller how it is getting on
  ddp.py          gamma, colour order, RGBW, packing and output
  game.py         the engine: game, idle, test mode, preview, frame loop
  audio.py        pygame mixer, four reserved channels
  button.py       GPIO switch and PWM lamp, with a mock fallback
  server.py       Starlette app: REST, websocket, static UI
  web/            the interface; house.js draws the windows
tools/            make_sounds, apply_default_sounds, ddp_listen, selftest
docs/             wled-setup, wiring
```
