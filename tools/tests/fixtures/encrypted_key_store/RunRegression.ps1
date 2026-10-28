param(
    [string]$OutputRoot,
    [string]$KeyStoreSourceRoot
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repositoryRoot '.tmp/encrypted-key-store-regression'
} elseif (-not [IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $repositoryRoot $OutputRoot
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$intermediate = (Join-Path $OutputRoot 'obj').Replace('\', '/') + '/'
$binary = (Join-Path $OutputRoot 'bin').Replace('\', '/') + '/'
$dotnetArgs = @(
    'run', '--project', (Join-Path $PSScriptRoot 'StoreHarness.csproj'),
    '--configuration', 'Release',
    "--property:BaseIntermediateOutputPath=$intermediate",
    "--property:OutputPath=$binary"
)
if ($KeyStoreSourceRoot) {
    $dotnetArgs += "--property:KeyStoreSourceRoot=$([IO.Path]::GetFullPath($KeyStoreSourceRoot))"
}
$dotnetArgs += @('--', (Join-Path $OutputRoot 'probes'))
& dotnet @dotnetArgs
exit $LASTEXITCODE
