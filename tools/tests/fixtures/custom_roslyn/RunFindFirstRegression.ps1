[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.8-api'
$compiler = Join-Path $SdkPath 'Roslyn/bincore/csc.dll'
$shim = (Resolve-Path (Join-Path $PSScriptRoot '../../../../MCPForUnity/Runtime/Helpers/UnityFindObjectsCompat.cs')).Path
$harness = Join-Path $PSScriptRoot 'FindFirstRegressionHarness.cs'
New-Item -ItemType Directory -Force -Path $WorkPath | Out-Null
Write-Output ('SHIM_SHA256: ' + (Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash)
Write-Output ('HARNESS_SHA256: ' + (Get-FileHash -LiteralPath $harness -Algorithm SHA256).Hash)
$branches = @(
    @{ Name = 'legacy'; Defines = 'UNITY_2021_3_OR_NEWER' },
    @{ Name = 'modern'; Defines = 'UNITY_2022_3_OR_NEWER' },
    @{ Name = 'deprecated'; Defines = 'UNITY_2022_3_OR_NEWER,UNITY_6000_5_OR_NEWER' },
    @{ Name = 'missing-legacy'; Defines = 'UNITY_2021_3_OR_NEWER,MISSING_ORDERED_API' },
    @{ Name = 'missing-deprecated'; Defines = 'UNITY_2022_3_OR_NEWER,UNITY_6000_5_OR_NEWER,MISSING_ORDERED_API' }
)
foreach ($branch in $branches) {
    $executable = Join-Path $WorkPath ($branch.Name + '.exe')
    $arguments = @($compiler, '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/warnaserror+',
        ('/define:' + $branch.Defines), ('/out:' + $executable))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += @($shim, $harness)
    Write-Output ('BRANCH: ' + $branch.Name + '; DEFINES: ' + $branch.Defines)
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw ('Compilation failed: ' + $branch.Name) }
    & $executable
    if ($LASTEXITCODE -ne 0) { throw ('Regression failed: ' + $branch.Name) }
}
exit 0
