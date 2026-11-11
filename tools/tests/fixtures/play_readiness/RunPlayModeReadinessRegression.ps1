[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourcePath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$SourcePath) { $SourcePath = Join-Path $root 'MCPForUnity/Editor/Services/PlayModeReadiness.cs' }
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ('play-readiness-regression-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    $json = Join-Path $root '.compile-refs/Newtonsoft.Json.dll'
    Copy-Item -LiteralPath $json -Destination $work
    $references += $json
    $executable = Join-Path $work 'PlayModeReadinessRegression.exe'
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest', ('/out:' + $executable))
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    $arguments += @($source, (Join-Path $PSScriptRoot 'PlayModeReadinessRegression.cs'), (Join-Path $root 'MCPForUnity/Editor/Helpers/Response.cs'))
    Write-Output ('SOURCE: ' + $source)
    Write-Output ('SOURCE_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Readiness harness compilation failed' }
    Write-Output 'MANAGED_SEAMS: Actual readiness service; simulated Editor/scene/compilation signals; no Editor launched'
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') $executable
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('play-readiness-regression-')) {
        throw 'Refusing cleanup outside the owned temporary test directory'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
