"""Desk agent: runs in the background on each circulation desk PC.

It owns the RFID reader and the label printer, and serves them on
http://127.0.0.1:8765 to the Koha staff plugin (koha-plugin/) in the
librarian's browser. Started at boot by the Scheduled Task that
deploy/install_agent.ps1 registers; nobody runs it by hand.

    python -m koha_rfid.agent --config C:\\koha-rfid\\agent.ini

Setup and troubleshooting: docs/desk/desk-setup.md.
"""
