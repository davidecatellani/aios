"""Mouse e tastiera che arrivano dal visore, «premuti» su questo PC.

- Il puntatore va nel punto esatto dello schermo trasmesso: con Hyprland gli si dice dove andare dal suo
  socket (`dispatch movecursor`, senza avviare programmi: meno di un millisecondo); altrove con un
  dispositivo virtuale assoluto (come la tavoletta delle macchine virtuali).
- Tasti, pulsanti e rotellina passano da un dispositivo virtuale uinput: per il sistema è una tastiera e
  un mouse veri, quindi funzionano in tutti i programmi e con la disposizione della tastiera di questo PC.
  /dev/uinput è accessibile all'utente della sessione attiva (regola udev dell'immagine).

Quando il visore si stacca (o perde il fuoco) tutto ciò che era premuto si rilascia: niente tasti
bloccati.
"""

from __future__ import annotations

import fcntl
import os
import socket
import struct
import time
from typing import Any, Callable

from . import protocollo as P
from .cattura import Monitor

# costanti di linux/uinput.h e linux/input-event-codes.h
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_DEV_SETUP = 0x405C5503
UI_ABS_SETUP = 0x401C5504
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_REPORT = 0
REL_HWHEEL, REL_WHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 6, 8, 11, 12
ABS_X, ABS_Y = 0, 1
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BUTTONS = {1: BTN_LEFT, 2: BTN_MIDDLE, 3: BTN_RIGHT}
BUS_VIRTUAL = 0x06
ABS_MAX = 65535
MAX_KEY = 0x2FF  # i tasti della tastiera (0..248) e oltre, esclusi i pulsanti del mouse che si danno a parte


def input_event(kind: int, code: int, value: int, now: float | None = None) -> bytes:
    t = time.time() if now is None else now
    return struct.pack("llHHi", int(t), int((t % 1) * 1e6), kind, code, value)


def uinput_setup(name: str, vendor: int = 0x4149, product: int = 0x5301) -> bytes:
    return struct.pack("HHHH80sI", BUS_VIRTUAL, vendor, product, 1, name.encode()[:79], 0)


def abs_setup(code: int, maximum: int) -> bytes:
    # struct uinput_abs_setup { __u16 code; struct input_absinfo { value, min, max, fuzz, flat, resolution } }
    return struct.pack("Hxx6i", code, 0, 0, maximum, 0, 0, 0)


class VirtualDevice:
    """Un dispositivo uinput: `kind` "tastiera" (tasti, pulsanti, rotellina) o "puntatore" (assoluto)."""

    def __init__(self, kind: str, path: str = "/dev/uinput", ioctl: Callable[..., Any] = fcntl.ioctl,
                 opener: Callable[..., int] = os.open, writer: Callable[[int, bytes], int] = os.write):
        self.ioctl, self.writer = ioctl, writer
        self.fd = opener(path, os.O_WRONLY | os.O_NONBLOCK)
        if kind == "puntatore":
            self.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            self.ioctl(self.fd, UI_SET_KEYBIT, BTN_LEFT)
            self.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
            for code in (ABS_X, ABS_Y):
                self.ioctl(self.fd, UI_SET_ABSBIT, code)
                self.ioctl(self.fd, UI_ABS_SETUP, abs_setup(code, ABS_MAX))
            name = "AIOS Schermo puntatore"
        else:
            self.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for code in range(1, 249):
                self.ioctl(self.fd, UI_SET_KEYBIT, code)
            for code in BUTTONS.values():
                self.ioctl(self.fd, UI_SET_KEYBIT, code)
            self.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for code in (REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
                self.ioctl(self.fd, UI_SET_RELBIT, code)
            name = "AIOS Schermo tastiera e mouse"
        self.ioctl(self.fd, UI_DEV_SETUP, uinput_setup(name))
        self.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, events: list[tuple[int, int, int]]) -> None:
        data = b"".join(input_event(*e) for e in events) + input_event(EV_SYN, SYN_REPORT, 0)
        try:
            self.writer(self.fd, data)
        except BlockingIOError:
            pass

    def close(self) -> None:
        try:
            self.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


def hyprland_socket() -> str | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    for base in (os.environ.get("XDG_RUNTIME_DIR", ""), "/tmp"):
        path = os.path.join(base, "hypr", sig, ".socket.sock")
        if base and os.path.exists(path):
            return path
    return None


def hyprland_command(path: str, command: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        s.connect(path)
        s.sendall(command.encode())
        s.recv(64)


class Injector:
    """Applica gli eventi di INPUT allo schermo `monitor` di questo PC."""

    def __init__(self, monitor: Monitor | None, layout: list[Monitor] | None = None,
                 keyboard: Any = None, pointer: Any = None, hypr: Callable[[str], None] | None = None):
        self.monitor = monitor
        self.layout = layout or ([monitor] if monitor else [])
        self.keyboard, self.pointer, self.hypr = keyboard, pointer, hypr
        self.pressed_keys: set[int] = set()
        self.pressed_buttons: set[int] = set()
        self._wheel_rest = [0, 0]

    @classmethod
    def open(cls, monitor: Monitor | None, layout: list[Monitor]) -> "Injector":
        keyboard = pointer = None
        sock = hyprland_socket()
        try:
            keyboard = VirtualDevice("tastiera")
            if not sock:
                pointer = VirtualDevice("puntatore")
        except OSError:
            pass  # niente /dev/uinput: si guarda soltanto
        hypr = (lambda cmd: hyprland_command(sock, cmd)) if sock else None
        return cls(monitor, layout, keyboard, pointer, hypr)

    @property
    def can_control(self) -> bool:
        return self.keyboard is not None

    def position(self, nx: int, ny: int) -> tuple[int, int]:
        """Da 0..65535 sullo schermo trasmesso a pixel logici nella disposizione degli schermi."""
        m = self.monitor
        if m is None:
            return 0, 0
        lw, lh = m.logical
        return int(m.x + nx / ABS_MAX * (lw - 1)), int(m.y + ny / ABS_MAX * (lh - 1))

    def _abs_for_layout(self, x: int, y: int) -> tuple[int, int]:
        """Un puntatore assoluto copre tutta la disposizione: si riporta il punto su quella."""
        mons = self.layout or ([self.monitor] if self.monitor else [])
        if not mons:
            return 0, 0
        x0 = min(m.x for m in mons)
        y0 = min(m.y for m in mons)
        x1 = max(m.x + m.logical[0] for m in mons)
        y1 = max(m.y + m.logical[1] for m in mons)
        return (int((x - x0) / max(1, x1 - x0 - 1) * ABS_MAX), int((y - y0) / max(1, y1 - y0 - 1) * ABS_MAX))

    def handle(self, data: bytes) -> None:
        for ev in P.unpack_events(data):
            try:
                self.apply(ev)
            except OSError:
                continue

    def apply(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == P.EV_MOVE:
            x, y = self.position(ev[1], ev[2])
            if self.hypr is not None:
                self.hypr(f"dispatch movecursor {x} {y}")
            elif self.pointer is not None:
                ax, ay = self._abs_for_layout(x, y)
                self.pointer.emit([(EV_ABS, ABS_X, ax), (EV_ABS, ABS_Y, ay)])
        elif kind == P.EV_BUTTON and self.keyboard is not None and ev[1] in BUTTONS:
            code = BUTTONS[ev[1]]
            (self.pressed_buttons.add if ev[2] else self.pressed_buttons.discard)(code)
            self.keyboard.emit([(EV_KEY, code, 1 if ev[2] else 0)])
        elif kind == P.EV_WHEEL and self.keyboard is not None:
            # come una rotellina ad alta risoluzione: i 120esimi subito, lo scatto intero quando si completa
            # (in giù sul visore = valori negativi per il kernel)
            out = []
            for axis, delta, hi, lo, sign in ((1, ev[2], REL_WHEEL_HI_RES, REL_WHEEL, -1), (0, ev[1], REL_HWHEEL_HI_RES, REL_HWHEEL, 1)):
                if not delta:
                    continue
                self._wheel_rest[axis] += delta
                steps = int(self._wheel_rest[axis] / 120)
                self._wheel_rest[axis] -= steps * 120
                out.append((EV_REL, hi, sign * delta))
                if steps:
                    out.append((EV_REL, lo, sign * steps))
            if out:
                self.keyboard.emit(out)
        elif kind == P.EV_KEY and self.keyboard is not None and 0 < ev[1] <= MAX_KEY:
            (self.pressed_keys.add if ev[2] else self.pressed_keys.discard)(ev[1])
            self.keyboard.emit([(EV_KEY, ev[1], 1 if ev[2] else 0)])
        elif kind == P.EV_RELEASE_ALL:
            self.release_all()

    def release_all(self) -> None:
        if self.keyboard is None:
            return
        ups = [(EV_KEY, c, 0) for c in sorted(self.pressed_keys | self.pressed_buttons)]
        self.pressed_keys.clear()
        self.pressed_buttons.clear()
        if ups:
            self.keyboard.emit(ups)

    def close(self) -> None:
        self.release_all()
        for dev in (self.keyboard, self.pointer):
            if dev is not None:
                try:
                    dev.close()
                except OSError:
                    pass
        self.keyboard = self.pointer = None
