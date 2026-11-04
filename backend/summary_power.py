"""Keep a Windows CPU summary job awake without changing the user's power plan."""
import ctypes
import logging
import os
from contextlib import contextmanager

log = logging.getLogger("sumx.summary")
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def summary_awake():
    if os.name != "nt":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    set_state = kernel.SetThreadExecutionState
    set_state.argtypes = [ctypes.c_uint32]
    set_state.restype = ctypes.c_uint32
    previous = set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    log.info("summary_power_request active=%s", bool(previous))
    try:
        yield
    finally:
        if previous:
            set_state(previous)
        log.info("summary_power_request released=true")
