#Requires -RunAsAdministrator
<#
  Daily tasks (local machine clock):
    KalimatiSync      07:00  -> price sync
    KalimatiDigestAM  07:30  -> system notification summary
    KalimatiDigestPM  19:30  -> system notification summary

  Run as Administrator:
    Set-ExecutionPolicy -Scope Process Bypass
    .\install\windows\register_tasks.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $RepoRoot "scripts\kalimati_schedule.py"

if (-not (Test-Path $Python)) { Write-Error "Missing venv Python: $Python" }
if (-not (Test-Path $Runner)) { Write-Error "Missing $Runner" }

function Register-DailyTask($Name, $Time, $Arg) {
    $tr = "`"$Python`" `"$Runner`" $Arg"
    schtasks /Create /F /TN $Name /SC DAILY /ST $Time /TR $tr /RL LIMITED | Write-Host
}

Register-DailyTask "KalimatiSync" "07:00" "sync"
Register-DailyTask "KalimatiDigestAM" "07:30" "digest-am"
Register-DailyTask "KalimatiDigestPM" "19:30" "digest-pm"

Write-Host "Done. Check: schtasks /Query /TN KalimatiSync /V"
