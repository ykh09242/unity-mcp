[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [string]$SdkPath,
    [string]$DotnetPath = 'dotnet'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
if (!$SdkPath) {
    $installed = & $DotnetPath --list-sdks | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or $installed -notmatch '^(\S+) \[(.+)\]$') {
        throw 'The fixture smoke checks require an installed .NET SDK.'
    }
    $SdkPath = Join-Path $Matches[2] $Matches[1]
}

$work = (New-Item -ItemType Directory -Force -Path $WorkPath).FullName
$shell = (Get-Process -Id $PID).Path
$common = @('-UnityData', $UnityData, '-SdkPath', $SdkPath, '-DotnetPath', $DotnetPath)

function Invoke-Fixture([string]$Name, [string]$Script, [string[]]$Arguments, [string[]]$Expected, [int]$ExitCode = 0) {
    $log = Join-Path $work "$Name.log"
    & $shell -NoProfile -File (Join-Path $PSScriptRoot $Script) @common @Arguments *> $log
    $actualExit = $LASTEXITCODE
    $output = Get-Content -LiteralPath $log -Raw
    if ($actualExit -ne $ExitCode) {
        Write-Output $output
        throw "$Name exited $actualExit, expected $ExitCode. Log at $log"
    }
    foreach ($pattern in $Expected) {
        if ($output -notmatch $pattern) {
            Write-Output $output
            throw "$Name did not report '$pattern'. Log at $log"
        }
    }
    Write-Output "PASS: $Name"
}

$build = 'build_preflight/RunBuildPreflight.ps1'
$stdio = 'phase12_stdio/RunStdioRegression.ps1'
$idle = 'phase12_stdio/RunIdleQueueBenchmark.ps1'
$websocket = 'phase12_websocket/RunPhase12WebSocket.ps1'

Invoke-Fixture build $build @('-WorkPath', (Join-Path $work 'build')) @('BUILD_PREFLIGHT: [1-9][0-9]* checks, 0 failures')
Invoke-Fixture legacy-build $build @('-WorkPath', (Join-Path $work 'legacy-build'), '-UnityDefines', 'UNITY_2022_3_OR_NEWER') `
    @('BUILD_PREFLIGHT: [1-9][0-9]* checks, 0 failures')
Invoke-Fixture build-guard $build @('-WorkPath', (Join-Path $work 'build-guard'), '-DisablePlanGuardControl') @('FAIL: malformed guarded hash') 1
Invoke-Fixture stdio $stdio @('-EvidencePath', (Join-Path $work 'stdio.json')) @('focused stdio checks passed', 'PASS: response completion retains barrier')
# Check each executable output lane with one sample. No timing threshold is applied.
Invoke-Fixture idle $idle @('-Label', 'smoke', '-Iterations', '10', '-Samples', '1', '-EvidencePath', (Join-Path $work 'idle.json')) @(
    'smoke,delegate_control,0,10,', 'smoke,stdio_empty_delegate,0,10,', 'smoke,reflection_control,0,10,', 'smoke,stdio_empty_reflection,0,10,'
)
Invoke-Fixture websocket $websocket @('-Evidence', (Join-Path $work 'websocket'), '-Case', 'all') @('PASS_TOTAL: 9 FAIL_TOTAL: 0')
Invoke-Fixture websocket-architecture $websocket @('-Evidence', (Join-Path $work 'websocket-architecture'), '-Case', 'architecture') `
    @('PASS: existing_architecture_real_socket', 'PASS_TOTAL: 1 FAIL_TOTAL: 0')
Invoke-Fixture transport-architecture 'transport_architecture/RunTransportArchitecture.ps1' @() `
    @('PASS: Following mutation waits for canceled handler cleanup', 'PASS: Structured projection matches exact legacy bytes')
Invoke-Fixture prefab 'prefab_targets/RunPrefabTargetRegression.ps1' @() @('RESULT: 17/17 passed')
