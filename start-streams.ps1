    while (-not (Test-Connection -ComputerName youtube.com -Count 1)) {
       Write-Host "Network disconnected. Waiting..."
       Start-Sleep -Seconds 5 # Wait for 5 seconds before checking again
    }

    # put a 30 second delay at boot to make sure everything has started up
    Start-Sleep -Seconds 30

    Set-Location "C:\GCC-Streaming\obs-studio-sheet-1\bin\64bit\"
    Start-Process -FilePath "C:\GCC-Streaming\obs-studio-sheet-1\bin\64bit\obs64.exe" -ArgumentList "-m --startstreaming"
    Start-Sleep -Seconds 5

    Set-Location "C:\GCC-Streaming\obs-studio-sheet-2\bin\64bit\"
    Start-Process -FilePath "C:\GCC-Streaming\obs-studio-sheet-2\bin\64bit\obs64.exe" -ArgumentList "-m --startstreaming"
    Start-Sleep -Seconds 5

    Set-Location "C:\GCC-Streaming\obs-studio-sheet-3\bin\64bit\"
    Start-Process -FilePath "C:\GCC-Streaming\obs-studio-sheet-3\bin\64bit\obs64.exe" -ArgumentList "-m --startstreaming"
    Start-Sleep -Seconds 5

    #Set-Location "C:\GCC-Streaming\obs-studio-sheet-4\bin\64bit\"
    #Start-Process -FilePath "C:\GCC-Streaming\obs-studio-sheet-4\bin\64bit\obs64.exe" -ArgumentList "-m --startstreaming"
    #Start-Sleep -Seconds 5    

    #Set-Location "C:\GCC-Streaming\obs-studio-sheet-5\bin\64bit\"
    #Start-Process -FilePath "C:\GCC-Streaming\obs-studio-sheet-5\bin\64bit\obs64.exe" -ArgumentList "-m --startstreaming"
