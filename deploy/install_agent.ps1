<#
.SYNOPSIS
Install the Koha RFID desk agent on a circulation desk PC, so it runs in the
background from boot. The librarian never starts anything.

.DESCRIPTION
Run once per desk PC from an elevated (Run as administrator) PowerShell, and
again after updating the repo to upgrade:
  1. creates C:\koha-rfid\ with its own Python venv and installs the agent
     (with sllurp for the R220) from this repo,
  2. copies agent.example.ini to C:\koha-rfid\agent.ini (only the first time),
  3. registers the Scheduled Task "Koha RFID agent": starts at boot, runs
     hidden as SYSTEM with no login needed, and is re-checked every 5 minutes
     so it comes back if it ever stops,
  4. starts it and checks http://127.0.0.1:8765/status.

The agent itself reconnects to the reader when it's switched off or unplugged.

Other actions:
  -Status    show the task and the agent's /status
  -Restart   restart after editing agent.ini
  -Pause     stop and disable the agent, e.g. to free the R220 for the
             testing\ scripts (the R220 allows only one connection)
  -Resume    enable and start it again
  -Uninstall remove the task (C:\koha-rfid\ is left; delete it by hand)

.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\install_agent.ps1
.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\install_agent.ps1 -Python "C:\Program Files\Python312\python.exe"
.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\install_agent.ps1 -Pause
#>
[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    [Parameter(ParameterSetName = 'Install')][string]$Python,
    [Parameter(ParameterSetName = 'Install')][switch]$NoTask,
    [string]$InstallDir = 'C:\koha-rfid',
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Pause')][switch]$Pause,
    [Parameter(ParameterSetName = 'Resume')][switch]$Resume,
    [Parameter(ParameterSetName = 'Uninstall')][switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Koha RFID agent'
$Repo = Split-Path -Parent $PSScriptRoot
$Ini = Join-Path $InstallDir 'agent.ini'

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Assert-Admin {
    if (-not (Test-Admin)) { throw 'Run this from an elevated PowerShell (right-click > Run as administrator).' }
}

function Get-ListenPort {
    if (Test-Path $Ini) {
        $m = Select-String -Path $Ini -Pattern '^\s*listen_port\s*=\s*(\d+)' | Select-Object -First 1
        if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    }
    return 8765
}

function Show-AgentStatus([int]$WaitSeconds = 0) {
    $url = "http://127.0.0.1:$(Get-ListenPort)/status"
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        try {
            $s = Invoke-RestMethod -Uri $url -TimeoutSec 3
            $r = $s.reader
            $state = if ($r.connected) { 'connected' } else { "NOT connected ($($r.error))" }
            Write-Host "Agent $($s.version) is running at $url"
            Write-Host "  reader : $($r.source) - $state"
            Write-Host "  printer: $(if ($s.printer.configured) { $s.printer.host } else { 'none configured' })"
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    Write-Warning "Agent not answering at $url. See $InstallDir\agent.log"
    return $false
}

function Find-Python {
    if ($Python) { return $Python }
    $candidates = @(
        "$env:ProgramFiles\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    try { $p = (& py -3.12 -c 'import sys; print(sys.executable)') 2>$null; if ($p) { return $p.Trim() } } catch {}
    throw 'Python 3.12 not found. Install it for all users (python.org, "Install for all users"), or pass -Python <path to python.exe>.'
}

# ---- actions other than install ---------------------------------------------
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
        Write-Host 'Agent stopped and disabled. The R220 is free for the testing\ scripts. Use -Resume to bring it back.'
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

# ---- install / upgrade -------------------------------------------------------
if (-not $NoTask) { Assert-Admin }
$py = Find-Python
if ($py -like '*WindowsApps*') {
    throw "$py is the Microsoft Store Python, which the SYSTEM account can't run. Install Python 3.12 from python.org and pass -Python."
}
Write-Host "Using $py"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$venvPy = Join-Path $InstallDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host 'Creating the venv ...'
    & $py -m venv (Join-Path $InstallDir 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}

# Stop a running agent so its files can be replaced.
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }

Write-Host "Installing the agent from $Repo ..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet --upgrade "$Repo[reader]"
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

if (-not (Test-Path $Ini)) {
    Copy-Item (Join-Path $PSScriptRoot 'agent.example.ini') $Ini
    Write-Host "Created $Ini - check the reader/printer addresses and allowed_origins in it."
} else {
    Write-Host "Kept the existing $Ini"
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
    -Settings $settings -Description 'Koha RFID desk agent: reader and label printer for the Koha staff plugin. See docs/desk/desk-setup.md.' `
    -Force | Out-Null
Write-Host "Registered the '$TaskName' task (starts at boot, runs as SYSTEM)."

Start-ScheduledTask -TaskName $TaskName
if (Show-AgentStatus 30) {
    Write-Host "`nDone. The agent will start by itself from now on."
}
