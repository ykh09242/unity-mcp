[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [Parameter(Mandatory = $true)][string]$Label,
    [string]$HostSourcePath,
    [int]$Iterations = 100000,
    [int]$Samples = 5,
    [string]$EvidencePath,
    [switch]$CompileOnly,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$HostSourcePath) { $HostSourcePath = Join-Path $root 'MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeHost.cs' }
$inputs = @((Resolve-Path -LiteralPath $HostSourcePath).Path,
    (Join-Path $root 'MCPForUnity/Editor/Models/Command.cs'),
    (Join-Path $root 'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs'),
    (Join-Path $PSScriptRoot 'UnityBoundaryStubs.cs'),
    (Join-Path $PSScriptRoot 'IdleQueueBenchmarkHarness.cs'),
    (Join-Path $PSScriptRoot 'RunIdleQueueBenchmark.ps1'))
$hashes = @($inputs | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } })
$work = Join-Path ([IO.Path]::GetTempPath()) ('phase12-stdio-idle-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$result = 0
try {
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $executable = Join-Path $work 'IdleQueueBenchmark.exe'
    $json = Join-Path $root '.compile-refs/Newtonsoft.Json.dll'
    Copy-Item -LiteralPath $json -Destination $work
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+',
        '/target:exe', '/langversion:latest', '/optimize+', ('/out:' + $executable))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += '/reference:' + (Join-Path $framework 'Facades/netstandard.dll')
    $arguments += '/reference:' + $json
    $arguments += $inputs[0..4]
    $hashes | ForEach-Object { Write-Output ($_.path + ' SHA256=' + $_.sha256) }
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Idle queue benchmark compilation failed' }
    if ($CompileOnly) { Write-Output 'COMPILE_ONLY: no benchmark executed'; return }
    $output = @(& (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') $executable $Label $Iterations $Samples 2>&1 | ForEach-Object { $_.ToString() })
    $result = $LASTEXITCODE
    $output | ForEach-Object { Write-Output $_ }
    $fresh = $true
    foreach ($inputHash in $hashes) {
        if ((Get-FileHash -LiteralPath $inputHash.path -Algorithm SHA256).Hash -ne $inputHash.sha256) { $fresh = $false }
    }
    if (!$fresh) { $result = 1; throw 'Benchmark input source changed during capture' }
    if ($EvidencePath) {
        $evidence = [IO.Path]::GetFullPath($EvidencePath)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($evidence)) | Out-Null
        @{ label = $Label; iterations = $Iterations; samples = $Samples; sources = $hashes;
            output = $output; exit_code = $result; source_freshness_confirmed = $fresh;
            row_columns = @('label', 'row', 'sample', 'iterations', 'allocated_bytes', 'elapsed_ms');
            boundary = 'Complete host steady empty queue; identical Unity stubs; direct/reflection rows and matching controls; no publisher/Editor/network' } |
            ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $evidence -Encoding utf8
    }
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('phase12-stdio-idle-')) { throw 'Refusing cleanup outside owned benchmark temp' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
