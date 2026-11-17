"""SIP2 self-check: patrons check books out and in by themselves.

A semi-separate feature from the staff desk plugin (docs/desk/): it talks to
Koha over SIP2 as a SIP account, so Koha records the kiosk, not a librarian.
Design and status: docs/selfcheck/selfcheck-sip2.md.

    circulation.py  plan / transaction / log building blocks (done, used by
                    testing/step5_circulate.py)
    kiosk.py        the running self-check station (next)
"""
