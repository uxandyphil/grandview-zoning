# Schedules run.mjs to run every Sunday at 9:07 PM on this PC (or as soon as possible after, if the PC was off).
#   powershell -ExecutionPolicy Bypass -File schedule-windows.ps1          turn the schedule on (or update it)
#   powershell -ExecutionPolicy Bypass -File schedule-windows.ps1 remove   turn it off
$name = "Grandview permits"
if ($args[0] -eq "remove") { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "Schedule removed."; exit }
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { "Node.js isn't installed (see README.md, step 1)."; exit 1 }
$action = New-ScheduledTaskAction -Execute $node -Argument "run.mjs" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9:07pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"Scheduled: every Sunday at 9:07 PM (or as soon as possible after, if the PC was off)."
"Run it now:   Start-ScheduledTask -TaskName '$name'"
"Progress:     $dir\runner.log"
