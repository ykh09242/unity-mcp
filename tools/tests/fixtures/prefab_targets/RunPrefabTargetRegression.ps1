[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourcePath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$SourcePath) { $SourcePath = Join-Path $root 'MCPForUnity/Editor/Tools/Prefabs/ManagePrefabs.cs' }
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$text = [IO.File]::ReadAllText($source)
# Execute actual production bodies with controlled scene/stage/asset boundaries.
function Get-MethodBody([string]$signature) {
    $start = $text.IndexOf($signature)
    if ($start -lt 0) { throw ('Production method not found: ' + $signature) }
    $open = $text.IndexOf('{', $start)
    $depth = 1
    $end = $open + 1
    while ($depth -gt 0 -and $end -lt $text.Length) {
        if ($text[$end] -eq '{') { $depth++ }
        if ($text[$end] -eq '}') { $depth-- }
        $end++
    }
    if ($depth -ne 0) { throw ('Production method body is incomplete: ' + $signature) }
    return $text.Substring($start, $end - $start)
}
$method = Get-MethodBody 'private static GameObject FindSceneObjectByName('
$create = Get-MethodBody 'private static object CreatePrefabFromGameObject('
$work = Join-Path ([IO.Path]::GetTempPath()) ('prefab-target-regression-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $generated = Join-Path $work 'ProductionResolver.cs'
    [IO.File]::WriteAllText($generated, ('using System; using System.Collections.Generic; using System.Linq; using Newtonsoft.Json.Linq; public static partial class ProductionResolver {' + $method + $create + '}'))
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest',
        ('/out:' + (Join-Path $work 'PrefabTargetRegression.exe')))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += '/reference:' + (Join-Path $framework 'Facades/netstandard.dll')
    $json = Join-Path $root '.compile-refs/Newtonsoft.Json.dll'
    $arguments += '/reference:' + $json
    Copy-Item -LiteralPath $json -Destination $work
    $arguments += @($generated, (Join-Path $PSScriptRoot 'PrefabTargetRegressionHarness.cs'))
    Write-Output ('SOURCE_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Prefab resolver compilation failed' }
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') (Join-Path $work 'PrefabTargetRegression.exe')
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('prefab-target-regression-')) {
        throw 'Refusing cleanup outside the owned temporary test directory'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
