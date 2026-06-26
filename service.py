"""
SERVICE / HEADLESS entrypoint for the remote-desktop host.

Runs host.py logic without any GUI (no Tkinter, no pygame).
Designed for unattended access: auto-restart on crash, file-based logging.

Configuration sources (highest priority wins):
  1. CLI arguments
  2. Environment variables (RD_RELAY, RD_ID, RD_PASSWORD, RD_LISTEN, ...)
  3. Config file (--config path, default: service.json next to this script)

Usage examples:
  # Relay mode
  python service.py --relay vps:5800 --id myroom --password SECRET

  # Direct mode
  python service.py --listen 5900 --password SECRET

  # From environment (useful for Scheduled Task / service wrapper)
  set RD_RELAY=vps:5800
  set RD_ID=myroom
  set RD_PASSWORD=secret
  python service.py
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "service.json")
DEFAULT_LOG = os.path.join(HERE, "service.log")


def _setup_logging(log_file, verbose=False):
    """Configure file + stderr logging."""
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stderr)]
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=handlers,
    )
    return logging.getLogger("rd-service")


def _load_config(path):
    """Load JSON config file; return empty dict if missing/invalid."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[service] warning: cannot load config {path}: {exc}", file=sys.stderr)
        return {}


def _resolve(cli_val, env_name, cfg_dict, cfg_key, default=None):
    """Pick value from CLI > env > config > default."""
    if cli_val is not None:
        return cli_val
    env = os.environ.get(env_name)
    if env is not None:
        return env
    if cfg_key in cfg_dict:
        return cfg_dict[cfg_key]
    return default


def build_args(cli_args=None):
    """Parse CLI, merge env/config, return a namespace suitable for host.run_host()."""
    ap = argparse.ArgumentParser(
        description="Remote-desktop host — headless / service mode"
    )
    ap.add_argument("--relay", help="Relay address host:port")
    ap.add_argument("--listen", type=int, help="Direct-listen port")
    ap.add_argument("--id", default=None, help="Relay room ID")
    ap.add_argument("--password", default=None, help="Shared password (E2E)")
    ap.add_argument("--downloads", default=None, help="Directory for received files")
    ap.add_argument("--quality", type=int, default=None)
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--codec", default=None, choices=["auto", "jpeg", "png"])
    ap.add_argument("--engine", default=None, choices=["auto", "x264", "tiles"])
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="JSON config file path")
    ap.add_argument("--log-file", default=None, help="Log file path")
    ap.add_argument("--restart-delay", type=float, default=5.0,
                    help="Seconds to wait before restarting after crash")
    ap.add_argument("--no-restart", action="store_true",
                    help="Exit on crash instead of restarting")
    ap.add_argument("--verbose", action="store_true")

    parsed = ap.parse_args(cli_args)
    cfg = _load_config(parsed.config)

    # Build final namespace
    args = types.SimpleNamespace()
    args.relay = _resolve(parsed.relay, "RD_RELAY", cfg, "relay")
    listen_raw = _resolve(parsed.listen, "RD_LISTEN", cfg, "listen")
    args.listen = int(listen_raw) if listen_raw is not None else None
    args.id = _resolve(parsed.id, "RD_ID", cfg, "id", "default")
    args.password = _resolve(parsed.password, "RD_PASSWORD", cfg, "password")
    args.downloads = _resolve(
        parsed.downloads, "RD_DOWNLOADS", cfg, "downloads",
        os.path.join(os.path.expanduser("~"), "RemoteDesktop_received"),
    )

    q = _resolve(parsed.quality, "RD_QUALITY", cfg, "quality", 65)
    args.quality = int(q)
    f = _resolve(parsed.fps, "RD_FPS", cfg, "fps", 20)
    args.fps = int(f)
    s = _resolve(parsed.scale, "RD_SCALE", cfg, "scale", 1.0)
    args.scale = float(s)
    args.codec = _resolve(parsed.codec, "RD_CODEC", cfg, "codec", "auto")
    args.engine = _resolve(parsed.engine, "RD_ENGINE", cfg, "engine", "auto")

    # Service-specific
    args.log_file = _resolve(parsed.log_file, "RD_LOG_FILE", cfg, "log_file", DEFAULT_LOG)
    rd = _resolve(parsed.restart_delay, "RD_RESTART_DELAY", cfg, "restart_delay", 5.0)
    args.restart_delay = float(rd)
    args.no_restart = parsed.no_restart or cfg.get("no_restart", False)
    args.verbose = parsed.verbose or cfg.get("verbose", False)

    return args


def run_service(args=None, stop_event=None):
    """Main service loop: run host, auto-restart on crash.

    Parameters
    ----------
    args : namespace, optional
        Pre-built args (for programmatic use / testing). If None, parse sys.argv.
    stop_event : threading.Event, optional
        Set this to request a clean shutdown.
    """
    if args is None:
        args = build_args()

    if not args.password:
        print("ERROR: password is required (--password, RD_PASSWORD, or config file)",
              file=sys.stderr)
        sys.exit(1)
    if not args.relay and not args.listen:
        print("ERROR: specify --relay or --listen (or RD_RELAY / RD_LISTEN / config)",
              file=sys.stderr)
        sys.exit(1)

    logger = _setup_logging(args.log_file, args.verbose)

    if stop_event is None:
        stop_event = threading.Event()

    # Graceful shutdown on SIGINT / SIGTERM
    def _signal_handler(sig, frame):
        logger.info("Signal %s received, shutting down...", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Import host here so the module-level dependencies (mss, numpy, etc.)
    # are only loaded when we actually run.
    sys.path.insert(0, HERE)
    import host

    # Redirect host.LOG to our logger
    host.LOG = lambda msg: logger.info(msg)

    mode = f"relay={args.relay} id={args.id}" if args.relay else f"listen={args.listen}"
    logger.info("Service starting (%s), restart=%s",
                mode, "off" if args.no_restart else f"{args.restart_delay}s")

    attempt = 0
    while not stop_event.is_set():
        attempt += 1
        logger.info("--- Attempt %d ---", attempt)
        try:
            host.run_host(args, stop_event=stop_event)
            # run_host returned normally (stop_event set or graceful exit)
            if stop_event.is_set():
                logger.info("Clean shutdown.")
                break
        except SystemExit:
            logger.info("SystemExit caught, stopping.")
            break
        except Exception:
            logger.exception("Host crashed")

        if args.no_restart or stop_event.is_set():
            break
        logger.info("Restarting in %.1f s ...", args.restart_delay)
        stop_event.wait(args.restart_delay)

    logger.info("Service stopped.")


if __name__ == "__main__":
    run_service()
