# WLED setup, one controller per floor

The Pi does all the thinking. Each GLEDOPTO controller is configured once as a
plain pixel pusher and then left alone. Do the ground floor first, prove it,
then repeat for upstairs.

## 1. Flash and join the network

1. Flash WLED to the GLEDOPTO GL-C-017WL-D (an ESP32 board) using the web
   installer at install.wled.me, or over the air if it already runs WLED.
2. Join it to your Wi-Fi, then give it a **static IP** — best done as a DHCP
   reservation on the router against its MAC address, so a router reboot never
   moves it. Note the address; it goes in the Hardware tab of the display's web
   interface.
3. Name it in **Config, User Interface, Server description**: `ground` and
   `upstairs`. It makes the WLED app far easier to live with.

## 2. LED outputs

**Config, LED Preferences.**

Add four outputs and set them in the order below. This order is what decides
which section is which, because DDP addresses one flat run of pixels per
controller and the Pi assumes output 1 is the first section in its list.

| Output | GPIO | Length | Ground floor | Upstairs |
|---|---|---|---|---|
| 1 | 16 | 52 | DL1 | UL1 |
| 2 | 12 | 52 | DL2 | UL2 |
| 3 | 4  | 52 | DR1 | UR1 |
| 4 | 2  | 52 | DR2 | UR2 |

- Type: **WS281x** (the budget build strip is WS2811 at 24V).
- Colour order: **RGB** to start with. If a test section comes up the wrong
  colour, fix it here rather than in the Pi, then leave the Pi's colour order
  on RGB.
- Total LEDs should read **208**.
- **Max current: 0 mA** (or untick the limiter). Full brightness is a firm
  requirement for this build and the PSU is sized for it; leaving the limiter
  on will quietly dim the whole display.
- **Brightness: 255.** WLED's master brightness still applies on top of
  realtime data, so anything less scales everything the Pi sends.

## 3. Realtime and network

**Config, Sync Interfaces.**

- **DDP: enabled**, port **4048** (the default).
- **Realtime timeout: 5000 ms.** Long enough that a hiccup does not drop out
  of realtime mid-round, short enough to notice a dead Pi.
- **Force max brightness during realtime: off.** The Pi does its own
  brightness and gamma; letting WLED force it as well double-corrects.
- Turn **E1.31 / Art-Net off** unless you use them for something else. Two
  realtime sources fighting looks like flicker and is miserable to diagnose.
- **Receive UDP sync: off.** The two controllers must not try to mirror each
  other, they are showing different sections.

## 4. The fallback preset

DDP means WLED shows nothing of its own while the Pi is streaming. If the Pi
dies mid-evening the realtime timeout expires and WLED falls back to whatever
it was doing last, which by default is off. Give it something better:

1. Set a gentle static look you are happy to leave running: solid orange at
   about 40% brightness works.
2. Save it as **preset 1** and tick **Include brightness**.
3. **Config, LED Preferences, Apply preset at boot: 1.**

Now a Pi failure degrades to "windows lit but not playing" rather than a dark
house, and a power cut recovers to the same.

## 5. Prove it before the Pi is involved

In the WLED UI, set a solid colour and check all four sections light. Then from
the Pi:

```bash
curl -X POST -H 'Content-Type: application/json' \
     -d '{"seconds":10}' http://<pi>:8080/api/test/all
```

Every section should come up in its own colour. If one is missing, it is
almost always the output order or a data line, not the software. If a section
shows the wrong colour, fix the colour order in WLED.

## Notes

- **Pixel counts must agree.** The Pi sends 4 x 52 = 208 pixels per controller.
  If WLED is configured for fewer, the surplus is discarded silently and your
  sections will be offset. The Hardware tab shows what the Pi is sending.
- **Wi-Fi matters more than usual.** 50 frames a second of UDP to two ESP32s is
  not much bandwidth but it is unforgiving of a weak signal. If the upstairs
  bay is marginal, drop the frame rate to 30 before you start chasing ghosts.
- **No cabling between floors** is the whole point of this arrangement: the two
  controllers only ever talk to the Pi, never to each other.
