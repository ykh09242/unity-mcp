[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$work = Join-Path ([IO.Path]::GetTempPath()) ('property-scalars-regression-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    foreach ($path in @((Join-Path $root '.compile-refs/Newtonsoft.Json.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.CoreModule.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.SharedInternalsModule.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.ParticleSystemModule.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.ImageConversionModule.dll'))) {
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
    }
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest',
        ('/out:' + (Join-Path $work 'PropertyScalarRegression.exe')))
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    foreach ($relative in @('Editor/Helpers/ParamCoercion.cs', 'Editor/Helpers/StringCaseUtility.cs', 'Editor/Helpers/PropertyConversion.cs',
        'Editor/Helpers/UnityJsonSerializer.cs', 'Editor/Helpers/VectorParsing.cs', 'Editor/Helpers/TextureOps.cs', 'Editor/Helpers/RendererHelpers.cs',
        'Runtime/Serialization/JsonScalarConversion.cs', 'Runtime/Serialization/UnityTypeConverters.cs', 'Runtime/Helpers/UnityObjectIdCompat.cs')) {
        $path = Join-Path $root ('MCPForUnity/' + $relative)
        $arguments += $path
        Write-Output ($relative + '_SHA256: ' + (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash)
    }
    $arguments += @((Join-Path $PSScriptRoot 'PropertyScalarHarness.cs'), (Join-Path $PSScriptRoot 'PropertyScalarStubs.cs'))
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Property scalar harness compilation failed' }
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') (Join-Path $work 'PropertyScalarRegression.exe')
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('property-scalars-regression-')) {
        throw 'Refusing cleanup outside the owned temporary test directory'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
