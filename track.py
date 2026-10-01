"""Track flights and get phone notifications when they change.

  python track.py add UA1532            start tracking (first check runs right away)
  python track.py add UA1532 --no-notify   track, but print instead of notifying
  python track.py add UA1532 --date 2026-10-02   pin a specific date
  python track.py list                  show flights being tracked
  python track.py stop UA1532           stop tracking a flight
  python track.py run-due               check whatever is due, then exit (the cloud scheduler runs this)
  python track.py watch                 local mode: keep running and check flights as they come due
  python track.py --test-notify         send one test notification to your phone
"""

import argparse
import sys

from app import store
from app.notify import send_push
from app.tracker import run_due_checks, start_tracking, watch


def main():
    if "--test-notify" in sys.argv:
        ok = send_push("Flight agent test", "If you see this, notifications work.", 3)
        print("Sent." if ok else "Not sent. Check NTFY_TOPIC in .env.")
        return

    parser = argparse.ArgumentParser(description="Flight tracker", usage=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    add = sub.add_parser("add")
    add.add_argument("flight")
    add.add_argument("--date", help="flight date, YYYY-MM-DD (default: the most recent one found)")
    add.add_argument("--no-notify", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("flight")
    sub.add_parser("list")
    sub.add_parser("run-due")
    sub.add_parser("watch")
    args = parser.parse_args()

    db = store.connect()
    print(f"Database: {'Postgres (Neon)' if db.kind == 'postgres' else 'local SQLite'}")

    try:
        if args.cmd == "add":
            tid = start_tracking(args.flight, args.date, notify=not args.no_notify, db=db)
            print(f"Tracking {args.flight.upper()} (id {tid}).")
        elif args.cmd == "stop":
            n = store.stop_flight(db, args.flight.replace(" ", "").upper())
            print(f"Stopped {n} tracked flight(s).")
        elif args.cmd == "list":
            rows = store.active_flights(db)
            if not rows:
                print("No flights being tracked.")
            for r in rows:
                print(f"{r['flight_iata']:<8} date={r['flight_date'] or '?':<10} next check {r['next_check_at']} UTC")
        elif args.cmd == "run-due":
            n = run_due_checks(db)
            print(f"Checked {n} flight(s).")
        elif args.cmd == "watch":
            watch(db)
        else:
            print(__doc__)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
