[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$HttpSourcePath,
    [string]$StdioSourcePath,
    [string]$EvidencePath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$HttpSourcePath) { $HttpSourcePath = Join-Path $root 'MCPForUnity/Editor/Services/HttpBridgeReloadHandler.cs' }
if (!$StdioSourcePath) { $StdioSourcePath = Join-Path $root 'MCPForUnity/Editor/Services/StdioBridgeReloadHandler.cs' }
$sources = @((Resolve-Path -LiteralPath $HttpSourcePath).Path, (Resolve-Path -LiteralPath $StdioSourcePath).Path,
    (Join-Path $PSScriptRoot 'ReloadBatchIsolationHarness.cs'))
$work = Join-Path ([IO.Path]::GetTempPath()) ('reload-batch-isolation-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$results = @()
$sourceHashes = @($sources | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } })
try {
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $executable = Join-Path $work 'ReloadBatchIsolation.exe'
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+',
        '/target:exe', '/langversion:latest', ('/out:' + $executable))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += $sources
    $sourceHashes | ForEach-Object { Write-Output ($_.path + ' SHA256=' + $_.sha256) }
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Reload batch isolation fixture compilation failed' }
    $scenarios = @('batch-unset', 'batch-empty', 'batch-whitespace', 'batch-optin', 'batch-zero',
        'interactive-unset', 'interactive-empty', 'interactive-whitespace', 'interactive-optin', 'interactive-migrated')
    foreach ($scenario in $scenarios) {
        # Static constructors run once per process. Each scenario must start a fresh process.
        $output = @(& (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') $executable $scenario)
        $scenarioExit = $LASTEXITCODE
        $output | ForEach-Object { Write-Output $_ }
        $results += @{ scenario = $scenario; exit_code = $scenarioExit; output = $output }
    }
    $failed = @($results | Where-Object { $_.exit_code -ne 0 }).Count
    Write-Output ('RESULT: ' + ($results.Count - $failed) + '/' + $results.Count + ' fresh-process scenarios passed')
    $result = if ($failed -eq 0) { 0 } else { 1 }
    if ($EvidencePath) {
        $evidence = [IO.Path]::GetFullPath($EvidencePath)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($evidence)) | Out-Null
        @{ sources = $sourceHashes; scenarios = $results; passed = $results.Count - $failed; total = $results.Count;
            boundary = 'Complete production handler sources; hermetic UnityEditor/transport stubs; no real Editor or global preferences' } |
            ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $evidence -Encoding utf8
    }
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('reload-batch-isolation-')) {
        throw 'Refusing cleanup outside owned temporary fixture'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
