param(
    [Parameter(Mandatory = $true)][string]$EditorPath,
    [Parameter(Mandatory = $true)][string]$ProjectA,
    [Parameter(Mandatory = $true)][string]$ProjectB,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][string[]]$InputManifest,
    [int]$StartupTimeoutSeconds = 300,
    [switch]$ClientWindow,
    [switch]$ClientOnly
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This controller requires native Windows PowerShell 7' }
if ($StartupTimeoutSeconds -lt 30) { throw 'Startup timeout must permit native initialization' }
if ($ClientOnly -and -not $ClientWindow) { throw 'Client-only replay requires the bounded client window' }
$mode = if ($ClientOnly) { 'client-only' } elseif ($ClientWindow) { 'lease-and-client' } else { 'lease' }
. (Join-Path $PSScriptRoot 'native_preference_process.ps1')

function Checked-Path([string]$Path) {
    $result = [IO.Path]::GetFullPath($Path)
    $cursor = $result
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Linked native input/output refused'
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $result
}
function Write-Json([string]$Path, $Value) {
    $stage = $Path + '.stage'
    [IO.File]::WriteAllText($stage, ($Value | ConvertTo-Json -Depth 20))
    [IO.File]::Move($stage, $Path, $true)
}
function Verify-Inputs {
    foreach ($entry in @($script:inputs) + @($script:harnessInputs)) {
        $path = Checked-Path $entry.path
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $entry.sha256) { throw "Changed native input: $path" }
    }
}
function Verify-GeneratedLocks {
    $after = @()
    foreach ($entry in $script:generatedLocks) {
        $data = Get-Content -LiteralPath $entry.path -Raw | ConvertFrom-Json
        if ((Package-LockSignature $data $entry.declared_version $true) -ne $entry.signature) { throw 'Unexpected generated package lock change' }
        $after += @{ path = $entry.path; sha256 = (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    Write-Json (Join-Path $script:outputRoot 'generated-locks-after.json') $after
}
function Assert-Identity($Peer) {
    $Peer.process.Refresh()
    if ($Peer.process.HasExited -or $Peer.process.Id -ne $Peer.pid -or $Peer.process.StartTime.ToUniversalTime().Ticks -ne $Peer.started_ticks) {
        throw 'Owned Editor PID/start identity changed; termination refused'
    }
}
function Capture-Owned([bool]$Force = $false) {
    if (-not $Force -and [DateTime]::UtcNow -lt $script:nextSample) { return }
    $script:nextSample = [DateTime]::UtcNow.AddSeconds(1)
    $current = @(Resolve-ProcessSnapshotIdentity @(Get-CimInstance Win32_Process))
    Add-OwnedDescendants $current $script:baselineIds $script:ownedTree
}
function Cleanup-Descendants([int]$RootPid = 0) {
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        $current = @(Resolve-ProcessSnapshotIdentity @(Get-CimInstance Win32_Process))
        $live = @($current | Where-Object {
            $id = [int]$_.ProcessId
            $script:ownedTree.ContainsKey($id) -and $id -ne $script:ownedTree[$id].root_pid -and
            $_.exact_started_ticks -eq $script:ownedTree[$id].started_ticks -and
            ($RootPid -eq 0 -or $script:ownedTree[$id].root_pid -eq $RootPid)
        })
        if (-not $live.Count) { return }
        if ($attempt -ge 5) {
            foreach ($row in $live) {
                $expected = $script:ownedTree[[int]$row.ProcessId]
                $actual = @(Resolve-ProcessSnapshotIdentity @(Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$row.ProcessId))) | Select-Object -First 1
                Assert-OwnedProcessIdentity $expected $actual $script:baselineIds
                $process = [Diagnostics.Process]::GetProcessById($expected.pid)
                if ($process.StartTime.ToUniversalTime().Ticks -ne $expected.started_ticks) { throw 'Owned descendant identity changed' }
                $process.Kill()
                [void]$process.WaitForExit(1000)
                $script:descendantCleanup.Add($expected)
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'Owned descendants remained after cleanup'
}
function Wait-File([string]$Path, $Peer, [int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while (-not (Test-Path -LiteralPath $Path)) {
        try { Assert-Identity $Peer } catch {
            # A graceful peer publishes its final reply before exiting; accept that already-durable reply.
            if (Test-Path -LiteralPath $Path) { break }
            throw
        }
        Capture-Owned
        if ([DateTime]::UtcNow -ge $deadline) { throw "Native observation deadline: $Path" }
        Start-Sleep -Milliseconds 100
    }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}
function Start-Peer([string]$Name) {
    $directory = Join-Path $script:outputRoot $Name
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    foreach ($file in @('ready.json', 'command.json')) {
        $path = Join-Path $directory $file
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    $project = if ($Name -eq 'a') { $script:projectAPath } else { $script:projectBPath }
    $lock = Checked-Path (Join-Path $project 'Temp/UnityLockfile')
    if (Test-Path -LiteralPath $lock) {
        Assert-OwnedStaleProjectLock $project $script:exitedProjects[$project] @(Get-CimInstance Win32_Process -Filter "Name = 'Unity.exe'")
        Copy-Item -LiteralPath $lock -Destination (Join-Path $script:outputRoot ("stale-lock-$Name-$($script:launches.Count).bin"))
        Remove-Item -LiteralPath $lock
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $script:editor
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.WorkingDirectory = $project
    foreach ($argument in @('-batchmode', '-nographics', '-projectPath', $project, '-buildTarget', 'Win64', '-logFile', (Join-Path $directory ("editor-$($script:launches.Count).log")), '-forgetProjectPath', '-executeMethod', 'MCPForUnityTests.Editor.Services.NativePreferenceLeasePeer.Run')) {
        $info.ArgumentList.Add($argument)
    }
    $info.Environment['UNITY_MCP_NATIVE_LEASE_ROOT'] = $script:outputRoot
    $info.Environment['UNITY_MCP_NATIVE_LEASE_NONCE'] = $script:nonce
    $info.Environment['UNITY_MCP_NATIVE_LEASE_PEER'] = $Name
    $info.Environment.Remove('UNITY_MCP_ALLOW_BATCH') | Out-Null
    $temporary = Join-Path $directory 'tmp'
    [IO.Directory]::CreateDirectory($temporary) | Out-Null
    $info.Environment['TEMP'] = $temporary
    $info.Environment['TMP'] = $temporary
    $process = [Diagnostics.Process]::Start($info)
    $peer = @{ name = $Name; project = $project; process = $process; pid = $process.Id; started_ticks = $process.StartTime.ToUniversalTime().Ticks }
    $script:owned.Add($peer)
    $script:ownedTree[$peer.pid] = @{ pid = $peer.pid; started_ticks = $peer.started_ticks; root_pid = $peer.pid; name = 'Unity.exe'; identity_source = 'process' }
    $script:launches.Add(@{ peer = $Name; pid = $peer.pid; started_ticks = $peer.started_ticks; project = $project })
    $ready = Wait-File (Join-Path $directory 'ready.json') $peer $StartupTimeoutSeconds
    if ($ready.pid -ne $peer.pid -or $ready.started_ticks -ne $peer.started_ticks -or $ready.nonce -ne $script:nonce) { throw 'Native peer ready identity differs' }
    if ([IO.Path]::GetFullPath($ready.package_path) -ne (Join-Path $project 'Packages/com.ykh09242.unity-mcp')) { throw 'Native peer loaded an unexpected package source' }
    if ($script:editorVersion -and $ready.unity_version -ne $script:editorVersion) { throw 'Peer Editor versions differ' }
    $script:editorVersion = $ready.unity_version
    return $peer
}
function Command($Peer, [string]$Case, [string]$Operation, [int]$Idle = 0, [int]$Mode = 0) {
    Assert-Identity $Peer
    Capture-Owned
    $script:commandId++
    $directory = Join-Path $script:outputRoot $Peer.name
    Write-Json (Join-Path $directory 'command.json') @{ nonce = $script:nonce; id = $script:commandId; operation = $Operation; idle = $Idle; mode = $Mode }
    $reply = Wait-File (Join-Path $directory ("reply-$($script:commandId).json")) $Peer 60
    if ($reply.pid -ne $Peer.pid -or $reply.started_ticks -ne $Peer.started_ticks -or $reply.id -ne $script:commandId -or $reply.nonce -ne $script:nonce) { throw 'Native reply identity differs' }
    if ($reply.error) { throw $reply.error }
    $script:lastReply = $reply
    if ($Case) {
        $reply | Add-Member -NotePropertyName 'case' -NotePropertyValue $Case
        $script:steps.Add($reply)
        Write-Json (Join-Path $script:outputRoot 'observations.json') $script:steps.ToArray()
    }
}
function Stop-Owned($Peer, [string]$Mode) {
    Assert-Identity $Peer
    Capture-Owned $true
    if ($Mode -eq 'graceful') { Command $Peer '' 'quit' } else { $Peer.process.Kill() }
    if (-not $Peer.process.WaitForExit(60000)) { throw 'Owned Editor did not exit' }
    $script:terminations.Add(@{ pid = $Peer.pid; started_ticks = $Peer.started_ticks; identity_verified = $true; exited = $true; mode = $Mode })
    $script:exitedProjects[$Peer.project] = @{ project = $Peer.project; pid = $Peer.pid; started_ticks = $Peer.started_ticks; exited = $true }
    Cleanup-Descendants $Peer.pid
}

$editor = Checked-Path $EditorPath
$projectAPath = Checked-Path $ProjectA
$projectBPath = Checked-Path $ProjectB
$outputRoot = Checked-Path $Output
if ($projectAPath -eq $projectBPath -or (Test-Path -LiteralPath $outputRoot)) { throw 'Use two distinct projects and a fresh evidence directory' }
$inputs = @()
$harnessInputs = @($PSCommandPath, (Join-Path $PSScriptRoot 'native_preference_process.ps1')) | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() } }
$generatedLocks = @()
foreach ($manifest in $InputManifest) {
    $data = Get-Content -LiteralPath (Checked-Path $manifest) -Raw | ConvertFrom-Json
    if (-not $data.files -or @($data.files).Count -eq 0) { throw 'Nonempty frozen input files are required' }
    $inputs += @($data.files)
    foreach ($lockPath in @($data.generated_package_locks)) {
        if (-not $lockPath) { continue }
        $lockPath = Checked-Path $lockPath
        $lockData = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
        $projectManifest = Get-Content -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($lockPath)) 'manifest.json') -Raw | ConvertFrom-Json
        $declaredVersion = $projectManifest.dependencies.'com.ykh09242.unity-mcp'
        $generatedLocks += @{ path = $lockPath; declared_version = $declaredVersion; signature = (Package-LockSignature $lockData $declaredVersion); sha256 = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
}
foreach ($project in @($projectAPath, $projectBPath)) {
    foreach ($relative in @('Assets/Tests/EditMode/Services/NativePreferenceLeasePeer.cs', 'Packages/com.ykh09242.unity-mcp/Editor/Services/EditorPreferenceLease.cs')) {
        $required = Join-Path $project $relative
        if (-not ($inputs | Where-Object { [IO.Path]::GetFullPath($_.path) -eq $required })) { throw "Frozen manifest must include: $required" }
    }
}
foreach ($relative in @(
    'Assets/Tests/EditMode/Services/NativePreferenceLeasePeer.cs',
    'Packages/com.ykh09242.unity-mcp/Editor/Services/EditorPreferenceLease.cs'
)) {
    $hashA = (Get-FileHash -LiteralPath (Join-Path $projectAPath $relative) -Algorithm SHA256).Hash
    $hashB = (Get-FileHash -LiteralPath (Join-Path $projectBPath $relative) -Algorithm SHA256).Hash
    if ($hashA -ne $hashB) { throw 'Peer and preference lease source must match across both Editors' }
}
Verify-Inputs
$editorHash = (Get-FileHash -LiteralPath $editor -Algorithm SHA256).Hash.ToLowerInvariant()
$baseline = @(Resolve-ProcessSnapshotIdentity @(Get-CimInstance Win32_Process))
$baselineIds = [Collections.Generic.HashSet[int]]::new()
foreach ($row in $baseline) { [void]$baselineIds.Add([int]$row.ProcessId) }
foreach ($row in $baseline) {
    if ($row.Name -eq 'Unity.exe' -and $row.CommandLine) {
        $commandLine = $row.CommandLine.Replace('\', '/')
        foreach ($project in @($projectAPath, $projectBPath)) {
            if ($commandLine.Contains($project.Replace('\', '/'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Supplied project already has a running Editor' }
        }
    }
}
$protectedIds = [Collections.Generic.HashSet[int]]::new()
foreach ($row in $baseline | Where-Object { $_.Name -match 'Unity|Licens' }) { [void]$protectedIds.Add([int]$row.ProcessId) }
do {
    $countBefore = $protectedIds.Count
    foreach ($row in $baseline) { if ($protectedIds.Contains([int]$row.ParentProcessId)) { [void]$protectedIds.Add([int]$row.ProcessId) } }
} while ($protectedIds.Count -ne $countBefore)
$protected = @($baseline | Where-Object { $protectedIds.Contains([int]$_.ProcessId) } | ForEach-Object {
    New-ProtectedProcessIdentity $_
})
$nonce = [Guid]::NewGuid().ToString('N')
[IO.Directory]::CreateDirectory($outputRoot) | Out-Null
Write-Json (Join-Path $outputRoot 'owner.json') @{ schema = 'unity-mcp-native-preference-lease-v1'; nonce = $nonce; project_a = $projectAPath; project_b = $projectBPath }
Write-Json (Join-Path $outputRoot 'preferences.json') @{ ApplicationIdleTime = 4; InteractionMode = 0 }
Write-Json (Join-Path $outputRoot 'frozen-inputs.json') @{ files = $inputs; harness = $harnessInputs; editor = $editor; editor_sha256 = $editorHash }
Write-Json (Join-Path $outputRoot 'generated-locks-before.json') $generatedLocks
$owned = [Collections.Generic.List[object]]::new()
$launches = [Collections.Generic.List[object]]::new()
$terminations = [Collections.Generic.List[object]]::new()
$steps = [Collections.Generic.List[object]]::new()
$ownedTree = @{}
$exitedProjects = @{}
$descendantCleanup = [Collections.Generic.List[object]]::new()
$nextSample = [DateTime]::MinValue
$commandId = 0
$editorVersion = ''
$failure = $null
try {
    $a = Start-Peer 'a'
    $b = Start-Peer 'b'
    if (-not $ClientOnly) {
        Command $a 'graceful-a-acquire' 'acquire'
        Command $b 'graceful-b-acquire' 'acquire'
        Command $a 'graceful-a-release' 'release'
        Stop-Owned $a 'graceful'
        Command $b 'graceful-b-release' 'release'
        $a = Start-Peer 'a'
        Command $a 'forced-a-acquire' 'acquire'
        Command $b 'forced-b-acquire' 'acquire'
        Stop-Owned $a 'forced'
        Command $b 'forced-b-recover' 'recover'
        Command $b 'forced-b-release' 'release'
        Command $b 'restart-b-acquire' 'acquire'
        Stop-Owned $b 'forced'
        $a = Start-Peer 'a'
        Command $a 'restart-a-recover' 'recover'
        Command $a 'restart-a-acquire' 'acquire'
        Command $a 'restart-a-release' 'release'
        $b = Start-Peer 'b'
        Command $a 'manual-a-acquire' 'acquire'
        Command $b 'manual-b-acquire' 'acquire'
        Command $a 'manual-a-change' 'manual' 12 2
        Command $b 'manual-b-maintain' 'acquire'
        Command $a 'manual-a-release' 'release'
        Command $b 'manual-b-release' 'release'
        Command $a 'identity-a-reset' 'manual' 4 0
        Command $a 'identity-a-acquire' 'acquire'
        Command $a 'identity-a-recover' 'stale-start'
        Command $a 'admission-a-acquire' 'acquire'
        Command $a 'admission-a-release' 'release'
    }
    if ($ClientWindow) {
        Command $a '' 'status'
        $clientA = $lastReply
        Command $b '' 'status'
        Write-Json (Join-Path $outputRoot 'client-window.json') @{ nonce = $nonce; mode = $mode; peer_a = $clientA; peer_b = $lastReply; next_command_id = $commandId + 1; command_contract = 'one writer; nonce/id/operation; connect adds ws_endpoint and auth_token_file; poll status; disconnect both before completion' }
        $clientDeadline = [DateTime]::UtcNow.AddMinutes(8)
        $completed = Join-Path $outputRoot 'client-complete.json'
        while (-not (Test-Path -LiteralPath $completed)) {
            Assert-Identity $a
            Assert-Identity $b
            Capture-Owned
            if ([DateTime]::UtcNow -ge $clientDeadline) { throw 'Owned client probe window exceeded eight minutes' }
            Start-Sleep -Milliseconds 250
        }
        $clientResult = Get-Content -LiteralPath $completed -Raw | ConvertFrom-Json
        if ($clientResult.nonce -ne $nonce -or $clientResult.success -ne $true) { throw 'Owned client probe reported failure' }
        foreach ($name in @('a','b')) {
            $last = Get-Content -LiteralPath (Join-Path $outputRoot "$name/command.json") -Raw | ConvertFrom-Json
            $commandId = [Math]::Max($commandId, $last.id)
        }
    }
    Stop-Owned $a 'graceful'
    Stop-Owned $b 'graceful'
    Verify-Inputs
    Verify-GeneratedLocks
    if ((Get-FileHash -LiteralPath $editor -Algorithm SHA256).Hash -ine $editorHash) { throw 'Editor binary changed' }
} catch {
    $failure = $_.ToString()
} finally {
    foreach ($peer in $owned) {
        $peer.process.Refresh()
        if (-not $peer.process.HasExited) {
            try { Stop-Owned $peer 'forced' } catch { $failure = "$failure; cleanup: $_" }
        }
    }
    try { Cleanup-Descendants } catch { $failure = "$failure; descendants: $_" }
    $current = @(Resolve-ProcessSnapshotIdentity @(Get-CimInstance Win32_Process))
    $survivors = @($current | Where-Object { $ownedTree.ContainsKey([int]$_.ProcessId) -and $_.exact_started_ticks -eq $ownedTree[[int]$_.ProcessId].started_ticks }).Count
    $preserved = $true
    foreach ($identity in $protected) {
        if (-not (Test-ProtectedProcessPreserved $identity $current)) { $preserved = $false }
    }
    Write-Json (Join-Path $outputRoot 'owned-processes.json') @{
        observed = @($ownedTree.Values)
        descendant_cleanup = $descendantCleanup.ToArray()
        protected = $protected
    }
    $receipt = @{
        schema = if ($ClientOnly) { 'unity-mcp-native-client-window-v1' } else { 'unity-mcp-native-preference-lease-v1' }
        mode = $mode
        lease_observations_requested = -not $ClientOnly
        client_phase_succeeded = $ClientWindow -and $null -eq $failure
        native = $true
        platform = 'win32'
        editor_version = $editorVersion
        editor_sha256 = $editorHash
        inputs_verified_before_after = ($null -eq $failure)
        launches = $launches.ToArray()
        terminations = $terminations.ToArray()
        steps = $steps.ToArray()
        cleanup = @{ owned_survivors = $survivors; protected_preserved = $preserved }
        failure = $failure
    }
    Write-Json (Join-Path $outputRoot 'receipt.json') $receipt
}
if ($failure) { throw $failure }
Write-Output (Join-Path $outputRoot 'receipt.json')
