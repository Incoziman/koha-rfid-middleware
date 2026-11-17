# Middelburg Koha test instance: setup notes

Written on 2026-11-12 on the Koha 21 VM (`debian`, user `keanu`). Updated 2026-11-13: SIP opened to the Windows host, which reaches the VM by name as `debian.mshome.net`.

**Purpose:** a test copy of the Middelburg library data in Koha 21.11, used to test barcodes and RFID self-check over SIP2. It sits next to the existing `library` instance on the same VM.

> **Redacted copy.** Passwords, production server addresses and account names are replaced with `<placeholders>`. The full version is kept privately, outside git.

---

## 1. Environment

| Item | Value |
|---|---|
| VM | Debian 12 (bookworm), Hyper-V guest on the **Default Switch** |
| VM hostname (from the Windows host) | **`debian.mshome.net`**. Use this, not the IP. Confirmed working 2026-11-13 |
| VM IP | Changes when the Windows host restarts. It was `172.22.248.84` on 2026-11-12 and `172.17.21.49/20` on 2026-11-13. Check with `ip -4 addr` |
| Gateway / Windows host | First address of the VM's subnet (`172.17.16.1` on 2026-11-13). Check with `ip route` |
| Koha | `koha-common 21.11.26-1` |
| MariaDB | 10.11 |
| SSH | `sshd` runs on port 22, login `keanu@debian.mshome.net` |

### Instances on this VM

| | `library` (existing) | `middelburg` (new) |
|---|---|---|
| OPAC | port 80 | http://debian.mshome.net:8081 |
| Staff interface | port 8080 | http://debian.mshome.net:8082 |
| Database | `koha_library` | `koha_middelburg` |
| SIP listener | `127.0.0.1:6001` (VM only) | port `6002`, all interfaces (reachable from the host) |
| SIP institution | `1` | `MIDDL` |
| SIP login | `sipterm1` / `<sip-password>` | `sipterm1` / `<sip-password>` |
| Data | empty (0 records / items) | full Middelburg dump |

---

## 2. Getting the dump onto the VM

The source is `middelburglibrary-2026-09-14.sql.gz` (about 221 MB). It's a Koha **20.11** dump from MariaDB 10.3, and it covers the whole Mpumalanga consortium, not only Middelburg.

Expected SHA-256: `386dee28cbf3138f1ce0af2c9d8e053535608275f47b088e93a05dd8aab6bc84`

**Hop 1, library server → main PC** (run on the main PC):
```bash
rsync -avP <server-user>@<library-server-ip>:/home/<server-user>/middelburglibrary-2026-09-14.sql.gz .
sha256sum middelburglibrary-2026-09-14.sql.gz
```
The VM can't reach `<library-server-ip>` ("No route to host"), so this hop has to run from the main PC.

**Hop 2, main PC → VM** (run on the main PC, PowerShell):
```powershell
scp middelburglibrary-2026-09-14.sql.gz keanu@debian.mshome.net:~/
```

**Check on the VM:**
```bash
sha256sum ~/middelburglibrary-2026-09-14.sql.gz   # matched
gzip -t  ~/middelburglibrary-2026-09-14.sql.gz    # OK
```

**A newer dump, if needed:** the library server makes one daily, but it isn't readable by `<server-user>`. Copy it out with sudo on the server:
```bash
sudo cp /var/spool/koha/middelburglibrary/middelburglibrary-2026-11-12.sql.gz /home/<server-user>/
sudo chown <server-user>: /home/<server-user>/middelburglibrary-2026-11-12.sql.gz
```

---

## 3. Temporary sudo for Claude

Claude's shell can't type a sudo password, and `!` commands in Claude Code have no terminal. So you run this in a **separate terminal** (Hyper-V console or SSH):
```bash
echo 'keanu ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/claude-tmp >/dev/null \
  && sudo chmod 440 /etc/sudoers.d/claude-tmp && sudo visudo -c
```
To remove it afterwards: `sudo rm /etc/sudoers.d/claude-tmp`. It was removed after setup.

---

## 4. Creating the `middelburg` instance

The instance name `middelburg` was picked so it doesn't clash with `library`.

```bash
# custom ports via a copy of koha-sites.conf
sed -e 's/^OPACPORT=.*/OPACPORT="8081"/' -e 's/^INTRAPORT=.*/INTRAPORT="8082"/' \
    /etc/koha/koha-sites.conf > middelburg-sites.conf

# make Apache listen on the new ports (backup: /etc/apache2/ports.conf.bak-claude)
echo 'Listen 8081' | sudo tee -a /etc/apache2/ports.conf
echo 'Listen 8082' | sudo tee -a /etc/apache2/ports.conf

sudo koha-create --create-db --configfile middelburg-sites.conf middelburg
sudo apachectl configtest && sudo systemctl reload apache2
```

---

## 5. Importing the dump

The dump hard-codes `CREATE DATABASE` / `USE koha_middelburglibrary`. Those two lines have to be removed so the data goes into `koha_middelburg`:

```bash
zcat middelburglibrary-2026-09-14.sql.gz \
  | sed -e '/^CREATE DATABASE .*`koha_middelburglibrary`/d' \
        -e '/^USE `koha_middelburglibrary`;/d' \
  | sudo koha-mysql middelburg
```
It took about 3 minutes and loaded:

| Table | Rows |
|---|---|
| biblio | 748,299 |
| items | 644,503 |
| borrowers | 20,428 (before the 2 accounts added below) |
| branches | 157 |

---

## 6. Upgrading the schema from 20.11 to 21.11

```bash
sudo koha-upgrade-schema middelburg
```

**Problem:** the upgrade stopped at `21.11.02.003` (Bug 29605) with
`Duplicate entry 'ar' for key 'PRIMARY'`. The table `language_script_mapping` had 3 identical copies each of `ar/Arab` and `he/Hebr`, a known leftover from older versions.

**Fix:** remove the duplicates, then run the upgrade again:
```sql
CREATE TABLE lsm_tmp AS SELECT DISTINCT * FROM language_script_mapping;
DELETE FROM language_script_mapping;
INSERT INTO language_script_mapping SELECT * FROM lsm_tmp;
DROP TABLE lsm_tmp;
```
```bash
sudo koha-upgrade-schema middelburg   # finished at Version 21.1126000, no errors
```

---

## 7. Search index (Zebra)

```bash
sudo bash -c 'nohup koha-rebuild-zebra -f -b -a -v middelburg \
  > /var/log/koha/middelburg/zebra-full-rebuild.log 2>&1 &'
```
- It runs at about 700 records a second, so the full run takes roughly 20–30 minutes. Until it finishes, catalogue search in the web interface is incomplete.
- `error retrieving biblio NNN` lines are broken records in the source data. There were about 12,700 when these notes were written; they won't show in search.
- **SIP barcode lookups and circulation don't need the index.**

> **2026-11-13: the rebuild didn't finish.** The VM ran short of RAM (3.8 GB) and was shut down at 17:25 on 2026-11-12, while the rebuild was still exporting records (it had reached biblio 672,424 of about 748,000). The logs show an orderly shutdown (`last -x` records `shutdown`, and MariaDB shut down cleanly). The database hasn't been checked with root access yet. Run the rebuild again, and first give the VM more RAM or stop the `library` instance.

Check progress:
```bash
tail -c 300 /var/log/koha/middelburg/zebra-full-rebuild.log
pgrep -f 'rebuild_zebra.pl.*-f' && echo running || echo finished
```

---

## 8. SIP setup (copied from `library`)

How far `library` had got before this work:
- `SIPconfig.xml` with listener `127.0.0.1:6001`, account `sipterm1`/`<sip-password>` and institution `1`.
- Patron `sipterm1` (card `64`, category `L`, branch `1`, `flags=2`, which is circulate permission).
- A SIP login test passed (`941`). An item lookup returned "not found" because `library` has no items.

The same setup was repeated on `middelburg`.

**SIP patron** (same password hash as the `library` one):
```sql
INSERT INTO borrowers (cardnumber,userid,surname,branchcode,categorycode,flags,password,dateenrolled,dateexpiry,privacy)
VALUES ('64','sipterm1','sipterm1','MIDDL','L',2,'<hash copied from library>',CURDATE(),'2099-12-31',1);
```

**SIP config:**
```bash
sudo koha-enable-sip middelburg      # Koha's default config saved as SIPconfig.xml.default
```
`/etc/koha/sites/middelburg/SIPconfig.xml` is a copy of `library`'s with 3 lines changed:
```diff
-      port="127.0.0.1:6001/tcp"
+      port="127.0.0.1:6002/tcp"
-      <login id="sipterm1" ... institution="1" />
+      <login id="sipterm1" ... institution="MIDDL" />
-    <institution id="1" implementation="ILS" parms="">
+    <institution id="MIDDL" implementation="ILS" parms="">
```
```bash
sudo koha-sip --start middelburg     # note: the command hangs, but the daemon starts fine
```

**Tests that passed** (SIP2 on 127.0.0.1:6002):

| Message | Result |
|---|---|
| `93` Login `sipterm1` | `941` (success) |
| `17` Item info, barcode `3322810009661` | found "Sonder gister se spoor", status `03` (available), branch MIDDL |
| `63` Patron info, a MIDDL patron card | valid patron record returned |

Handy test script (perl, no extra packages):
```bash
TS=$(date +%Y%m%d)"    "$(date +%H%M%S)
perl -MIO::Socket::INET -e '
my $s=IO::Socket::INET->new("127.0.0.1:6002") or die $!;
for my $m (@ARGV){ print $s "$m\r"; local $/="\r"; print ">> $m\n<< ".<$s>."\n\n"; }' \
 "9300CNsipterm1|CO<sip-password>|CPMIDDL|" \
 "17${TS}AOMIDDL|AB3322810009661|AC|"
```

Logs: `/var/log/koha/middelburg/sip.log`, `sip-error.log`

---

## 9. Staff login

| Account | Username | Password | Notes |
|---|---|---|---|
| Superlibrarian | `superlib` | `<redacted>` | Patron #22411, card `superlib`, branch MIDDL, category S, `flags=1`. Login tested. |
| Database user (backup) | `koha_middelburg` | `<redacted>` | From `koha-conf.xml`. Avoid for daily use; some circulation pages break with it. |

Change the `superlib` password in the staff interface: patron record → More → Change password.

The staff accounts that came with the dump keep their production passwords.

---

## 10. Fixing the 404 on catalogue and patron searches

**Symptom:** every search returned Koha's "An error has occurred! Error 404" page.

**Cause:** the source library had renamed Koha to **"InCommand"**. Its `IntranetUserJS` setting rewrote every link and form from `/cgi-bin/koha/` to `/cgi-bin/incommand/`, a path that only existed on their server. The Apache log showed `script not found ... /usr/lib/cgi-bin/incommand`.

**Fix (on `middelburg` only):**
- Cleared `IntranetUserJS` and `OPACUserJS`. This also removes the "InCommand" name and a button for a room-booking plugin that isn't installed.
- Changed `OPACBaseURL` from `<production-opac-url>` (production) to `http://172.22.248.84:8081`. That IP is now out of date. Change it to `http://debian.mshome.net:8081` under Administration → System preferences.
- Flushed memcached so the changes apply immediately. This is a shared cache, but flushing it is harmless.
- Backup of the original values: `/home/keanu/middelburg-backup/userjs-css-prefs.tsv`

`IntranetUserCSS` and `OPACUserCSS` were left alone. They only load the logo, partly from the production server.

---

## 11. Notes about the data

- This is **real patron data**: names, emails and phone numbers for about 20,000 people.
- Outgoing email is **disabled** for this instance (Koha default; there's no `/var/lib/koha/middelburg/email.enabled`), so check-outs and notices won't email real patrons. **Keep it disabled.**
- Branch for testing: `MIDDL` (Middelburg Regional Library).
- Production server IPs that appear in the settings: `<production-server-ip>`.

---

## 12. Next step: RFID testing from the Windows host

### How it fits together

```
RFID reader ──USB/serial──> middleware / self-check software ──SIP2 over TCP──> Koha SIP
(on the Windows host)       (on the Windows host)                               (VM: debian.mshome.net:6002)
```

- Koha never sees the reader. The middleware reads the tag, gets the barcode, and handles the security bit (AFI/EAS) itself. It talks to Koha only through SIP2 messages: `93` login, `17` item info, `11` check-out, `09` check-in, `63` patron info.
- So the VM does **not** need to detect the reader. No USB passthrough is needed, which Hyper-V doesn't handle well anyway.
- **Plan:** plug the reader into the Windows host, install the middleware there, and point it at the VM. This acts as a sort of off-site test.

### Opening SIP to the host (done 2026-11-13)

At first the SIP server only accepted connections from inside the VM (`127.0.0.1`). What was changed:

1. In `/etc/koha/sites/middelburg/SIPconfig.xml`:
   ```
   port="127.0.0.1:6002/tcp"   →   port="6002/tcp"
   ```
   > **Don't use `0.0.0.0:6002/tcp`.** It opens the port, but Koha can't match incoming connections to that listener. Each connection is accepted and then closed with no reply, even from inside the VM. `sip.log` shows `process_request: Unknown recognized server connection`. Leaving out the address (`6002/tcp`) still listens on all interfaces, and it works.
2. Restart SIP: `sudo koha-sip --restart middelburg`
3. Tested 2026-11-13: login `941` and item info for barcode `3322810009661` both succeeded on `127.0.0.1` and on the VM's network IP. From the host (PowerShell), this succeeds:
   ```powershell
   Test-NetConnection debian.mshome.net -Port 6002
   ```
4. **Not done yet:** a firewall rule so only the host can reach port 6002, because SIP2 is unencrypted. No firewall is running now (ufw and nftables are both inactive). On the Default Switch only the host can reach the VM anyway, but add the rule before moving to an External switch. The host's IP changes too, so the rule should allow the subnet (`ip route`), not one address.

`library` stays local-only.

### Middleware settings

| Setting | Value |
|---|---|
| Host | **`debian.mshome.net`** (don't use the IP; it changes) |
| Port | `6002` |
| Login / password | `sipterm1` / `<sip-password>` |
| Institution / location (AO/CP) | `MIDDL` |
| Field delimiter | `\|`, with messages ending in a carriage return (CR) |
| Error detection / checksums | enabled in `SIPconfig.xml` (`error-detect="enabled"`); match it in the middleware |

### Network limits to know about

- **The host can reach the VM.** The host is the VM's gateway on the Default Switch.
- **Other LAN machines can't**, for example a separate self-check PC or a network RFID reader. For those you'd need an **External virtual switch** in Hyper-V, or port forwarding on the host.
- **The VM IP changes when the host restarts** (the Default Switch picks a new subnet). The name `debian.mshome.net` keeps working, so use it everywhere on the host. If the name ever stops resolving, find the current IP with `ip -4 addr` on the VM.
- **Find out the middleware's needs:** the vendor or product may require a specific port, a terminal or location code, or checksum settings.

---

## 13. Files and locations

| Path | What it is |
|---|---|
| `~/middelburglibrary-2026-09-14.sql.gz` | Source dump |
| `/etc/koha/sites/middelburg/` | Instance config (`koha-conf.xml`, `SIPconfig.xml`, `SIPconfig.xml.default`) |
| `/etc/apache2/ports.conf.bak-claude` | Apache ports backup from before adding 8081/8082 |
| `~/middelburg-backup/userjs-css-prefs.tsv` | Original JS/CSS/URL settings from the dump |
| `/var/log/koha/middelburg/` | Logs (`intranet-error.log`, `sip.log`, `zebra-full-rebuild.log`) |
| `/etc/sudoers.d/claude-tmp` | Temporary sudo rule, **removed** |

## 14. Open items

- [ ] Check the database after the 2026-11-12 shutdown (needs root): row counts against section 5, plus `CHECK TABLE` on the main tables.
- [ ] Run the Zebra reindex again. It was interrupted on 2026-11-12 (section 7).
- [x] Open SIP to the host (section 12). Done 2026-11-13.
- [ ] Add a firewall rule for port 6002 (section 12, step 4).
- [ ] Change `OPACBaseURL` to `http://debian.mshome.net:8081` (section 10).
- [ ] Install and configure the RFID middleware on the Windows host.
- [ ] Test the full check-out and check-in cycle (`11` / `09`) with real tags.
- [ ] Optional: change the `superlib` password.
- [ ] Optional: switch the instance to Plack for speed (`sudo koha-plack --enable middelburg && sudo koha-plack --start middelburg`). It currently runs as plain CGI, which is slower.
