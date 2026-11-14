"""Step 1 CLI.

    python -m app.hello contacts
    python -m app.hello sms <contact_id> "<text>"
"""

import argparse
import sys

from app import ghl


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.hello")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("contacts", help="print id, name, phone of up to 10 contacts")
    sms = sub.add_parser("sms", help="send one SMS to a contact")
    sms.add_argument("contact_id")
    sms.add_argument("text")
    args = parser.parse_args(argv)

    try:
        if args.command == "contacts":
            for c in ghl.list_contacts(10):
                name = c.get("contactName") or " ".join(
                    filter(None, [c.get("firstName"), c.get("lastName")])
                )
                print(f"{c['id']}\t{name or '-'}\t{c.get('phone') or '-'}")
        else:
            print(ghl.send_sms(args.contact_id, args.text))
    except ghl.GHLError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
