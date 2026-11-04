"""Demo server for filming: its clock is BASE + film time, where film time is written by capture.js after every frame.
Lets price holds count down at video speed even though frames are captured slower than real time."""
import os, sys, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("OMNIPAY_DEMO", "1")
from wsgiref.simple_server import make_server
from app.config import Settings
from app.platform import DEMO_PRICES, Platform
from app.seed import seed
from app.server import make_app
from app.serve import Quiet, Threaded

CLOCK = sys.argv[1]
BASE = float(sys.argv[2])
def clock():
    try:
        with open(CLOCK) as f:
            return BASE + float(f.read() or 0)
    except (OSError, ValueError):
        return BASE
cfg = Settings.from_env()
pf = Platform(cfg, prices=DEMO_PRICES, clock=clock)
pf.groq = None
if not pf.ledger.conn().execute("SELECT 1 FROM users LIMIT 1").fetchone():
    seed(pf)
print("film server up", flush=True)
make_server("127.0.0.1", 8124, make_app(pf), server_class=Threaded, handler_class=Quiet).serve_forever()
