[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [string]$SourceRoot,
    [string]$PumpSource,
    [switch]$Baseline
)
$ErrorActionPreference = 'Stop'
if (!$SourceRoot) {
    $SourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
}
if (!$PumpSource) {
    $PumpSource = Join-Path $SourceRoot 'MCPForUnity/Runtime/Helpers/RecordingFramePump.cs'
}
New-Item -ItemType Directory -Force -Path $WorkPath | Out-Null
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.8-api'
$executable = Join-Path $WorkPath 'RecordingFramePumpRegression.exe'
$arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/langversion:latest', '/target:exe', ('/out:' + $executable))
$arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$arguments += @($PumpSource, (Join-Path $PSScriptRoot 'Harness.cs'))
foreach ($source in @($PumpSource, (Join-Path $PSScriptRoot 'Harness.cs'))) {
    Write-Output ((Split-Path -Leaf $source) + '_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
}
& dotnet @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'Recording frame pump harness compilation failed'
}
$runArguments = @($executable)
if ($Baseline) {
    $runArguments += '--baseline'
}
& (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') @runArguments
exit $LASTEXITCODE
