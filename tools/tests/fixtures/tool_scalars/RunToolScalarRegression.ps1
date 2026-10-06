[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourceRootPath,
    [switch]$SkipNUnit,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$SourceRootPath) { $SourceRootPath = Join-Path $root 'MCPForUnity/Editor/Helpers' }
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$work = Join-Path ([IO.Path]::GetTempPath()) ('tool-scalars-regression-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    foreach ($name in @('Newtonsoft.Json.dll', 'nunit.framework.dll')) {
        $path = Join-Path $root ('.compile-refs/' + $name)
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
    }
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest',
        ('/out:' + (Join-Path $work 'ToolScalarRegression.exe')))
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    foreach ($name in @('ParamCoercion.cs', 'ToolParams.cs')) {
        $path = (Resolve-Path -LiteralPath (Join-Path $SourceRootPath $name)).Path
        $arguments += $path
        Write-Output ($name + '_SHA256: ' + (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash)
    }
    $scalarCore = Join-Path $root 'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs'
    $arguments += $scalarCore
    Write-Output ('JsonScalarConversion.cs_SHA256: ' + (Get-FileHash -LiteralPath $scalarCore -Algorithm SHA256).Hash)
    $arguments += @((Join-Path $root 'MCPForUnity/Editor/Helpers/StringCaseUtility.cs'), (Join-Path $root 'MCPForUnity/Editor/Helpers/Response.cs'),
        (Join-Path $PSScriptRoot 'ToolScalarHarness.cs'))
    if (!$SkipNUnit) {
        $tests = Join-Path $root 'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Helpers'
        $arguments += @( (Join-Path $tests 'ToolParamsTests.cs'), (Join-Path $tests 'ToolParamsCultureTests.cs') )
        if (Test-Path -LiteralPath (Join-Path $tests 'ParamCoercionTests.cs')) { $arguments += Join-Path $tests 'ParamCoercionTests.cs' }
    }
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Scalar harness compilation failed' }
    $runArguments = @((Join-Path $work 'ToolScalarRegression.exe'))
    if ($SkipNUnit) { $runArguments += '--skip-nunit' }
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') @runArguments
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('tool-scalars-regression-')) {
        throw 'Refusing cleanup outside the owned temporary test directory'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
