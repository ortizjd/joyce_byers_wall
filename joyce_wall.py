#!/usr/bin/env python3
"""Interactive WS2811 alphabet wall. GPIO18 backend for Raspberry Pi 0–4.

Use --dry-run on any computer. Index 0 is the first pixel at DATA IN.
Default physical rows: A-H, I-Q, R-Z; wire snakes left/right/left.
Edit LETTER_TO_LED below if there are unused bulbs between letters.
"""
import argparse
import random
import signal
import string
import threading
import time
from pathlib import Path

WIRE_ORDER = "ABCDEFGH" + "QPONMLKJI" + "RSTUVWXYZ"
LETTER_TO_LED = {letter: index for index, letter in enumerate(WIRE_ORDER)}
PALETTE = [(255, 220, 0), (30, 80, 255), (255, 0, 0), (0, 200, 40)]
BUZZER_PIN = 23  # BCM GPIO23; physical header pin 16.
PIR_PIN = 24  # BCM GPIO24; physical header pin 18.
CREEPY_SONG = (196, 233, 277, 349, 311, 262, 220, 294,
               196, 233, 277, 392, 349, 294, 247, 196)


class Wall:
    def __init__(self, count=50, brightness=75, order="RGB", dry_run=False, muted=False):
        self.count = count
        self.dry_run = dry_run
        self.muted = muted
        self.action_lock = threading.Lock()
        self.strip = None
        self.gpio = None
        self.buzzer = None
        self.buzzer_active = False
        if not dry_run:
            model_path = Path("/proc/device-tree/model")
            model = model_path.read_text().strip("\x00") if model_path.exists() else ""
            if "Raspberry Pi" not in model or "Pi 5" in model or "Compute Module 5" in model:
                raise RuntimeError("This GPIO18 backend targets Pi 0–4. Use --dry-run here; Pi 5 needs a different driver setup.")
            from rpi_ws281x import PixelStrip, Color, ws
            import RPi.GPIO as GPIO
            self.color = Color
            self.strip = PixelStrip(count, 18, freq_hz=800000, dma=10,
                                   invert=False, brightness=brightness,
                                   channel=0, strip_type=getattr(ws, "WS2811_STRIP_" + order))
            self.strip.begin()
            self.gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(BUZZER_PIN, GPIO.OUT)
            self.buzzer = GPIO.PWM(BUZZER_PIN, 500)
            GPIO.setup(PIR_PIN, GPIO.IN)

    def frame(self, pixels):
        """Replace the whole frame; unspecified pixels are always off."""
        if any(not 0 <= i < self.count for i in pixels):
            raise ValueError("Pixel index outside the configured strand")
        if self.strip is not None:
            for i in range(self.count):
                self.strip.setPixelColor(i, self.color(*pixels.get(i, (0, 0, 0))))
            self.strip.show()

    def clear(self):
        self.frame({})

    def play_tone(self, frequency, duration):
        if self.muted or self.buzzer is None:
            time.sleep(duration)
            return
        self.buzzer.ChangeFrequency(frequency)
        self.buzzer.start(50)
        self.buzzer_active = True
        time.sleep(duration)
        self.buzzer.stop()
        self.buzzer_active = False

    def buzzer_test(self):
        if self.muted or self.buzzer is None:
            return
        print("Buzzer test: fixed tone", flush=True)
        self.buzzer.start(50)
        self.buzzer_active = True
        try:
            time.sleep(2)
        finally:
            self.buzzer.stop()
            self.buzzer_active = False

    def close(self):
        if self.gpio is not None:
            if self.buzzer is not None and self.buzzer_active:
                self.buzzer.stop()
                self.buzzer_active = False
            self.gpio.output(BUZZER_PIN, 1)
            self.gpio.cleanup((BUZZER_PIN, PIR_PIN))

    def motion_loop(self, message, stop_event=None):
        if self.gpio is None:
            return
        print(f"Motion mode armed on BCM GPIO{PIR_PIN}; message: {message}", flush=True)
        motion_active = False
        while stop_event is None or not stop_event.is_set():
            current_state = bool(self.gpio.input(PIR_PIN))
            if current_state and not motion_active:
                print("Motion detected", flush=True)
                with self.action_lock:
                    self.flicker(short=True)
                    self.spell(message)
                motion_active = True
            elif not current_state:
                motion_active = False
            time.sleep(0.1)

    def pir_test(self):
        if self.gpio is None:
            return
        previous_state = bool(self.gpio.input(PIR_PIN))
        print(f"PIR test armed on BCM GPIO{PIR_PIN}; initial state: {'HIGH' if previous_state else 'LOW'}", flush=True)
        heartbeat_at = time.monotonic()
        while True:
            current_state = bool(self.gpio.input(PIR_PIN))
            if current_state != previous_state:
                print(f"PIR state: {'HIGH' if current_state else 'LOW'}", flush=True)
                previous_state = current_state
            if time.monotonic() >= heartbeat_at:
                print(f"PIR sample: {'HIGH' if current_state else 'LOW'}", flush=True)
                heartbeat_at = time.monotonic() + 1
            time.sleep(0.1)

    def flicker(self, short=False):
        # Quick synchronized bursts across most of the strand, with varied intensity per bulb.
        indices = list(range(self.count))
        if self.dry_run:
            print("[random flicker burst]")
        burst_count = random.randint(8, 12) if short else random.randint(36, 64)
        for burst_index in range(burst_count):
            lit = random.sample(indices, random.randint(len(indices) // 2, len(indices)))
            self.frame({
                index: tuple(int(channel * random.uniform(0.35, 1.0))
                            for channel in PALETTE[index % len(PALETTE)])
                for index in lit
            })
            self.play_tone(
                CREEPY_SONG[burst_index % len(CREEPY_SONG)],
                random.uniform(0.08, 0.16),
            )
            self.clear()
            time.sleep(random.uniform(0.015, 0.06))

    def spell(self, message, flicker=True):
        # Normalize punctuation into pauses; accept only the physical A-Z alphabet.
        message = " ".join("".join(c if c in string.ascii_uppercase else " "
                                  for c in message.upper()).split())
        if not message:
            print("Please enter a message containing A-Z letters.")
            return
        print("Spelling:", message)
        self.clear()
        time.sleep(0.6)
        for letter in message:
            if letter == " ":
                time.sleep(0.9)
                continue
            index = LETTER_TO_LED[letter]
            color = PALETTE[(ord(letter) - ord("A")) % len(PALETTE)]
            print(f"  {letter} -> LED {index}", flush=True)
            if flicker:
                # Stutter only the target bulb so spelling remains readable.
                for _ in range(random.randint(1, 3)):
                    self.frame({index: color})
                    time.sleep(random.uniform(0.03, 0.08))
                    self.clear()
                    time.sleep(random.uniform(0.03, 0.08))
            self.frame({index: color})
            time.sleep(1.5)
            self.clear()
            time.sleep(0.35)  # Includes a visible gap between repeated letters.
        if flicker:
            self.flicker()

    def scan(self):
        for index in range(self.count):
            print(f"LED {index}", flush=True)
            self.frame({index: (255, 80, 0)})
            time.sleep(0.65)
        self.clear()

    def colors(self):
        for name, rgb in [("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)), ("BLUE", (0, 0, 255))]:
            print(f"First bulb should be {name}", flush=True)
            self.frame({0: rgb})
            time.sleep(2)
        self.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", help="Omit to type messages interactively")
    parser.add_argument("--dry-run", action="store_true", help="Print sequence without GPIO hardware")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--brightness", type=int, default=75, help="0–255; default 75 (~30%%)")
    parser.add_argument("--order", choices=["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"], default="RGB")
    parser.add_argument("--no-flicker", action="store_true")
    parser.add_argument("--scan", action="store_true", help="Light each bulb to check its index")
    parser.add_argument("--colors", action="store_true", help="Check RGB color order on first bulb")
    parser.add_argument("--buzzer-test", action="store_true", help="Play test tones on GPIO23")
    parser.add_argument("--motion", action="store_true", help="Trigger the message when PIR motion is detected")
    parser.add_argument("--pir-test", action="store_true", help="Print PIR state changes on GPIO24")
    parser.add_argument("--mute", action="store_true", help="Disable buzzer sounds")
    args = parser.parse_args()
    if not 0 <= args.brightness <= 255 or not 26 <= args.count <= 1000:
        parser.error("brightness must be 0–255 and count must be 26–1000")
    if set(LETTER_TO_LED) != set(string.ascii_uppercase) or len(set(LETTER_TO_LED.values())) != 26:
        parser.error("LETTER_TO_LED must map A-Z to 26 unique indices")
    if any(type(i) is not int or not 0 <= i < args.count for i in LETTER_TO_LED.values()):
        parser.error("LETTER_TO_LED contains an invalid index for --count")

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    interactive_mode = (args.message is None and not args.scan and not args.colors
                        and not args.buzzer_test and not args.motion and not args.pir_test)
    wall = None
    try:
        wall = Wall(args.count, args.brightness, args.order, args.dry_run,
                    muted=args.mute or interactive_mode)
        wall.clear()
        if args.scan:
            wall.scan()
        elif args.colors:
            wall.colors()
        elif args.buzzer_test:
            wall.buzzer_test()
        elif args.motion:
            wall.motion_loop(args.message or "RUN")
        elif args.pir_test:
            wall.pir_test()
        elif interactive_mode:
            stop_event = threading.Event()
            motion_thread = threading.Thread(
                target=wall.motion_loop,
                args=("RUN", stop_event),
                daemon=True,
            )
            motion_thread.start()
            print("Type a message, /flicker for lights, or /quit. Motion triggers RUN (sound muted).")
            while True:
                message = input("Joyce > ").strip()
                if message.lower() == "/quit":
                    stop_event.set()
                    break
                if message.lower() == "/flicker":
                    with wall.action_lock:
                        wall.flicker()
                elif message:
                    with wall.action_lock:
                        wall.spell(message, not args.no_flicker)
        elif args.message is not None:
            wall.spell(args.message, not args.no_flicker)
        else:
            print("Type a message, /flicker for a burst, or /quit. Ctrl+C also exits.")
            while True:
                message = input("Joyce > ").strip()
                if message.lower() == "/quit":
                    break
                if message.lower() == "/flicker":
                    wall.flicker()
                elif message:
                    wall.spell(message, not args.no_flicker)
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye from the Upside Down.")
    except (ImportError, RuntimeError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    finally:
        if wall is not None:
            try:
                wall.clear()
            finally:
                wall.close()


if __name__ == "__main__":
    main()
