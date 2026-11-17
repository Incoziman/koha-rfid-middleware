<#
.SYNOPSIS
Print item barcode labels on a Zebra ZD500R and write the same barcode to each label's UHF tag.

.DESCRIPTION
For each barcode, one ZPL label that:
  - writes the EPC in the length-prefixed encoding from docs/uhf-rfid.md
    (first byte = number of digits, then the digits, zero-padded to 96 bits),
  - reads the tag back and returns it to the host (only seen with -PrinterHost),
  - prints a Codabar barcode and the spaced human-readable number.

Barcodes come from -Barcode (sent exactly as given), from -CsvFile (the output
of tools/check_test_books.py: real Koha books, with their titles printed on the
label), or are generated in the standard format with -Start/-Count
(3 + institution + 8-digit item + Luhn check).
See docs/barcode-format.md, "Printing test RFID labels".

Layout is for a 203 dpi printer and a label at least 50 mm wide.
UNTESTED on the hardware: check the first labels by hand.

.EXAMPLE
# Look at the ZPL without printing
powershell -ExecutionPolicy Bypass -File tools\print_rfid_labels.ps1 -Barcode 33228100096617

.EXAMPLE
# Printer on the network (raw port 9100): print, then check the read-back
powershell -ExecutionPolicy Bypass -File tools\print_rfid_labels.ps1 -Start 99990001 -Count 3 -PrinterHost 192.168.1.50

.EXAMPLE
# Real books checked against Koha by tools/check_test_books.py
powershell -ExecutionPolicy Bypass -File tools\print_rfid_labels.ps1 -CsvFile test_books.csv -PrinterHost 192.168.1.50

.EXAMPLE
# Printer on USB, shared in Windows as "ZD500R" (no read-back over this path)
powershell -ExecutionPolicy Bypass -File tools\print_rfid_labels.ps1 -Barcode 33228100096617 -PrinterShare \\localhost\ZD500R
#>
[CmdletBinding()]
param(
    [string[]]$Barcode = @(),
    [string]$CsvFile,
    [int]$Start = -1,
    [int]$Count = 0,
    [string]$Institution = '3228',
    [string]$PrinterHost,
    [int]$Port = 9100,
    [string]$PrinterShare,
    [string]$OutFile
)

$ErrorActionPreference = 'Stop'

# Luhn over a 13-digit payload, doubling the 1st, 3rd, ... 13th digit from the left.
# Same as koha_rfid.barcode.check_digit.
function Get-CheckDigit([string]$Payload) {
    $total = 0
    for ($i = 0; $i -lt 13; $i++) {
        $d = [int][string]$Payload[$i]
        if ($i % 2 -eq 0) { $d *= 2; if ($d -gt 9) { $d -= 9 } }
        $total += $d
    }
    return [string]((10 - $total % 10) % 10)
}

function Test-Standard([string]$Code) {
    return ($Code -match '^3\d{13}$') -and ((Get-CheckDigit $Code.Substring(0, 13)) -eq $Code.Substring(13))
}

# Length-prefixed digits, 96 bits: "0E" + 14 digits + "00000000".
function Get-Epc([string]$Code) {
    return (('{0:X2}' -f $Code.Length) + $Code).PadRight(24, '0')
}

# --- Collect barcodes -------------------------------------------------------
$codes = New-Object System.Collections.Generic.List[string]
# "powershell -File" passes "a,b" as one string, so split on commas here.
foreach ($b in ($Barcode -split ',')) { if ($b.Trim()) { $codes.Add($b.Trim()) } }
# Titles to print on the label, by barcode (only from -CsvFile).
$titles = @{}
if ($CsvFile) {
    foreach ($row in (Import-Csv -Path $CsvFile -Encoding UTF8)) {
        $b = "$($row.barcode)".Trim()
        if ($b) { $codes.Add($b); $titles[$b] = "$($row.title)" }
    }
}
if ($Count -gt 0) {
    if ($Start -lt 0 -or ($Start + $Count - 1) -gt 99999999) { throw '-Start/-Count must give item numbers 0..99999999' }
    if ($Institution -notmatch '^\d{4}$') { throw '-Institution must be 4 digits' }
    for ($n = $Start; $n -lt $Start + $Count; $n++) {
        $payload = '3' + $Institution + $n.ToString('00000000')
        $codes.Add($payload + (Get-CheckDigit $payload))
    }
}
if ($codes.Count -eq 0) { throw 'Give -Barcode, -CsvFile, or -Start and -Count' }

# ZPL's default font is ASCII: fold accents (ê -> e), drop ZPL control characters ^ and ~.
function Get-LabelText([string]$Text, [int]$Max) {
    $d = $Text.Normalize([Text.NormalizationForm]::FormD)
    $t = -join ($d.ToCharArray() | Where-Object {
        [Globalization.CharUnicodeInfo]::GetUnicodeCategory($_) -ne 'NonSpacingMark' })
    $t = ($t -replace '[^\x20-\x7E]', '' -replace '[\^~]', '' -replace '\s+', ' ').Trim()
    $t = $t.TrimEnd(' ', '/', ':', ';', ',', '=')   # catalogue punctuation: "Title /"
    if ($t.Length -gt $Max) { $t = $t.Substring(0, $Max - 1).TrimEnd() + '.' }
    return $t
}

foreach ($c in $codes) {
    if ($c -notmatch '^\d{1,22}$') { throw "'$c': only numeric barcodes of 1-22 digits fit a 96-bit length-prefixed EPC" }
    if (-not (Test-Standard $c)) { Write-Warning "'$c' is not a standard-14 barcode; writing it exactly as given" }
}

# --- Build ZPL --------------------------------------------------------------
$labels = foreach ($c in $codes) {
    $epc = Get-Epc $c
    if (Test-Standard $c) {
        $text = '{0} {1} {2} {3}' -f $c.Substring(0, 1), $c.Substring(1, 4), $c.Substring(5, 8), $c.Substring(13)
    } else {
        $text = $c
    }
    if ($titles.ContainsKey($c) -and $titles[$c]) { $line3 = Get-LabelText $titles[$c] 32 }
    else { $line3 = 'RFID TEST' }
    [pscustomobject]@{
        Barcode = $c
        Epc     = $epc
        Zpl     = @"
^XA
^PW400^LL200^LH0,0
^RFW,H^FD$epc^FS
^FN1^RFR,H^FS^HV1,,EPC:^FS
^FO20,15^BY2,2.5,80^BKN,N,80,N,N,A,A^FD$c^FS
^FO20,105^A0N,28,28^FD$text^FS
^FO20,140^A0N,22,22^FD$line3^FS
^FO20,170^A0N,18,18^FDEPC $epc^FS
^PQ1
^XZ
"@
    }
}
$zpl = ($labels | ForEach-Object { $_.Zpl }) -join "`r`n"

if ($OutFile) {
    [System.IO.File]::WriteAllText($OutFile, $zpl, [System.Text.Encoding]::ASCII)
    Write-Host "Wrote $($labels.Count) label(s) to $OutFile"
}

# --- Send -------------------------------------------------------------------
if ($PrinterHost) {
    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect($PrinterHost, $Port)
    $reply = ''
    try {
        $stream = $client.GetStream()
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($zpl)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        Write-Host "Sent $($labels.Count) label(s) to ${PrinterHost}:$Port, waiting for tag read-back..."
        $stream.ReadTimeout = 15000
        $buf = New-Object byte[] 1024
        try {
            while (([regex]::Matches($reply, 'EPC:')).Count -lt $labels.Count) {
                $n = $stream.Read($buf, 0, $buf.Length)
                if ($n -le 0) { break }
                $reply += [System.Text.Encoding]::ASCII.GetString($buf, 0, $n)
            }
        } catch {
            # Read timed out: report what arrived.
        }
    } finally {
        $client.Close()
    }
    Write-Verbose "Raw reply: $reply"
    # Replies arrive back to back with no separator ("EPC:<hex>EPC:<hex>..."). Matching
    # hex digits would swallow the "E" of the next "EPC:", so split on the header instead.
    $got = @($reply -split 'EPC:' | Select-Object -Skip 1 |
        ForEach-Object { ([regex]::Match($_, '^[0-9A-Fa-f]*')).Value.ToUpper() })
    for ($i = 0; $i -lt $labels.Count; $i++) {
        $l = $labels[$i]
        if ($i -ge $got.Count) { $status = 'NO REPLY'; $read = '' }
        else {
            $read = $got[$i]
            if ($read.StartsWith($l.Epc)) { $status = 'OK' } else { $status = 'MISMATCH' }
        }
        [pscustomobject]@{ Barcode = $l.Barcode; Written = $l.Epc; ReadBack = $read; Status = $status }
    }
} elseif ($PrinterShare) {
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        [System.IO.File]::WriteAllText($tmp, $zpl, [System.Text.Encoding]::ASCII)
        cmd /c copy /b "$tmp" "$PrinterShare" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "copy to $PrinterShare failed" }
        Write-Host "Sent $($labels.Count) label(s) to $PrinterShare (no read-back over USB; check the tags with the R220)"
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
} elseif (-not $OutFile) {
    $zpl
}
