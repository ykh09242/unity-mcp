param(
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repositoryRoot '.tmp/key-store-process-regression'
} elseif (-not [IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$failed = $false
foreach ($harness in @('LifecycleHarness', 'ProcessHarness')) {
    $artifactRoot = Join-Path $OutputRoot $harness
    $intermediate = (Join-Path $artifactRoot 'obj').Replace('\', '/') + '/'
    $binary = (Join-Path $artifactRoot 'bin').Replace('\', '/') + '/'
    $dotnetArgs = @(
        'run', '--project', (Join-Path $PSScriptRoot "$harness.csproj"),
        '--configuration', 'Release',
        "--property:BaseIntermediateOutputPath=$intermediate",
        "--property:OutputPath=$binary"
    )
    if ($harness -eq 'ProcessHarness') {
        $dotnetArgs += @('--', (Join-Path $artifactRoot 'probes'))
    }
    & dotnet @dotnetArgs
    if ($LASTEXITCODE -ne 0) {
        $failed = $true
    }
}
if ($failed) {
    exit 1
}
exit 0
