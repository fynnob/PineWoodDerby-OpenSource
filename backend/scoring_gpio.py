"""
Pinewood Derby — GPIO Sensor Scoring (Module 7)
Reads lane finish sensors directly on a Raspberry Pi (or any Linux GPIO device).
Each lane has one IR sensor wired to a GPIO pin. When the beam breaks (car passes),
the pin fires a FALLING edge interrupt and time is recorded.

Wiring example (4 lanes, Raspberry Pi):
  Lane 1 → GPIO 17
  Lane 2 → GPIO 27
  Lane 3 → GPIO 22
  Lane 4 → GPIO 23
  All sensors share GND and 3.3V.

Configure lane_pins in config.json:
  "lane_pins": [17, 27, 22, 23]
"""
import time, asyncio, threading
from scoring_sensor import SensorScoring


class GPIOScoring(SensorScoring):
    def __init__(self, config: dict, broadcast_fn):
        super().__init__(config, broadcast_fn)
        self.lane_pins: list[int] = config.get("lane_pins", [17, 27, 22, 23])
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._gpio = None
        self._race_start_time: float | None = None

    def start(self, loop: asyncio.AbstractEventLoop):
        """Start GPIO listener in a background thread."""
        self._loop = loop
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
        except ImportError:
            print("[GPIO] RPi.GPIO not available — GPIO scoring disabled.")
            print("[GPIO] Install it with: pip install RPi.GPIO")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for lane_num, pin in enumerate(self.lane_pins, start=1):
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Capture lane_num in closure
            def make_callback(ln, p):
                def callback(channel):
                    self._on_sensor(ln)
                return callback
            GPIO.add_event_detect(pin, GPIO.FALLING,
                                  callback=make_callback(lane_num, pin),
                                  bouncetime=50)

        print(f"[GPIO] Listening on pins: {dict(enumerate(self.lane_pins, 1))}")

    def arm(self):
        """Call just before a heat starts to zero the timer."""
        self._race_start_time = time.monotonic()
        print("[GPIO] Race armed — waiting for sensor hits")

    def _on_sensor(self, lane: int):
        """Called from GPIO interrupt thread when a sensor fires."""
        if self._race_start_time is None:
            return
        elapsed_ms = (time.monotonic() - self._race_start_time) * 1000
        print(f"[GPIO] Lane {lane} finish: {elapsed_ms:.1f} ms")
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._handle_async(lane, elapsed_ms), self._loop
            )

    async def _handle_async(self, lane: int, time_ms: float):
        result = await self.record_hit(lane, time_ms)
        if result:
            await self.broadcast("heat_results", "INSERT", result)

    def stop(self):
        """Clean up GPIO on shutdown."""
        if self._gpio:
            self._gpio.cleanup()
            print("[GPIO] Cleanup done")
