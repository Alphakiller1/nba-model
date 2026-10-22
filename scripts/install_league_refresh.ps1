# Registers the twice-daily stats.nba.com snapshot refresh with Windows Task Scheduler.
# stats.nba.com answers this machine but not GitHub's runners, so the Synergy play
# types and player tracking are pulled here and pushed (scripts/refresh_league_snapshot.py).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_league_refresh.ps1
#   schtasks /Query /TN "Chase NBA League Snapshot"      # check it
#   schtasks /Delete /TN "Chase NBA League Snapshot" /F  # remove it

$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Script = Join-Path $Repo "scripts\refresh_league_snapshot.py"
if (-not (Test-Path $Python)) { throw "No virtualenv at $Python - run: python -m venv .venv; .venv\Scripts\pip install -e .[dev]" }

# cmd /c keeps a console for the child processes (git) and captures every line,
# including a crash before the script's own log opens.
$RunLog = Join-Path $Repo "data\nbastats\task.log"
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$Python`" -u `"$Script`" >> `"$RunLog`" 2>&1`"" -WorkingDirectory $Repo
# 10:15 and 17:15 local: after the league posts the previous night's tracking, and
# again before tip-off. StartWhenAvailable runs a missed slot once the PC is back on.
$Triggers = @(
  (New-ScheduledTaskTrigger -Daily -At "10:15"),
  (New-ScheduledTaskTrigger -Daily -At "17:15")
)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "Chase NBA League Snapshot" -Action $Action -Trigger $Triggers `
  -Settings $Settings -Description "Refresh stats.nba.com tables for nba-model and push them" -Force | Out-Null
Write-Output "Registered 'Chase NBA League Snapshot' (10:15 and 17:15 daily)."

