"""python -m koha_rfid.agent --config agent.ini"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

from .. import __version__
from . import config
from .server import Agent


def setup_logging(log_file: str, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3,
                                                  encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    # sllurp is chatty at INFO
    logging.getLogger("sllurp").setLevel(logging.DEBUG if verbose else logging.WARNING)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="koha-rfid-agent", description=__doc__)
    ap.add_argument("--config", default=os.environ.get("KOHA_RFID_AGENT_CONFIG", "agent.ini"))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    try:
        cfg = config.load(a.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"koha-rfid-agent: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_file, a.verbose)
    if not cfg.allowed_origins:
        logging.warning("allowed_origins is empty: the Koha plugin will be refused. "
                        "Set it to the staff interface address, e.g. http://debian.mshome.net:8082")

    try:
        agent = Agent(cfg)
    except OSError as e:
        logging.error("can't listen on 127.0.0.1:%s (%s) - is another agent running?",
                      cfg.listen_port, e)
        return 3
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    agent.start()
    try:
        while not stop.wait(1):
            pass
    finally:
        logging.info("stopping")
        agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
