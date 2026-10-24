[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$work = Join-Path ([IO.Path]::GetTempPath()) ('object-id-cache-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $output = Join-Path $work 'CacheHarness.exe'
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+',
        '/target:exe', '/define:UNITY_EDITOR', '/langversion:latest', '/optimize+', ('/out:' + $output))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += @((Join-Path $root 'MCPForUnity/Runtime/Helpers/UnityObjectIdCompat.cs'),
        (Join-Path $PSScriptRoot 'CacheHarness.cs'), (Join-Path $PSScriptRoot 'UnityBoundaryStubs.cs'))
    & dotnet @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Object cache harness compilation failed' }
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') $output
    if ($LASTEXITCODE -ne 0) { throw 'Object cache harness failed' }
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('object-id-cache-')) {
        throw 'Refusing cleanup outside owned cache harness temp'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
