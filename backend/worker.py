"""Single durable worker; does not auto-send reports."""
import signal
import threading
from .prod import build_app

def main():
    app=build_app();stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *args:stop.set())
    while not stop.is_set():
        try:worked=app.state.run_next()
        except Exception:worked=False
        if not worked:stop.wait(1)
if __name__=='__main__':main()
