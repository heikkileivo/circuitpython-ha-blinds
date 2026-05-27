# mqtt.py

import os, time
import asyncio
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from blink import blink, Color


async def mqtt_publish(state, topic, value):
    """
    Serialized publish wrapper used by all tasks.
    Uses Mqtt.publish() under a lock and updates last_publish on success.
    """
    async with state.mqtt_lock:
        try:
            # We trust the supervisor to keep MQTT reasonably healthy.
            state.mqtt.publish(topic, value)

            return True
        except Exception as e:
            print("MQTT publish failed in wrapper:", e)
            # Supervisor will see on_disconnected / stall and reconnect.
            return False

class Mqtt:
    """
    Thin MQTT wrapper with a clear state machine:
    - connect() : single connection attempt (no loops, no recursion)
    - disconnect() : clean disconnect
    - publish() : single publish; on error, flags disconnection

    External code (e.g. mqtt_supervisor) is responsible for:
    - retrying connect() on failure
    - deciding when to reconnect based on stalls / timeouts
    """

    def __init__(self, on_connect_callback=None, on_message_callback=None):
        self.running = False
        self.last_connect = 0
        self.last_publish = 0
        self.client = None
        self.pool = None
        self.ssl_context = None
        self.lock = asyncio.Lock()

        # State signals
        self.on_connected = asyncio.Event()
        self.on_disconnected = asyncio.Event()
        self.supervisor_stopped = asyncio.Event()

        # Stats / diagnostics
        self.reconnects = -1     # first successful connect -> 0
        self.last_error = None

        # External callbacks
        self._on_connect_callback = on_connect_callback
        self._on_message_callback = on_message_callback

        # Deferred post-connect work (discovery publish + subscribe). Set by the
        # connect callback, run by the supervisor AFTER connect() returns - never
        # inside the CONNACK handler, where a slow/failed publish or subscribe
        # would trigger MiniMQTT's connect-retry loop.
        self._pending_on_connect = False

    # ---------- internal helpers ----------

    def init(self):
        """
        Initialize socket pool and ssl context.
        """
        import wifi, socketpool, ssl
        if self.pool is not None and self.ssl_context is not None:
            return  # already initialized
        self.pool = socketpool.SocketPool(wifi.radio)
        self.ssl_context = ssl.create_default_context()

    def _build_client(self):
        """
        (Re)create the MiniMQTT client with proper callbacks.
        """
        broker = os.getenv("mqtt_broker")
        port = os.getenv("mqtt_port")
        user = os.getenv("mqtt_user")
        pwd = os.getenv("mqtt_pwd")

        self.client = MQTT.MQTT(
            broker=broker,
            port=port,
            username=user,
            password=pwd,
            socket_pool=self.pool,
            ssl_context=self.ssl_context,
            keep_alive=120,
            socket_timeout=1,
        )

        on_message_cb = self._on_message_callback

        # Callbacks are purely reactive: they update state, do not loop.
        def _connected(client, userdata, flags, rc):
            print("MQTT: connected")
            self.reconnects += 1
            self.on_disconnected.clear()
            self.on_connected.set()
            asyncio.create_task(blink(Color.GREEN, 3))
            # Defer the heavy on_connect work (discovery publish + subscribe).
            # Running it here, inside connect()'s CONNACK handler, lets a slow or
            # failed publish/subscribe abort the connect and loop forever.
            self._pending_on_connect = True

        def _disconnected(client, userdata, rc):
            print("MQTT: disconnected (cb)")
            self.on_connected.clear()
            self.on_disconnected.set()
            asyncio.create_task(blink(Color.ORANGE, 5))

        def _message(client, topic, message):
            print(f"MQTT: message on {topic}: {message}")
            asyncio.create_task(blink(Color.GREEN, 2))
            if on_message_cb:
                on_message_cb(client, topic, message)

        self.client.on_connect = _connected
        self.client.on_disconnect = _disconnected
        self.client.on_message = _message

    # ---------- public API used by supervisor / tasks ----------

    async def connect(self):
        """
        Single connection attempt.

        - Ensures Wi-Fi is up.
        - Builds MQTT client if necessary.
        - Calls blocking client.connect().
        - On success: on_connected is set.
        - On failure: on_disconnected is set and exception is re-raised.

        This function DOES NOT loop or recurse. The caller (supervisor)
        decides when to retry.
        """
        print("MQTT: connect() called")

        # Fast path: already connected
        if self.client and self.client.is_connected():
            print("MQTT: already connected")
            self.on_connected.set()
            self.on_disconnected.clear()
            return True

        # Clear old state
        self.on_connected.clear()
        # Don't clear on_disconnected here; it signals "needs connect".

        # Build / rebuild client
        if self.client:
            try:
                self.client.reconnect()
                return True
            except Exception as e:
                self.last_error = e
                print(f"MQTT: reconnect failed: {repr(e)}")
                self.on_connected.clear()
                self.on_disconnected.set()
                return False
        else:
            self._build_client()

            try:
                print("MQTT: connecting to broker...")
                self.client.connect()  # blocking, but short
                return True
            except Exception as e:
                self.last_error = e
                print(f"MQTT: connect failed: {repr(e)}")
                self.on_connected.clear()
                self.on_disconnected.set()
                return False

    def publish(self, topic, msg):
        """
        Single publish attempt.

        - If not connected: raises RuntimeError.
        - On publish error: marks as disconnected and re-raises.
        - Does NOT reconnect by itself. Supervisor will notice via last_publish
          / events and handle reconnection.
        """
        if not self.client or not self.client.is_connected():
            print("MQTT: publish called while not connected")
            self.on_connected.clear()
            self.on_disconnected.set()
            raise RuntimeError("MQTT not connected")

        try:
            self.client.publish(topic, msg)
            self.last_publish = time.monotonic()
        except Exception as e:
            self.last_error = e
            print(f"MQTT: publish failed: {repr(e)}")
            # Mark as disconnected so supervisor wakes up
            self.on_connected.clear()
            self.on_disconnected.set()
            raise

    def subscribe(self, topic):
        """Subscribe to an MQTT topic."""
        if self.client and self.client.is_connected():
            self.client.subscribe(topic)

    def _run_pending_on_connect(self):
        """Run the deferred discovery-publish + subscribe AFTER a successful
        connect, outside connect()'s CONNACK handler. If it fails it stays
        pending and retries on the next supervisor pass, rather than triggering
        a reconnect loop."""
        if not self._pending_on_connect:
            return
        if not (self.client and self.client.is_connected()):
            return
        if self._on_connect_callback:
            try:
                self._on_connect_callback(self.client)
                self._pending_on_connect = False
            except Exception as e:
                print(f"on_connect work failed, will retry: {repr(e)}")
        else:
            self._pending_on_connect = False

    def disconnect(self):
        """
        Clean disconnect. Does not loop or reconnect.
        """
        print("Disconnecting mqtt client...")
        if self.client:
            try:
                print("MQTT: disconnect() called")
                self.client.disconnect()
            except Exception as e:
                print(f"MQTT: disconnect failed: {repr(e)}")
            finally:
                self.on_connected.clear()
                self.on_disconnected.set()
                self.client = None
                self.ssl_context = None
                self.pool = None
        else:
            print("Already disconnected.")

    def start_supervisor(self):
        """
        Start the mqtt supervisor loop.
        """
        print("MQTT: starting supervisor")
        self.running = True
        asyncio.create_task(self.mqtt_supervisor())

    async def stop_supervisor(self):
        """
        Stop the mqtt supervisor loop.
        """
        print("MQTT: stopping supervisor")
        self.running = False

    async def mqtt_supervisor(self):
        """
        Monitor MQTT connection health and reconnect as necessary.
        """
        STALL_TIMEOUT = 180     # seconds without publish -> reconnect
        RECONNECT_DELAY = 15     # seconds between retries
        MAX_FAILURES = 5       # max consecutive failures before backoff

        failures = 0

        while self.running:
            self._run_pending_on_connect()
            print("MQTT supervisor is alive.")
            now = time.monotonic()
            if failures >= MAX_FAILURES:
                print("MQTT supervisor: too many failures, reconnecting...")
                failures = 0
                self.disconnect()

            if self.last_publish + STALL_TIMEOUT < now:
                print("MQTT supervisor: publish stall detected, reconnecting...")
                self.last_publish = now
                self.disconnect()
                failures = 0

            if self.client:
                try:
                    self.client.ping()
                    print(f"MQTT supervisor: ping successful. Last publish = { self.last_publish}")
                except Exception as e:
                    print("MQTT supervisor: ping failed:", e)
                    failures += 1
            else:
                print("MQTT supervisor: initializing MQTT client.")
                self.init()
                try:
                    if await self.connect():
                        self.last_connect = now
                        self.last_publish = now
                        failures = 0
                        self._run_pending_on_connect()
                    else:
                        failures += 1
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue

                except Exception as e:
                    print("MQTT supervisor: connecting failed:", e)
                    failures += 1
                    continue
            # Everything fine, chill a bit
            await asyncio.sleep(10)
        print("MQTT Supervisor is stopped.")
