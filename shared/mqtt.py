# mqtt.py

import time
import env
import asyncio
import adafruit_connection_manager
from bounded_mqtt import BoundedMQTT

# A loop(T) call lasts T to T + socket_timeout. One that takes longer than
# that by more than this, in seconds, is logged as SLOW LOOP.
SLOW_LOOP_MARGIN_S = 0.5

# The supervisor sleeps this long, in seconds, between its blocking steps.
YIELD_S = 0.1


async def mqtt_publish(state, topic, value):
    """
    Serialized publish wrapper used by all tasks.
    Uses Mqtt.publish() under a lock and updates last_publish on success.
    """
    async with state.mqtt.lock:
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
    - connect() : single connection attempt on a new client (no loops, no recursion)
    - disconnect() : throw the client away and free its sockets
    - publish() : single publish; on error, flags disconnection
    - loop() : services incoming messages (opt-in); on error, flags disconnection

    External code (e.g. mqtt_supervisor) is responsible for:
    - retrying connect() on failure
    - deciding when to reconnect based on stalls / timeouts
    """

    def __init__(self, on_connect_callback=None, on_message_callback=None,
                 client_id=None, socket_timeout=1, recv_timeout=10,
                 connect_retries=5, echo_topic=None, echo_timeout=45,
                 paused=None, availability_topic=None):
        """
        Everything after the callbacks is opt-in; the defaults are what the
        meters have always used.

        - client_id: a fixed MQTT client ID. None lets MiniMQTT pick a random one.
        - socket_timeout, recv_timeout: passed to MiniMQTT, in seconds.
        - connect_retries: attempts per connect(), passed to MiniMQTT. Past
          the first, MiniMQTT sleeps 2, 4, 8 and 16 s between attempts after
          MQTT-level errors, and that sleep blocks the asyncio loop.
        - echo_topic: a topic the device publishes to itself, not retained.
          The supervisor subscribes to it on every connect and rebuilds the
          client when loop() has run for echo_timeout seconds with nothing
          arriving on it. Messages only arrive through loop(), so a device
          that sets it must call loop() regularly.
        - paused: a callable. While it returns True, the supervisor neither
          rebuilds nor connects, because both block the asyncio loop.
        - availability_topic: every new client sets a retained "offline" last
          will on it before connecting, and the on-connect work publishes a
          retained "online" to it first, on every connect. Pair it with a
          fixed client_id: Mosquitto then publishes a stale session's will
          on takeover, before the new CONNACK, so it can't land after
          "online".
        """
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

        # Client options
        self._client_id = client_id
        self._socket_timeout = socket_timeout
        self._recv_timeout = recv_timeout
        self._connect_retries = connect_retries
        self._paused = paused
        self._availability_topic = availability_topic

        # Liveness echo. last_echo is the time.monotonic() of the last echo
        # received, across rebuilds; None until the first one. last_loop is
        # the time.monotonic() at which the last loop() call ended.
        self._echo_topic = echo_topic
        self._echo_timeout = echo_timeout
        self.last_echo = None
        self.last_loop = 0

        # Deferred post-connect work (discovery publish + subscribe). Set by the
        # connect callback, run by the supervisor AFTER connect() returns - never
        # inside the CONNACK handler, where a slow/failed publish or subscribe
        # would trigger MiniMQTT's connect-retry loop.
        self._pending_on_connect = False

    # ---------- internal helpers ----------

    def init(self):
        """
        Initialize socket pool and ssl context, once. Every client is built on
        this same pool: a rebuild frees the pool's sockets instead of
        replacing the pool.
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
        broker = env.text("mqtt_broker")
        port = env.integer("mqtt_port")
        user = env.text("mqtt_user")
        pwd = env.text("mqtt_pwd")

        self.client = BoundedMQTT(
            broker=broker,
            port=port,
            username=user,
            password=pwd,
            client_id=self._client_id,
            socket_pool=self.pool,
            ssl_context=self.ssl_context,
            keep_alive=60,
            socket_timeout=self._socket_timeout,
            recv_timeout=self._recv_timeout,
            connect_retries=self._connect_retries,
        )
        if self._availability_topic:
            # The will doesn't survive a rebuild, so each new client sets it.
            # Topic and message positional, retain as a keyword: MiniMQTT
            # 7.10.1 renamed the message argument and swapped qos and retain.
            self.client.will_set(self._availability_topic, "offline", retain=True)

        on_message_cb = self._on_message_callback
        echo_topic = self._echo_topic

        # Callbacks are purely reactive: they update state, do not loop,
        # and leave the LED alone (#116).
        def _connected(client, userdata, flags, rc):
            print("MQTT: connected")
            self.reconnects += 1
            self.on_disconnected.clear()
            self.on_connected.set()
            # Defer the heavy on_connect work (discovery publish + subscribe).
            # Running it here, inside connect()'s CONNACK handler, lets a slow or
            # failed publish/subscribe abort the connect and loop forever.
            self._pending_on_connect = True

        def _disconnected(client, userdata, rc):
            print("MQTT: disconnected (cb)")
            self.on_connected.clear()
            self.on_disconnected.set()

        def _message(client, topic, message):
            if topic == echo_topic:
                # The supervisor's own topic: record it, don't route it.
                self.last_echo = time.monotonic()
                return
            print(f"MQTT: message on {topic}: {message}")
            if on_message_cb:
                on_message_cb(client, topic, message)

        self.client.on_connect = _connected
        self.client.on_disconnect = _disconnected
        self.client.on_message = _message

    def _close_client(self):
        """
        Throw the client away: disconnect it, then free every socket on the
        pool, swallowing all errors. Leaves self.client None and the pool
        ready for a new client.

        The order matters. connection_manager_close_all() unregisters the
        client's live socket, so a disconnect() after it would raise
        RuntimeError("Socket not managed"). Freeing the sockets also releases
        a stale one that disconnect() left open, so the next client can
        connect on the same pool, on MiniMQTT 7.10.0 as well as 8.1.0.
        """
        try:
            print("MQTT: disconnect() called")
            self.client.disconnect()
        except Exception as e:
            print(f"MQTT: disconnect failed: {repr(e)}")
        try:
            adafruit_connection_manager.connection_manager_close_all(self.pool)
        except Exception as e:
            print(f"MQTT: closing sockets failed: {repr(e)}")
        self.client = None

    def _echo_lost(self):
        """
        True when an echo topic is set and loop() has run for echo_timeout
        seconds with nothing arriving on it, counted from the last echo or the
        last connect, whichever is later.

        It's measured up to the end of the last loop() call, not up to now.
        Echoes only arrive through loop(), so while the device doesn't call it,
        a quiet echo topic says nothing about the link.
        """
        if not self._echo_topic:
            return False
        since = self.last_connect
        if self.last_echo is not None and self.last_echo > since:
            since = self.last_echo
        return since + self._echo_timeout < self.last_loop

    # ---------- public API used by supervisor / tasks ----------

    async def connect(self):
        """
        Single connection attempt.

        - Throws away any old client and builds a new one on the same pool.
        - Calls blocking client.connect().
        - On success: on_connected is set.
        - On failure: on_disconnected is set and False is returned.

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

        # A client that isn't connected is never reused: rebuild it.
        if self.client:
            self._close_client()

        try:
            self._build_client()
            print("MQTT: connecting to broker...")
            self.client.connect()  # blocking, but short
            return True
        except Exception as e:
            self.last_error = e
            print(f"MQTT: connect failed: {repr(e)}")
            self.on_connected.clear()
            self.on_disconnected.set()
            return False

    def publish(self, topic, msg, retain=False):
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
            self.client.publish(topic, msg, retain=retain)
            self.last_publish = time.monotonic()
        except Exception as e:
            self.last_error = e
            print(f"MQTT: publish failed: {repr(e)}")
            # Mark as disconnected so supervisor wakes up
            self.on_connected.clear()
            self.on_disconnected.set()
            raise

    async def loop(self, timeout):
        """
        Service incoming messages for about `timeout` seconds, which must be
        at least socket_timeout. The call blocks the asyncio loop meanwhile.

        Returns True on success. Does nothing and returns False while not
        connected or once disconnection is flagged. Any exception flags
        disconnection for the supervisor to rebuild, and returns False.
        """
        async with self.lock:
            if self.on_disconnected.is_set() or not (self.client and self.client.is_connected()):
                return False
            start = time.monotonic_ns()
            try:
                self.client.loop(timeout)
                self.last_loop = time.monotonic()
                return True
            except Exception as e:
                self.last_error = e
                print(f"MQTT: loop failed: {repr(e)}")
                self.on_connected.clear()
                self.on_disconnected.set()
                return False
            finally:
                elapsed = (time.monotonic_ns() - start) / 1_000_000_000
                if elapsed > timeout + self._socket_timeout + SLOW_LOOP_MARGIN_S:
                    print(f"MQTT: SLOW LOOP {elapsed:.2f} s")

    def subscribe(self, topic):
        """Subscribe to an MQTT topic."""
        if self.client and self.client.is_connected():
            self.client.subscribe(topic)

    def _run_pending_on_connect(self):
        """Run the deferred on-connect work AFTER a successful connect,
        outside connect()'s CONNACK handler: the "online" publish, the
        connect callback (discovery publish + subscribe), then the echo
        subscribe. If it fails it stays pending and retries on the next
        supervisor pass, rather than triggering a reconnect loop."""
        if not self._pending_on_connect:
            return
        if not (self.client and self.client.is_connected()):
            return
        try:
            if self._availability_topic:
                self.client.publish(self._availability_topic, "online", retain=True)
            if self._on_connect_callback:
                self._on_connect_callback(self.client)
            if self._echo_topic:
                self.client.subscribe(self._echo_topic)
            self._pending_on_connect = False
        except Exception as e:
            print(f"on_connect work failed, will retry: {repr(e)}")

    def disconnect(self):
        """
        Clean disconnect: throws the client away and frees its sockets,
        keeping the pool. Does not loop or reconnect.
        """
        print("Disconnecting mqtt client...")
        if self.client:
            self._close_client()
            self.on_connected.clear()
            self.on_disconnected.set()
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

        No active PINGREQ probe: adafruit_minimqtt's ping() runs a blocking
        read loop for up to keep_alive seconds waiting for PINGRESP, and since
        the meters never call client.loop(), on a half-dead socket that wait
        freezes the whole asyncio loop (sampling stops, the watchdog goes
        unfed). Instead the regular ~10s uptime publishes keep the broker
        keep-alive fresh; a dead socket then surfaces as a publish error that
        sets on_disconnected, and we rebuild the client. STALL_TIMEOUT is the
        backstop for a broker that goes away without sending us anything.

        A successful publish doesn't prove the link is alive: it only fills
        lwIP's send buffer. With an echo topic, the supervisor also rebuilds
        when the device's own publishes stop coming back.
        """
        STALL_TIMEOUT = 90       # seconds without a successful publish -> rebuild
        RECONNECT_DELAY = 15     # seconds between connect attempts

        while self.running:
            if self._paused and self._paused():
                # Rebuilding, connecting and the on-connect work all block.
                await asyncio.sleep(1)
                continue

            self._run_pending_on_connect()
            now = time.monotonic()

            stall = self.client and (self.last_publish + STALL_TIMEOUT < now)
            echo_lost = self.client and self._echo_lost()
            if self.client and (self.on_disconnected.is_set() or stall or echo_lost):
                if self.on_disconnected.is_set():
                    reason = "disconnect flagged"
                elif stall:
                    reason = "publish stall"
                else:
                    reason = "echo timeout"
                print(f"MQTT supervisor: {reason}, rebuilding client.")
                async with self.lock:
                    self.disconnect()
                # Rebuilding, connecting and the on-connect work each block.
                # Yield between them, so a device that feeds its watchdog from
                # a task only has to fit each one, not all three, in the timeout.
                # A short sleep, not sleep(0), lets tasks that are already due
                # run first.
                await asyncio.sleep(YIELD_S)

            if not self.client:
                print("MQTT supervisor: connecting MQTT client.")
                self.init()
                try:
                    async with self.lock:
                        connected = await self.connect()
                    if connected:
                        # After the blocking connect, so a slow one doesn't
                        # eat into the echo timeout.
                        self.last_connect = time.monotonic()
                        self.last_publish = now
                        await asyncio.sleep(YIELD_S)
                        self._run_pending_on_connect()
                    else:
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue
                except Exception as e:
                    print("MQTT supervisor: connecting failed:", e)
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue
            elif self._echo_topic:
                print(f"MQTT supervisor: alive. Last publish = {self.last_publish}, last echo = {self.last_echo}")
            else:
                print(f"MQTT supervisor: alive. Last publish = {self.last_publish}")

            await asyncio.sleep(10)
        print("MQTT Supervisor is stopped.")
