# Script to launch OBS processes for multiple streams. Each stream shows
# a composite view of a curling sheet.

$dbg = $false
$localConfig = "$PSScriptRoot\start-streams-config.ps1"
if (-not (Test-Path $localConfig)) {
    Write-Host "No $localConfig file found"
    $webhookURL = ""
} else {
    . $localConfig
}

# First, verify that YouTube is reachable over the network
while (-not (Test-Connection -ComputerName youtube.com -Count 1)) {
    Write-Host "Network disconnected. Waiting..."
    Start-Sleep -Seconds 5 # Wait for 5 seconds before checking again
}

# Wait for 30 seconds to make sure everything has started up
if ($dbg) {
    Write-Host "Skip initial sleep period"
} else {
    Start-Sleep -Seconds 30
}

function Notify-GCC {
    param ([string] $mesg)
    $msg = "On $(Get-Date):`n$mesg"
    if ($dbg -or $webhookURL -eq "") {
	$payload = @{text = "***Debug*** $msg"} | ConvertTo-Json
	Write-Host Invoke-RestMethod -Uri `$webhookURL -Method Post `
	  -Body $payload -ContentType "application/json"
    } else {
	$payload = @{text = $msg} | ConvertTo-Json
	Invoke-RestMethod -Uri $webhookURL -Method Post `
	  -Body $payload -ContentType "application/json"
    }
}

# On each stream server, select a range of sheets to monitor
$first_sheet = 4
$last_sheet = 5

# Build a list of OBS processes that create the composite view for the streams
$proc_list = [System.Collections.Generic.List[int]]::new()
for ($i = $first_sheet; $i -le $last_sheet; $i++) {
    Write-Host Set-Location "C:\GCC-Streaming\obs-studio-shee-$i\bin\64bit\"
    Write-Host "`$proc_list.Add(StartProcess `
      -FilePath `"C:\GCC-Streaming\obs-studio-sheet-$i\bin\64bit\obs64.exe`" `
      -ArgumentList `"-m --startstreaming`" -PassThru)"
    Write-Host "Register-ObjectEvent -InputObject `$proc_list[-1] `
      -EventName Exited -Action {Notify-GCC(`"Sheet $i OBS process died`")} "
}

# Keep this script going until all processes have exited
$proc_list = @("proc1", "proc2")
$alive = $false
$count = 0
do {
    $alive = $false
    foreach ($proc in $proc_list) {
	if ($dbg) {
            Write-Host "if (-not `$$proc.HasExited) { `
	      `$alive = `$true `
	      } "
	} else {
	    if (-not $proc.HasExited) {
		$alive = $true 
	    }
	}
    }
    if ($dbg) {
	Write-Host "Start-Sleep -Seconds 60"
	$alive = $count++ -lt 3
    } else {
	Start-Sleep -Seconds 60
    }
} while ($alive)

Notify-GCC("All OBS processes for sheets $first_sheet to $last_sheet have exited.")
