import os
import threading


class TouchClient:
    """TUIO 1.1 listener for AirTouch (https://github.com/jing-interactive/AirTouch) — a native
    Cinder/OpenCV app that turns a Kinect/RealSense/OpenNI depth camera into a multi-touch
    surface. AirTouch has no API of its own beyond this: it broadcasts every touch point as a
    standard TUIO 1.1 `/tuio/2Dcur` OSC bundle over UDP (127.0.0.1:3333 by default, configurable
    in AirTouch's own `include/item.def`). This client is a pure TUIO listener, so it works
    unmodified against any TUIO 1.1 source, not just AirTouch.

    Runs a background UDP server (lazy-started on enable) that keeps `_cursors` current; reads
    are instant dict lookups under a lock, no blocking I/O on the calling thread.
    """

    def __init__(self):
        self.enabled = os.getenv("TOUCH_ENABLED", "false").lower() == "true"
        self.host = os.getenv("TUIO_HOST", "127.0.0.1")
        self.port = int(os.getenv("TUIO_PORT", "3333"))
        self._cursors: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        if self.enabled:
            self._start()

    def _start(self):
        """Bind the UDP listener and start its serve loop on a daemon thread."""
        if self._server is not None:
            return
        try:
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import ThreadingOSCUDPServer
        except ImportError:
            raise RuntimeError("python-osc not installed. Run: pip install python-osc")

        dispatcher = Dispatcher()
        dispatcher.map("/tuio/2Dcur", self._handle_2dcur)
        self._server = ThreadingOSCUDPServer((self.host, self.port), dispatcher)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _handle_2dcur(self, address: str, *args) -> None:
        """Parses the three TUIO 2Dcur message shapes AirTouch sends per frame:

        - `alive s1 s2 ...` — the full set of currently-active session ids. Anything tracked
          here that's missing from this list has been lifted, so it's dropped.
        - `set s x y X Y m` — one cursor's state: session id, position (x, y, normalized 0..1),
          velocity vector (X, Y), motion acceleration (m).
        - `fseq f` — a frame sequence number; carries no per-cursor state worth keeping.

        See the TUIO 1.1 spec (https://www.tuio.org/?tuio11) for the wire format this mirrors.
        """
        if not args:
            return
        command = args[0]
        if command == "alive":
            alive_ids = set(args[1:])
            with self._lock:
                for session_id in list(self._cursors):
                    if session_id not in alive_ids:
                        del self._cursors[session_id]
        elif command == "set":
            session_id, x, y, x_velocity, y_velocity, acceleration = args[1:7]
            with self._lock:
                self._cursors[int(session_id)] = {
                    "id": int(session_id),
                    "x": x,
                    "y": y,
                    "x_velocity": x_velocity,
                    "y_velocity": y_velocity,
                    "acceleration": acceleration,
                }
        # "fseq" and any other TUIO command carry nothing worth tracking here.

    def get_active_touches(self) -> list[dict]:
        """Current touch points, normalized 0..1 over the AirTouch-calibrated surface."""
        if not self.enabled:
            raise RuntimeError(
                "Touch tracking not enabled. Set TOUCH_ENABLED=true (requires AirTouch, or "
                "another TUIO 1.1 source, broadcasting to TUIO_HOST:TUIO_PORT)."
            )
        with self._lock:
            return list(self._cursors.values())

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._server = None
            self._thread = None

    def __del__(self):
        self.close()
