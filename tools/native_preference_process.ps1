# Native ownership/evidence rules shared by the controller and its contract tests.
function Test-CimIdentityAssociation([long]$CimTicks, [long]$ExactTicks) {
    return ($CimTicks - ($CimTicks % 10)) -eq ($ExactTicks - ($ExactTicks % 10))
}

function Resolve-ProcessSnapshotIdentity($Rows) {
    foreach ($row in $Rows) {
        $exact = $null
        $observedProcess = $null
        try {
            $observedProcess = [Diagnostics.Process]::GetProcessById([int]$row.ProcessId)
            $nativeStart = $observedProcess.StartTime
            if ($null -ne $nativeStart -and $null -ne $row.CreationDate -and -not $observedProcess.HasExited) {
                $ticks = $nativeStart.ToUniversalTime().Ticks
                if (Test-CimIdentityAssociation $row.CreationDate.ToUniversalTime().Ticks $ticks) { $exact = $ticks }
            }
        } catch [ArgumentException] { } catch [InvalidOperationException] { } catch [System.ComponentModel.Win32Exception] { }
        finally { if ($null -ne $observedProcess) { $observedProcess.Dispose() } }
        $row | Add-Member -NotePropertyName exact_started_ticks -NotePropertyValue $exact -Force
        $row
    }
}

function Canonical-JsonValue($Value) {
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [ValueType]) { return $Value }
    if ($Value -is [System.Collections.IList]) {
        $items = @($Value | ForEach-Object { Canonical-JsonValue $_ })
        return ,$items
    }
    $result = [ordered]@{}
    foreach ($property in $Value.PSObject.Properties | Sort-Object Name) { $result[$property.Name] = Canonical-JsonValue $property.Value }
    return $result
}

function Package-LockSignature($Data, [string]$DeclaredVersion, [bool]$After = $false) {
    $copy = $Data | ConvertTo-Json -Depth 30 | ConvertFrom-Json
    $product = $copy.dependencies.'com.ykh09242.unity-mcp'
    $embedded = $null -ne $product -and $product.source -eq 'embedded' -and $product.version -eq 'file:com.ykh09242.unity-mcp'
    $local = -not $After -and $null -ne $product -and $product.source -eq 'local' -and $DeclaredVersion.StartsWith('file:') -and $product.version -eq $DeclaredVersion
    if (-not $embedded -and -not $local) { throw 'Unexpected product package lock version/source shape' }
    $product.version = 'file:com.ykh09242.unity-mcp'
    $product.source = 'embedded'
    return (Canonical-JsonValue $copy) | ConvertTo-Json -Depth 30 -Compress
}
function Add-OwnedDescendants($Current, $BaselineIds, $Owned) {
    do {
        $added = $false
        foreach ($row in $Current) {
            $id = [int]$row.ProcessId
            $parentId = [int]$row.ParentProcessId
            if ($BaselineIds.Contains($id) -or $Owned.ContainsKey($id) -or $row.Name -match 'Licens') { continue }
            if (-not $Owned.ContainsKey($parentId) -or $Owned[$parentId].identity_source -ne 'process') { continue }
            $parent = @($Current | Where-Object { [int]$_.ProcessId -eq $parentId })
            if ($parent.Count -ne 1 -or $parent[0].exact_started_ticks -ne $Owned[$parentId].started_ticks -or $null -eq $row.exact_started_ticks) { continue }
            $started = $row.exact_started_ticks
            if ($started -lt $Owned[$parentId].started_ticks) { continue }
            $Owned[$id] = @{ pid = $id; started_ticks = $started; parent_pid = $parentId; root_pid = $Owned[$parentId].root_pid; name = $row.Name; identity_source = 'process' }
            $added = $true
        }
    } while ($added)
}

function Assert-OwnedProcessIdentity($Expected, $Actual, $BaselineIds) {
    if (
        $Expected.identity_source -ne 'process' -or $null -eq $Actual -or $BaselineIds.Contains([int]$Expected.pid) -or $Actual.Name -match 'Licens' -or
        [int]$Actual.ProcessId -ne $Expected.pid -or $Actual.exact_started_ticks -ne $Expected.started_ticks
    ) { throw 'Owned process identity not proven; termination refused' }
}

function New-ProtectedProcessIdentity($Row) {
    if ($null -eq $Row.CreationDate) { throw 'Protected process CIM creation metadata unavailable' }
    return @{
        pid = [int]$Row.ProcessId
        started_ticks = $Row.exact_started_ticks
        cim_creation_ticks = $Row.CreationDate.ToUniversalTime().Ticks
        identity_source = if ($null -eq $Row.exact_started_ticks) { 'cim_observation_only' } else { 'process' }
    }
}

function Test-ProtectedProcessPreserved($Identity, $Current) {
    $rows = @($Current | Where-Object { [int]$_.ProcessId -eq $Identity.pid })
    if ($rows.Count -ne 1 -or $null -eq $rows[0].CreationDate) { return $false }
    if ($rows[0].CreationDate.ToUniversalTime().Ticks -ne $Identity.cim_creation_ticks) { return $false }
    if ($Identity.identity_source -eq 'cim_observation_only') { return $true }
    return $Identity.identity_source -eq 'process' -and $rows[0].exact_started_ticks -eq $Identity.started_ticks
}

function Assert-OwnedStaleProjectLock([string]$Project, $ExitedOwner, $Current) {
    if ($null -eq $ExitedOwner -or $ExitedOwner.exited -ne $true -or $ExitedOwner.project -ne $Project) {
        throw 'Project lock has no verified exited owner'
    }
    foreach ($row in $Current) {
        if ($row.Name -eq 'Unity.exe' -and [string]::IsNullOrWhiteSpace($row.CommandLine)) { throw 'Editor command line unavailable; stale lock removal refused' }
        if ($row.Name -eq 'Unity.exe' -and $row.CommandLine -and $row.CommandLine.Replace('\','/').Contains($Project.Replace('\','/'), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Project still has a live Editor; stale lock removal refused'
        }
    }
}
