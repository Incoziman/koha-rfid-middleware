##Steps to be taken
Step 1 — read tags, nothing else. Run sllurp against the R220 and print EPCs to the console. Confirm South African band settings (915–919) and that your test tags report consistently. No Koha involvement.

Step 2 — decode EPC → barcode, offline. A function that takes an EPC string and returns a barcode. Test it against known tag/item pairs with no reader and no network. This is where you’ll find encoding surprises — padding, hex vs ASCII, byte order.

Step 3 — barcode → SIP item info, offline. A pysip2 script that logs in and sends a 17 for a hardcoded barcode, exactly what you did by hand in telnet. Confirms your SIP client library behaves like the raw protocol did.

Step 4 — join them. Reader → decode → SIP item info → print title. Read-only, so a mistake can’t corrupt circulation data. This is the real milestone.

Step 5 — checkout. Only now add the 11 message, against your middelburg test instance.
