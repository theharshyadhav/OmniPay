"""python -m app.cli create-admin <handle>   (password from OMNIPAY_ADMIN_PASSWORD; never on the command line)"""
import os
import sys

from app.config import Settings
from app.platform import Platform


def main(argv):
    if len(argv) != 2 or argv[0] != "create-admin":
        print(__doc__); return 2
    pw = os.environ.get("OMNIPAY_ADMIN_PASSWORD", "")
    pf = Platform(Settings.from_env(), prices={})
    try:
        pf.sw._user(argv[1])
    except Exception:
        pf.sw.register(argv[1], os.environ.get("OMNIPAY_ADMIN_PIN"))
    pf.auth.set_password(argv[1], pw, is_admin=True)
    pf.ledger.conn().execute("UPDATE credentials SET is_admin=1 WHERE handle=?", (argv[1],))
    pf.audit.log("cli", "admin.created", argv[1])
    print("admin ready:", argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
