# Steps to be taken
Step 1 — read tags, nothing else. Run sllurp against the R220 and print EPCs to the console. Confirm South African band settings (915–919) and that your test tags report consistently. No Koha involvement.

Step 2 — decode EPC → barcode, offline. A function that takes an EPC string and returns a barcode. Test it against known tag/item pairs with no reader and no network. This is where you’ll find encoding surprises — padding, hex vs ASCII, byte order.

Step 3 — barcode → SIP item info, offline. A pysip2 script that logs in and sends a 17 for a hardcoded barcode, exactly what you did by hand in telnet. Confirms your SIP client library behaves like the raw protocol did.

Step 4 — join them. Reader → decode → SIP item info → print title. Read-only, so a mistake can’t corrupt circulation data. This is the real milestone.

Step 5 — checkout. Only now add the 11 message, against your middelburg test instance.

Step 6 — field test with real books. Tag 10–20 real MIDDL books (checked against Koha with testing/check_test_books.py, printed with print_rfid_labels.ps1 -CsvFile), tune the pad power, then run the scenario list in docs/field-test.md: stacks, orientation, holds, blocked patrons, stray tags, Koha down, exit gate. Still against the test instance.

Step 7 — package it. Staff desk: Koha plugin (koha-plugin/) + desk agent (koha_rfid/agent/, installed by deploy/install_agent.ps1 as a boot-time task), so check-out/check-in from the pad happen in Koha's own pages as the logged-in librarian (docs/desk/desk-setup.md). Self-check over SIP2 split out as its own feature (koha_rfid/selfcheck/, docs/selfcheck/selfcheck-sip2.md).
