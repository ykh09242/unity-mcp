param(
    [Parameter(Mandatory)][string]$PythonPath,
    [Parameter(Mandatory)][string]$SamplerPath,
    [Parameter(Mandatory)][string]$OutputPath,
    [Parameter(Mandatory)][ValidateSet('complete', 'exited', 'identity', 'failure')][string]$Mode
)
$ErrorActionPreference = 'Stop'
. $SamplerPath
$launch = [Diagnostics.ProcessStartInfo]::new()
$launch.FileName = $PythonPath
$launch.UseShellExecute = $false
$launch.CreateNoWindow = $true
$launch.ArgumentList.Add('-c')
$launch.ArgumentList.Add('import time; time.sleep(20)')
$ownedProcess = [Diagnostics.Process]::Start($launch)
$created = $ownedProcess.StartTime.ToUniversalTime().Ticks
$observation = $null
$reason = 'completed'
try {
    $observation = New-OwnedProcessObservation -ProcessId $ownedProcess.Id -CreationTimeUtcTicks $created -OutputPath $OutputPath
    Add-OwnedProcessObservation $observation
    Start-Sleep -Milliseconds 100
    Add-OwnedProcessObservation $observation
    if ($Mode -eq 'exited') {
        if ($ownedProcess.StartTime.ToUniversalTime().Ticks -ne $created) { throw 'Owned fixture identity changed.' }
        $ownedProcess.Kill()
        if (-not $ownedProcess.WaitForExit(5000)) { throw 'Owned process did not exit.' }
        Add-OwnedProcessObservation $observation
        if ($observation.StopReason -notin @('process_exited', 'process_exited_or_identity_changed')) { throw 'Exited process was not classified.' }
    }
    if ($Mode -eq 'identity') {
        $observation.CreationTimeUtcTicks++
        Add-OwnedProcessObservation $observation
        if ($observation.StopReason -ne 'identity_mismatch') { throw 'Changed identity was accepted.' }
    }
    if ($Mode -eq 'failure') { throw 'Expected controller test failure.' }
}
catch {
    $reason = 'failed'
    if ($Mode -ne 'failure' -or $_.Exception.Message -ne 'Expected controller test failure.') { throw }
}
finally {
    try {
        if ($null -ne $observation) {
            Close-OwnedProcessObservation $observation $reason
            Close-OwnedProcessObservation $observation $reason
            Add-OwnedProcessObservation $observation
            if (-not $observation.Closed -or $null -ne $observation.Writer) { throw 'Observer retained its stream.' }
        }
    }
    finally {
        $ownedProcess.Refresh()
        if (-not $ownedProcess.HasExited) {
            if ($ownedProcess.StartTime.ToUniversalTime().Ticks -ne $created) { throw 'Owned fixture identity changed.' }
            $ownedProcess.Kill()
            if (-not $ownedProcess.WaitForExit(5000)) { throw 'Owned process survived fixture cleanup.' }
        }
        $ownedProcess.Dispose()
    }
}
