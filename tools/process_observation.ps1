# Caller-driven Windows process sampling. No process launch, termination or background worker.
# Bind every point to the owned process identity; stream points instead of retaining history.

function New-OwnedProcessObservation {
    param(
        [Parameter(Mandatory)][ValidateRange(1, 2147483647)][int]$ProcessId,
        [Parameter(Mandatory)][ValidateRange(1, 3155378975999999999)][long]$CreationTimeUtcTicks,
        [Parameter(Mandatory)][string]$OutputPath
    )
    if (-not [IO.Path]::IsPathFullyQualified($OutputPath)) { throw 'An absolute observation output path is required.' }
    $resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
    if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($resolvedOutput))) { throw 'Observation output parent must already exist.' }
    $stream = [IO.File]::Open($resolvedOutput, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false)); $writer.AutoFlush = $true }
    catch { $stream.Dispose(); throw }
    $state = [pscustomobject]@{
        ProcessId = $ProcessId; CreationTimeUtcTicks = $CreationTimeUtcTicks
        Writer = $writer; Clock = [Diagnostics.Stopwatch]::StartNew()
        StartedUtc = [DateTime]::UtcNow; FirstSampleUtc = $null; LastSampleUtc = $null
        Samples = 0; SupportedWorkingSet = 0; SupportedHandles = 0
        LastWorkingSet = $null; LastHandles = $null
        PeakWorkingSet = $null; PeakHandles = $null
        StopReason = $null; Closed = $false
    }
    try {
        $writer.WriteLine((@{ type = 'header'; schema = 'unity-mcp-owned-process-v1'; pid = $ProcessId; creation_time_utc_ticks = $CreationTimeUtcTicks; started_utc = $state.StartedUtc.ToString('o'); scope = 'one_owned_process_excluding_children' } | ConvertTo-Json -Compress))
    }
    catch { $writer.Dispose(); throw }
    return $state
}

function Add-OwnedProcessObservation {
    param([Parameter(Mandatory)]$Observation)
    if ($Observation.Closed -or $null -ne $Observation.StopReason) { return }
    $observedProcess = $null
    $point = $null
    try {
        try { $observedProcess = [Diagnostics.Process]::GetProcessById($Observation.ProcessId) }
        catch [ArgumentException] { $Observation.StopReason = 'process_exited'; return }
        try { $actualCreation = $observedProcess.StartTime.ToUniversalTime().Ticks }
        catch { $Observation.StopReason = 'identity_unavailable'; return }
        if ($actualCreation -ne $Observation.CreationTimeUtcTicks) { $Observation.StopReason = 'identity_mismatch'; return }
        $observedProcess.Refresh()
        if ($observedProcess.HasExited) { $Observation.StopReason = 'process_exited'; return }
        $workingSet = $null; $handles = $null
        $workingSetStatus = 'unavailable'; $handleStatus = 'unavailable'
        try {
            if ($observedProcess.WorkingSet64 -gt 0) { $workingSet = $observedProcess.WorkingSet64; $workingSetStatus = 'supported' }
        }
        catch { $workingSetStatus = 'unavailable' }
        try {
            if ($observedProcess.HandleCount -gt 0) { $handles = $observedProcess.HandleCount; $handleStatus = 'supported' }
        }
        catch { $handleStatus = 'unavailable' }
        # StartTime opens the original process handle. Recheck before publishing a point.
        if ($observedProcess.HasExited -or $observedProcess.StartTime.ToUniversalTime().Ticks -ne $Observation.CreationTimeUtcTicks) {
            $Observation.StopReason = 'process_exited_or_identity_changed'; return
        }
        $now = [DateTime]::UtcNow
        $point = @{ type = 'sample'; pid = $Observation.ProcessId; creation_time_utc_ticks = $Observation.CreationTimeUtcTicks; index = $Observation.Samples; utc = $now.ToString('o'); elapsed_ms = $Observation.Clock.Elapsed.TotalMilliseconds; working_set_bytes = $workingSet; working_set_status = $workingSetStatus; process_handle_count = $handles; process_handle_status = $handleStatus }
    }
    catch [System.ComponentModel.Win32Exception] { $Observation.StopReason = 'identity_unavailable'; return }
    catch [System.InvalidOperationException] { $Observation.StopReason = 'process_exited_or_identity_changed'; return }
    finally { if ($null -ne $observedProcess) { $observedProcess.Dispose() } }
    if ($null -ne $point) {
        # Stream failures are controller failures, never unsupported-counter evidence.
        $Observation.Writer.WriteLine(($point | ConvertTo-Json -Compress))
        if ($Observation.Samples -eq 0) { $Observation.FirstSampleUtc = $now }
        $Observation.LastSampleUtc = $now
        $Observation.Samples++
        $Observation.LastWorkingSet = $workingSet; $Observation.LastHandles = $handles
        if ($null -ne $workingSet) {
            $Observation.SupportedWorkingSet++
            if ($null -eq $Observation.PeakWorkingSet -or $workingSet -gt $Observation.PeakWorkingSet) { $Observation.PeakWorkingSet = $workingSet }
        }
        if ($null -ne $handles) {
            $Observation.SupportedHandles++
            if ($null -eq $Observation.PeakHandles -or $handles -gt $Observation.PeakHandles) { $Observation.PeakHandles = $handles }
        }
    }
}

function Close-OwnedProcessObservation {
    param([Parameter(Mandatory)]$Observation, [Parameter(Mandatory)][ValidateSet('completed', 'failed', 'timeout', 'controller_cleanup')][string]$Reason)
    if ($Observation.Closed) { return }
    try {
        $Observation.Clock.Stop()
        $first = if ($null -ne $Observation.FirstSampleUtc) { $Observation.FirstSampleUtc.ToString('o') } else { $null }
        $last = if ($null -ne $Observation.LastSampleUtc) { $Observation.LastSampleUtc.ToString('o') } else { $null }
        $Observation.Writer.WriteLine((@{
            type = 'summary'; schema = 'unity-mcp-owned-process-v1'; reason = $Reason; stop_reason = $Observation.StopReason
            sample_count = $Observation.Samples; first_sample_utc = $first; last_sample_utc = $last
            closed_utc = [DateTime]::UtcNow.ToString('o'); observation_duration_ms = $Observation.Clock.Elapsed.TotalMilliseconds
            working_set_supported_samples = $Observation.SupportedWorkingSet; handle_supported_samples = $Observation.SupportedHandles
            peak_working_set_bytes = $Observation.PeakWorkingSet; peak_process_handle_count = $Observation.PeakHandles
            coverage = $(if ($Observation.Samples -gt 0) { 'sampled_window' } else { 'empty' })
        } | ConvertTo-Json -Compress))
    }
    finally { $Observation.Closed = $true; $Observation.Writer.Dispose(); $Observation.Writer = $null }
}
