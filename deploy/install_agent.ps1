<#
.SYNOPSIS
Install the Koha RFID desk agent on a circulation desk PC, so it runs in the
background from boot. The librarian never starts anything.

.DESCRIPTION
Normally run on a desk PC with the command shown in Koha on the RFID desk
plugin's Configure page. That command downloads this script from the Koha server
and runs it with -Koha <staff address>. The desk PC doesn't need the repo.

Run it from an elevated (Run as administrator) PowerShell. It:
  1. finds Python 3.12, or installs it for all users with winget,
  2. downloads the agent package from the RFID desk plugin on the Koha server
     (-Koha / -Update), or installs it from the repo this script is in,
  3. creates C:\koha-rfid\ with its own Python venv and installs the agent
     (with sllurp for the Impinj R220, from PyPI),
  4. creates C:\koha-rfid\agent.ini the first time only: it asks for the
     reader type, the reader address (or COM port), pad power and printer address,
  5. copies itself to C:\koha-rfid\install_agent.ps1 for the actions below,
  6. registers the Scheduled Task "Koha RFID agent": starts at boot, runs
     hidden as SYSTEM with no login needed, and is re-checked every 5 minutes
     so it comes back if it ever stops,
  7. starts it and checks http://127.0.0.1:8765/status.

The agent itself reconnects to the reader when it's switched off or unplugged.

Reader types: r220 (Impinj R220 on the network, the test reader) or chafon
(Chafon CF606 on USB). The Chafon CF606 is a candidate only: it is NOT
confirmed for any implementation, has not been tested, and its driver has not
been written, so with type chafon the agent reports the reader as not connected.

Other actions (C:\koha-rfid\install_agent.ps1 <action>):
  -Update    download and install the agent version now on the Koha server
             (the address is the first allowed_origins entry in agent.ini).
             agent.ini is kept.
  -Status    show the task and the agent's /status
  -Restart   restart after editing agent.ini
  -Pause     stop and disable the agent, e.g. to free the R220 for the
             testing\ scripts or a stocktake (it allows only one connection)
  -Resume    enable and start it again
  -Uninstall remove the task (C:\koha-rfid\ is left; delete it by hand)

.EXAMPLE
powershell -ExecutionPolicy Bypass -File install_agent.ps1 -Koha https://staff.library.example
.EXAMPLE
C:\koha-rfid\install_agent.ps1 -Update
.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\install_agent.ps1
(development: install from this repo)
.EXAMPLE
powershell -ExecutionPolicy Bypass -File C:\koha-rfid\install_agent.ps1 -Pause
#>
[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    # Koha staff interface address, exactly as in the browser's address bar.
    [Parameter(ParameterSetName = 'Install')][string]$Koha,
    [Parameter(ParameterSetName = 'Update')][switch]$Update,
    [Parameter(ParameterSetName = 'Install')][Parameter(ParameterSetName = 'Update')][string]$Python,
    [Parameter(ParameterSetName = 'Install')][Parameter(ParameterSetName = 'Update')][switch]$NoTask,
    # First install only; asked for when not given. 'none' leaves it empty.
    # -ReaderType r220 (default) or chafon. -Power is the R220 power-table index,
    # or dBm (5-26) for chafon. -SerialPort (e.g. COM3) is for chafon only.
    [Parameter(ParameterSetName = 'Install')][string]$ReaderType,
    [Parameter(ParameterSetName = 'Install')][string]$ReaderHost,
    [Parameter(ParameterSetName = 'Install')][string]$SerialPort,
    [Parameter(ParameterSetName = 'Install')][string]$Power,
    [Parameter(ParameterSetName = 'Install')][string]$PrinterHost,
    [string]$InstallDir = 'C:\koha-rfid',
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Pause')][switch]$Pause,
    [Parameter(ParameterSetName = 'Resume')][switch]$Resume,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Given = @($PSBoundParameters.Keys)     # to tell "-PrinterHost ''" from not given
# Filled in by deploy/build_plugin.py in the copy inside the .kpz; empty in the repo.
$BundledWheel = ''
$TaskName = 'Koha RFID agent'
$Repo = Split-Path -Parent $PSScriptRoot
$Ini = Join-Path $InstallDir 'agent.ini'
$StaticPath = '/api/v1/contrib/rfiddesk/static/agent'
# The ZD500R's usual address here; '1' at the printer question (or -PrinterHost 1) picks it.
$ZebraDefault = '192.168.1.2'
$ChafonWarning = 'The Chafon CF606 is a candidate only: it is NOT confirmed for any implementation, has not been tested, and its driver has not been written. The agent will report the reader as not connected. See docs/hardware/chafon-cf606.md.'

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Assert-Admin {
    if (-not (Test-Admin)) { throw 'Run this from an elevated PowerShell (right-click > Run as administrator).' }
}

# ---- agent.ini ----------------------------------------------------------------
function Get-IniValue([string]$Path, [string]$Section, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    $in = $false
    foreach ($l in [IO.File]::ReadAllLines($Path)) {
        if ($l -match '^\s*\[(.+?)\]') { $in = ($Matches[1].Trim() -eq $Section); continue }
        if ($in -and $l -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") { return $Matches[1].Trim() }
    }
    return $null
}

# Sets "key = value" in a section. A key that is only there commented out
# ("; key = ...") is uncommented and set.
function Set-IniValue([string]$Path, [string]$Section, [string]$Key, [string]$Value) {
    $lines = [IO.File]::ReadAllLines($Path)
    foreach ($pattern in @("^\s*$([regex]::Escape($Key))\s*=", "^\s*;\s*$([regex]::Escape($Key))\s*=")) {
        $in = $false
        for ($i = 0; $i -lt $lines.Length; $i++) {
            if ($lines[$i] -match '^\s*\[(.+?)\]') { $in = ($Matches[1].Trim() -eq $Section); continue }
            if ($in -and $lines[$i] -match $pattern) {
                $lines[$i] = if ($Value) { "$Key = $Value" } else { "$Key =" }
                # UTF-8 without a BOM: the agent's configparser doesn't expect one.
                [IO.File]::WriteAllLines($Path, $lines)
                return
            }
        }
    }
    throw "No '$Key' in [$Section] of $Path"
}

function Get-ListenPort {
    $p = Get-IniValue $Ini 'agent' 'listen_port'
    if ($p -match '^\d+$') { return [int]$p }
    return 8765
}

function Get-SavedKoha {
    $o = Get-IniValue $Ini 'agent' 'allowed_origins'
    if (-not $o) { return $null }
    return ($o -split '[,\s]+' | Where-Object { $_ } | Select-Object -First 1)
}

# Read-Host throws in a -NonInteractive session; then the default is used.
function Ask([string]$Question, [string]$Default) {
    $shown = if ($Default) { $Default } else { 'none' }
    try { $a = Read-Host "$Question [Enter = $shown, or type none]" } catch { return $Default }
    if ([string]::IsNullOrWhiteSpace($a)) { return $Default }
    return $a.Trim()
}

function New-AgentIni([string]$Example) {
    Copy-Item $Example $Ini
    Write-Host "`nFirst install: a few settings for $Ini (change them later in that file, then -Restart)."
    $origin = if ($Koha) { $Koha } else {
        Ask 'Koha staff interface address, as in the browser address bar' (Get-IniValue $Ini 'agent' 'allowed_origins')
    }
    $rt = if ($Given -contains 'ReaderType') { $ReaderType } else {
        Ask 'Reader type: r220 (Impinj R220, network) or chafon (Chafon CF606, USB: NOT confirmed, no driver yet)' (Get-IniValue $Ini 'reader' 'type')
    }
    $rt = "$rt".Trim().ToLower()
    if (-not $rt -or $rt -eq 'none') { $rt = 'r220' }
    if (@('r220', 'chafon') -notcontains $rt) { Remove-Item $Ini; throw "Reader type must be r220 or chafon, not '$rt'." }
    if ($rt -eq 'chafon') {
        Write-Warning $ChafonWarning
        $sp = if ($Given -contains 'SerialPort') { $SerialPort } else {
            Ask 'Chafon CF606 COM port (Device Manager > Ports), e.g. COM3' (Get-IniValue $Ini 'reader' 'serial_port')
        }
        if ($sp -eq 'none') { $sp = '' }
        if ($sp -and $sp -notmatch '^COM\d{1,3}$') { Remove-Item $Ini; throw "The COM port must look like COM3, not '$sp'." }
        $pw = if ($Given -contains 'Power') { $Power } else { Ask 'Pad power in dBm (5-26)' '20' }
        if (-not $pw -or $pw -eq 'none') { $pw = '20' }
        if ($pw -notmatch '^\d+$' -or [int]$pw -lt 5 -or [int]$pw -gt 26) { Remove-Item $Ini; throw "Pad power must be 5-26 dBm, not '$pw'." }
    } else {
        $rh = if ($Given -contains 'ReaderHost') { $ReaderHost } else {
            Ask 'R220 reader IP address' (Get-IniValue $Ini 'reader' 'host')
        }
        $pw = if ($Given -contains 'Power') { $Power } else {
            Ask 'Pad power (index from the field test; 0 = max 32.5 dBm, 21 = 15 dBm, 41 = 20 dBm)' (Get-IniValue $Ini 'reader' 'power')
        }
        if (-not $pw -or $pw -eq 'none') { $pw = Get-IniValue $Ini 'reader' 'power' }
        if ($pw -notmatch '^\d+$') { Remove-Item $Ini; throw "Pad power must be a whole number, not '$pw'." }
    }
    $ph = if ($Given -contains 'PrinterHost') { $PrinterHost } else {
        $d = Get-IniValue $Ini 'printer' 'host'
        $shown = if ($d) { $d } else { 'none' }
        try {
            $a = Read-Host "ZD500R label printer IP address, only on desks that print labels [Enter = $shown, 1 = $ZebraDefault (Zebra default), or type an IP]"
        } catch { $a = '' }
        if ([string]::IsNullOrWhiteSpace($a)) { $d } else { $a.Trim() }
    }
    if ($ph -eq '1') { $ph = $ZebraDefault }
    if ($rh -eq 'none') { $rh = '' }
    if ($ph -eq 'none') { $ph = '' }
    Set-IniValue $Ini 'agent' 'allowed_origins' $origin
    Set-IniValue $Ini 'reader' 'type' $rt
    if ($rt -eq 'chafon') {
        Set-IniValue $Ini 'reader' 'host' ''
        Set-IniValue $Ini 'reader' 'serial_port' ("$sp".ToUpper())
        Set-IniValue $Ini 'reader' 'power_dbm' $pw
    } else {
        Set-IniValue $Ini 'reader' 'host' $rh
        Set-IniValue $Ini 'reader' 'power' $pw
    }
    Set-IniValue $Ini 'printer' 'host' $ph
    Write-Host "Created $Ini"
}

# ---- status ---------------------------------------------------------------------
function Show-AgentStatus([int]$WaitSeconds = 0) {
    $url = "http://127.0.0.1:$(Get-ListenPort)/status"
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        try {
            $s = Invoke-RestMethod -Uri $url -TimeoutSec 3
            $r = $s.reader
            $state = if ($r.connected) { 'connected' } else { "NOT connected ($($r.error))" }
            Write-Host "Agent $($s.version) is running at $url"
            Write-Host "  reader : $($r.type) $($r.source) - $state"
            Write-Host "  printer: $(if ($s.printer.configured) { $s.printer.host } else { 'none configured' })"
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    Write-Warning "Agent not answering at $url. See $InstallDir\agent.log"
    return $false
}

# ---- Python ---------------------------------------------------------------------
function Find-Python {
    if ($Python) { return $Python }
    $candidates = @(
        "$env:ProgramFiles\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    try { $p = (& py -3.12 -c 'import sys; print(sys.executable)') 2>$null; if ($p) { return $p.Trim() } } catch {}
    return $null
}

function Get-Python {
    $py = Find-Python
    if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Python 3.12 not found: installing it for all users with winget ...'
        # Out-Host: winget's output must not become part of this function's
        # return value (PowerShell returns everything a function doesn't capture).
        & winget install -e --id Python.Python.3.12 --scope machine --silent `
            --accept-package-agreements --accept-source-agreements | Out-Host
        $py = Find-Python
    }
    if (-not $py) {
        throw 'Python 3.12 not found. Install it for all users (python.org, "Install for all users"), or pass -Python <path to python.exe>.'
    }
    if ($py -isnot [string] -or -not (Test-Path -LiteralPath $py -PathType Leaf)) {
        throw "Python 3.12 not found at '$py'. Pass -Python <path to python.exe>."
    }
    if ($py -like '*WindowsApps*') {
        throw "$py is the Microsoft Store Python, which the SYSTEM account can't run. Install Python 3.12 from python.org and pass -Python."
    }
    return $py
}

# ---- download from Koha -----------------------------------------------------------
function Get-KohaFile([string]$Name, [string]$Dir) {
    $out = Join-Path $Dir $Name
    $url = "$Koha$StaticPath/$Name"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out
    } catch {
        throw "Download failed: $url`n  $($_.Exception.Message)`n  Is the RFID desk plugin installed, and was plack restarted after uploading it?"
    }
    return $out
}

# ---- actions other than install ---------------------------------------------------
switch ($PSCmdlet.ParameterSetName) {
    'Status' {
        Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
            Format-Table TaskName, State -AutoSize
        [void](Show-AgentStatus); return
    }
    'Restart' {
        Assert-Admin
        Stop-ScheduledTask -TaskName $TaskName; Start-ScheduledTask -TaskName $TaskName
        [void](Show-AgentStatus 20); return
    }
    'Pause' {
        Assert-Admin
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
        Stop-ScheduledTask -TaskName $TaskName
        Write-Host 'Agent stopped and disabled. The reader is free for the testing\ scripts and stocktake. Use -Resume to bring it back.'
        return
    }
    'Resume' {
        Assert-Admin
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        [void](Show-AgentStatus 20); return
    }
    'Uninstall' {
        Assert-Admin
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Removed the '$TaskName' task. $InstallDir was left in place."
        } else { Write-Host "No '$TaskName' task found." }
        return
    }
}

# ---- install / upgrade ---------------------------------------------------------------
if (-not $NoTask) { Assert-Admin }

# Where the agent comes from: the Koha server (-Koha, -Update) or this repo.
$fromRepo = -not $Koha -and -not $Update -and (Test-Path (Join-Path $Repo 'pyproject.toml'))
if (-not $fromRepo -and -not $Koha) {
    $Koha = Get-SavedKoha
    if (-not $Koha) { throw "No Koha address saved in $Ini. Run again with -Koha <staff interface address>." }
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($fromRepo) {
    $package = "$Repo[r220]"
    $example = Join-Path $PSScriptRoot 'agent.example.ini'
    Write-Host "Installing the agent from the repo $Repo"
} else {
    $Koha = $Koha.Trim().TrimEnd('/')
    if ($Koha -notmatch '^https?://[^/\s]+$') {
        throw "-Koha must be the staff interface address only, e.g. https://staff.library.example (got '$Koha')."
    }
    # Windows PowerShell 5.1 may not offer TLS 1.2 by default.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $dl = Join-Path $InstallDir 'download'
    if (Test-Path $dl) { Remove-Item -Recurse -Force $dl }
    New-Item -ItemType Directory -Force -Path $dl | Out-Null
    Write-Host "Downloading the agent from $Koha ..."
    $newInstaller = Get-KohaFile 'install_agent.ps1' $dl

    # The server's installer may be newer than this one: hand over to it.
    if ($PSCommandPath -and ((Get-FileHash $newInstaller).Hash -ne (Get-FileHash $PSCommandPath).Hash)) {
        Write-Host 'Running the installer from the Koha server ...'
        $pass = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $newInstaller, '-Koha', $Koha, '-InstallDir', $InstallDir)
        foreach ($n in 'Python', 'ReaderType', 'ReaderHost', 'SerialPort', 'Power', 'PrinterHost') {
            if ($Given -contains $n) {
                $v = Get-Variable -Name $n -ValueOnly
                # Windows PowerShell drops empty arguments; 'none' means empty.
                $pass += @("-$n", $(if ($v) { $v } else { 'none' }))
            }
        }
        if ($NoTask) { $pass += '-NoTask' }
        & (Get-Process -Id $PID).Path @pass
        exit $LASTEXITCODE
    }

    $m = Select-String -Path $newInstaller -Pattern "^\`$BundledWheel = '([^']+)'" | Select-Object -First 1
    if (-not $m) { throw "The installer on $Koha names no agent package. Rebuild the plugin with deploy/build_plugin.py." }
    $wheel = Get-KohaFile $m.Matches[0].Groups[1].Value $dl
    $example = Get-KohaFile 'agent.example.ini' $dl
    $package = "$wheel[r220]"
}

$py = Get-Python
Write-Host "Using $py"

$venvPy = Join-Path $InstallDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host 'Creating the venv ...'
    & $py -m venv (Join-Path $InstallDir 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}

# Stop a running agent so its files can be replaced (not with -NoTask: that
# install isn't the task's).
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $NoTask -and $task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }

Write-Host 'Installing the agent and its Python packages (from PyPI) ...'
& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venvPy -m pip install --quiet --disable-pip-version-check --upgrade $package
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

if (-not (Test-Path $Ini)) {
    New-AgentIni $example
} else {
    Write-Host "Kept the existing $Ini"
}

# Keep a copy of this script next to the agent for -Status, -Restart, -Update, ...
$self = Join-Path $InstallDir 'install_agent.ps1'
if ($PSCommandPath -and ((Resolve-Path $PSCommandPath).Path -ne $self)) {
    Copy-Item $PSCommandPath $self -Force
}

if ($NoTask) { Write-Host "Installed to $InstallDir (no Scheduled Task, -NoTask)."; return }

$pythonw = Join-Path $InstallDir 'venv\Scripts\pythonw.exe'      # no console window
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m koha_rfid.agent --config `"$Ini`"" -WorkingDirectory $InstallDir
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    # Safety net: re-check every 5 minutes; with IgnoreNew this only starts the
    # agent if it isn't already running.
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650))
)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings -Description 'Koha RFID desk agent: reader and label printer for the Koha RFID desk plugin. Manage with C:\koha-rfid\install_agent.ps1.' `
    -Force | Out-Null
Write-Host "Registered the '$TaskName' task (starts at boot, runs as SYSTEM)."

Start-ScheduledTask -TaskName $TaskName
if (Show-AgentStatus 30) {
    Write-Host "`nDone. The agent will start by itself from now on."
    Write-Host "Manage it with: $self -Status | -Restart | -Pause | -Resume | -Update"
}
