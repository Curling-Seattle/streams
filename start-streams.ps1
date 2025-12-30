# Script to launch OBS processes for multiple streams. Each stream shows
# a composite view of a curling sheet.

$dbg = $false
$initialDelay = 30
$pollEvery = 60
$localConfig = "$PSScriptRoot\start-streams-config.ps1"
if (-not (Test-Path $localConfig)) {
    Write-Host "No $localConfig file found"
    $webhookURL = ""
} else {
    . $localConfig
}

Write-Host "Starting OBS process initialization script ..."

# First, verify that YouTube is reachable over the network
while (-not (Test-Connection -ComputerName youtube.com -Count 1)) {
    Write-Host "No network connection to youtube. Waiting..."
    Start-Sleep -Seconds 5 # Wait for 5 seconds before checking again
}
Write-Host "Connection to youtube works."

# Wait for a certain period to make sure everything has started up
if ($initialDelay -gt 0) {
    Write-Host "Sleeping $initialDelay seconds at start up"
    Start-Sleep -Seconds $initialDelay
}

function Notify-GCC {
    param ([string] $mesg)
    $msg = "On $(Get-Date):`n$mesg"
    if ($dbg -or $webhookURL -eq "") {
	$payload = @{text = "***Debug*** $msg"} | ConvertTo-Json
	Write-Host Invoke-RestMethod -Uri `$webhookURL -Method Post `
	  -Body $payload -ContentType "application/json"
    } else {
	Write-Host "Sending '$msg' to web-cast channel"
	$payload = @{text = $msg} | ConvertTo-Json
	Invoke-RestMethod -Uri $webhookURL -Method Post `
	  -Body $payload -ContentType "application/json"
    }
}

# On each stream server, select which sheets to monitor
$sheets = 4, 5

# Associative array to may process IDs to sheet numbers
$sheet_from_pid = @{}

# Build a list of OBS processes that create the composite view for the streams
$proc_list = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
foreach ($i in $sheets) {
    $sheetPath = "C:\GCC-Streaming\obs-studio-sheet-$i\bin\64bit"
    $exePath = "$sheetPath\obs64.exe"
    $running = (Get-Process | Where-Object { $_.Path -eq $exePath }).Count -gt 0
    if ($running) {
	Write-Host "Found process running in $exePath. Skipping OBS launch."
	Start-Sleep -Seconds 2
    } else {
	Write-Host "Starting OBS process for sheet $i ..."
	if ($dbg) {
	    $proc = Start-Process `
	      "/Applications/Emacs.app/Contents/MacOS/Emacs" `
	      -ArgumentList "$($i)away-horiz.png" -PassThru
	} else {
	    Set-Location $sheetPath
	    $proc = Start-Process -FilePath $exePath `
	      -ArgumentList "-m --startstreaming" -PassThru
	}
	$proc_list.Add($proc)
	$sheet_from_pid[$proc.Id] = $i
	Start-Sleep -Seconds 5
    }
}

Set-Location $PSScriptRoot

if ($proc_list.Count -gt 0) {
    # Keep this script going until all processes have exited
    Write-Host "Waiting until OBS processes exit..."
    do {
	$alive = $false
	foreach ($proc in $proc_list) {
	    if (-not $proc.HasExited) {
		$alive = $true 
	    } elseif ($sheet_from_pid[$proc.Id] -gt 0) {
		Notify-GCC("Process died for sheet $($sheet_from_pid[$proc.Id]) (id $($proc.Id))")
		$sheet_from_pid[$proc.Id] = 0
	    } elseif ($dbg) {
		Write-Host "Process ID $($proc.Id) died and sheet_from_pid = $($sheet_from_pid[$proc.Id])"
	    }
	}
	if ($dbg) {
	    Write-Host "Start-Sleep -Seconds $pollEvery"
	}
	Start-Sleep -Seconds $pollEvery
    } while ($alive)

    Notify-GCC("All OBS processes for sheets " + ($sheets -join ', ') +
	       " have either exited or were launched by another script.")
} else {
    Write-Host "No processes launched."
}
