# RFID at the circulation desk: setup and use

This is the production setup for staff circulation. Librarians check books out and in from the RFID pad inside Koha's normal Check out and Check in pages. Koha records **the logged-in librarian and their branch** for every transaction, exactly as for a barcode scan. Librarians also print RFID labels from a bibliographic record.

(Patron self-check over SIP2 is a separate feature: see [`../selfcheck/selfcheck-sip2.md`](../selfcheck/selfcheck-sip2.md).)

```
 desk PC                                                       Koha server
┌───────────────────────────────────────────────┐          ┌──────────────────────────┐
│ browser: Koha staff interface                 │  HTTPS   │ RFID desk plugin         │
│   + plugin script (panel, queue) ─────────────┼─────────►│  - adds the script       │
│        │ http://127.0.0.1:8765                │          │  - /api/v1/contrib/      │
│        ▼                                      │          │    rfiddesk (look-ups)   │
│ koha-rfid desk agent (Scheduled Task, SYSTEM) │          └──────────────────────────┘
│   - R220 reader (LLRP) ───────────────────────┼──► R220 + antenna (the pad)
│   - ZD500R printer (port 9100) ───────────────┼──► label printer
└───────────────────────────────────────────────┘
```

- **The agent** runs in the background on each desk PC from boot. Nobody starts it by hand. It owns the reader and the printer, and answers only on the PC itself (127.0.0.1) and only to the Koha staff address in its config.
- **The plugin** adds a panel to Koha's Check out and Check in pages. The panel asks the agent what's on the pad and looks the barcodes up in Koha. When the librarian clicks **Check out N books**, it types each barcode into Koha's own barcode box, one at a time. Everything Koha normally does happens as usual: circulation rules, holds questions, fines warnings, slips, logs.
- **Nothing is written to the tags** at check-out or check-in. "Checked out" lives only in Koha. A security gate would ask Koha; see [`../reference/uhf-rfid.md`](../reference/uhf-rfid.md#security-checked-out-or-not).

## 1. Koha server: once

Needs `sudo` on the Koha server. The example uses the `middelburg` instance.

1. **Enable plugins.** In `/etc/koha/sites/middelburg/koha-conf.xml`, set
   ```xml
   <enable_plugins>1</enable_plugins>
   ```
   and check that `<pluginsdir>` points to a directory that exists (usually `/var/lib/koha/middelburg/plugins`). Then restart:
   ```bash
   sudo koha-plack --restart middelburg
   sudo systemctl restart apache2 memcached
   ```
2. **Install the plugin.**
   1. Build it on any PC with the repo: `python deploy/build_plugin.py` gives `dist/koha-plugin-rfiddesk-<version>.kpz`. The `.kpz` also carries the desk agent and its installer, so desk PCs don't need the repo. The build needs internet the first time, for pip.
   2. In the staff interface: **Administration → Manage plugins → Upload plugin**, and choose the `.kpz`. This needs the `plugins` permission (a superlibrarian has it).
   3. Restart plack again, so the plugin's REST route and the agent downloads are loaded: `sudo koha-plack --restart middelburg`.
3. **Configure it.** In **Manage plugins**, find **RFID desk** and choose **Actions → Configure**. The defaults are right for most desks: agent address `http://127.0.0.1:8765`, with the check-out, check-in and labels features all on.
4. **System preferences** (Administration → System preferences):

   | Preference | Set to | Why |
   |---|---|---|
   | `RecordStaffUserOnCheckout` | Record | Shows "Checked out by: *librarian*" on each checkout. Off by default |
   | `IssueLog`, `ReturnLog` | Log | The action log (Tools → Log viewer) records the librarian for each check-out and check-in. On by default |

5. **Permissions.** Librarians need `circulate` to check out and in (they already have it). To print labels they need `editcatalogue → edit_items`.

To **upgrade** the plugin, upload the new `.kpz` the same way, then restart plack. Then update each desk PC (section 2).

## 2. Each desk PC: once

The desk PC doesn't need the repo. Everything comes from the plugin on the Koha server.

1. On the desk PC, log in to the staff interface as an admin. Open **Administration → Manage plugins**, find **RFID desk** and choose **Actions → Configure**. The **Set up a desk PC** section shows a command, with this Koha's address already filled in. Click **Copy command**.
2. Open **PowerShell as administrator**: Start menu, type *PowerShell*, right-click, **Run as administrator**. Paste the command and press Enter.
   - If Python 3.12 isn't installed, it installs it for all users with `winget`. On a PC without `winget`, first install Python 3.12 from python.org with **Install for all users** ticked. Don't use the Microsoft Store Python.
   - It downloads the agent from Koha and installs it, with its Python packages from the internet, into `C:\koha-rfid\`: its own Python environment, `agent.ini`, `agent.log` and a copy of the installer, `install_agent.ps1`.
   - The first time, it asks for:
     - the R220's IP address;
     - the **pad power** from the field test ([`../testing/field-test.md`](../testing/field-test.md#3-tune-the-pad)), low enough that books 1 m away aren't read;
     - the ZD500R's IP address, only on desks that print labels. Type `1` for `192.168.1.2`, the usual Zebra address.

     Press Enter to keep the value shown, or type `none` to leave it empty. `allowed_origins` is set to the Koha address from the command.
   - It registers the **Koha RFID agent** Scheduled Task, which starts at boot, runs hidden, and is re-checked every 5 minutes. It then starts the agent and shows its status.
3. **Check it.** The installer ends by showing the reader as connected. In Koha, open a patron's Check out page: the RFID pad panel has a green dot.

**Managing a desk PC from Koha.** On the desk PC, open the plugin's **Configure** page (Manage plugins → RFID desk → Actions → Configure). The **This desk PC** section talks to the agent on that PC and shows:
- the agent version, whether the reader is connected (and the error if not), the label printer, and the settings file;
- **On the pad now**: the library tags the reader sees, updated every second. Use it to tune the pad power: lower it until books about 1 m from the pad are no longer listed;
- the reader and printer settings: IP addresses, pad power (with a table of what each index means in dBm), antennas, pad memory, reconnect interval, session and ports. **Save to this desk's agent** writes them to `C:\koha-rfid\agent.ini` and applies them at once: the reader reconnects, with no restart. **Reconnect reader** drops and reconnects the reader.

The page shows the agent of the PC it is opened on, because the agent only answers on that PC. `listen_port` and `allowed_origins` can't be changed from Koha, since a wrong value would cut the page off from the agent. Change those in `agent.ini`, then `-Restart`.

**Managing a desk PC from PowerShell.** Run these in PowerShell as administrator:

| Command | What it does |
|---|---|
| `C:\koha-rfid\install_agent.ps1 -Status` | Shows whether the agent is running, and the reader and printer |
| `C:\koha-rfid\install_agent.ps1 -Restart` | Restarts the agent after editing `C:\koha-rfid\agent.ini` |
| `C:\koha-rfid\install_agent.ps1 -Update` | After a plugin upgrade on the server: installs the agent version now in Koha. Keeps `agent.ini` |
| `C:\koha-rfid\install_agent.ps1 -Pause` / `-Resume` | Stops the agent and frees the R220 for the `testing\` scripts, then starts it again |
| `C:\koha-rfid\install_agent.ps1 -Uninstall` | Removes the Scheduled Task. Delete `C:\koha-rfid\` by hand |

If PowerShell refuses to run the script, put `powershell -ExecutionPolicy Bypass -File` in front of it.

**Settings** are in `C:\koha-rfid\agent.ini`:
- `allowed_origins`: the staff interface address exactly as the browser shows it, e.g. `https://staff.library.example` or `http://debian.mshome.net:8082`. **If this is wrong, the panel shows "agent not running".**
- `[reader] host`, `power`: the R220's IP address and the pad power.
- `[printer] host`: the ZD500R's IP address, or empty.

Change the `[reader]` and `[printer]` settings on the Configure page (above). After editing the file by hand, run `-Restart`.

**Development PC with the repo:** `powershell -ExecutionPolicy Bypass -File deploy\install_agent.ps1` installs the agent from the working copy instead of from Koha.

## 3. Using it (librarians)

**Check out**
1. Find the patron as usual: scan their card, or search for them.
2. Put the books on the pad. The **RFID pad** panel above the barcode box lists them within a second or two:
   - `→` will be checked out;
   - `✓` is already checked out to this patron;
   - `–` will be skipped, with the reason: "on loan to another patron", "not in Koha", and so on.
3. **Check the list against the books in front of you**, then click **Check out N books**.
4. The page reloads once per book, as if you had scanned each one. If Koha asks something (a hold, a limit, a block), answer it in Koha's own box as usual, and the RFID queue carries on afterwards. If Koha shows a message with nothing to click, press **Continue** in the panel.
5. The panel ends with "Done: all N books checked out". If any weren't, it lists them.

**Check in.** The same steps on the **Check in** page, with **Check in N books**. Only books Koha shows as on loan are checked in. Hold and transfer messages appear as usual. Deal with each one (hold shelf, transfer bin), and the queue continues.

**Print RFID labels.** On a bibliographic record, click **RFID labels** in the toolbar. Tick the items, then **Print selected**. Each label's tag is read back by the printer, and any label that doesn't read back correctly is listed: void it and print again. Barcodes that aren't 1–22 digits can't go on a tag; give those items a numeric barcode first ([`../reference/barcode-format.md`](../reference/barcode-format.md)).

**Normal barcode scanning always still works**, with or without RFID.

## 4. Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| Grey dot: "RFID agent not running on this PC" | Agent stopped, **or** `allowed_origins` doesn't match the Koha address | `C:\koha-rfid\install_agent.ps1 -Status`. If it's running, fix `allowed_origins` (scheme, host and port exactly as in the address bar) and `-Restart`. Restarting the PC also starts it |
| Setup command: "Download failed … 404" | Plack wasn't restarted after uploading the plugin, or the `.kpz` was built without the agent | `sudo koha-plack --restart middelburg`. The plugin's Configure page warns when the `.kpz` doesn't carry the agent |
| Setup command: TLS or certificate error | The staff interface uses a certificate Windows doesn't trust (self-signed) | Install the certificate on the PC, or use a trusted certificate on the server |
| Setup command: "pip install failed" | No internet on the desk PC, or a proxy | The Python packages come from PyPI. Allow the PC out to pypi.org and files.pythonhosted.org, then run the command again |
| Amber dot: "reader not connected" | R220 off, unplugged, or busy with another program | Switch on or plug in the R220; the agent reconnects within 10 s. Only one program can use the R220: `-Pause` the agent before running the `testing\` scripts, then `-Resume` |
| Books on the pad aren't listed | Too far from the antenna, tag on metal, or power set too low | Move the books closer or flat on the pad; check the pad power on the Configure page (This desk PC) |
| Books from the trolley or the next patron are listed | Power too high | Lower the pad power on the Configure page (This desk PC), watching **On the pad now** |
| Configure page: "running on this PC but refuses this page" | `allowed_origins` doesn't match the Koha address | Fix `allowed_origins` in `agent.ini`, then `-Restart` |
| "Could not look items up in Koha: Koha API 401/403" | Logged out, or no `circulate` permission | Log in again; check permissions |
| "Koha API 404" | Plugin not loaded by the REST API | `sudo koha-plack --restart middelburg` |
| Labels: "no label printer configured on this desk" | `[printer] host` empty | Set the printer on the Configure page (This desk PC), on that desk PC |
| Labels: MISMATCH / NO REPLY | The tag write failed, or the label isn't calibrated | Void the label and reprint. For a new roll, run RFID calibration (`^HR`, see [`../reference/barcode-format.md`](../reference/barcode-format.md#printing-test-rfid-labels-zebra-zd500r)) |

The agent's log is `C:\koha-rfid\agent.log`.

## What Koha records

Because every barcode goes through Koha's own form in the librarian's session:
- **The checkout:** the patron, the item, the **branch the librarian is logged in at**, and, with `RecordStaffUserOnCheckout`, the **librarian** ("Checked out by").
- **Statistics and the action log** (Tools → Log viewer, module Circulation): the librarian's account.
- Nothing is recorded as `sipterm1` or any SIP account. SIP is only used by patron self-check.
