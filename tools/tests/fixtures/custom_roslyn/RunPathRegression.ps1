[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourcePath,
    [switch]$CompanionProbe,
    [string]$UnityDefines = 'UNITY_2022_3_OR_NEWER',
    [string]$WorkPath,
    [switch]$WarningsAsErrors,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
if (!$SourcePath) {
    $sourceName = if ($CompanionProbe) { 'RoslynRuntimeCompiler.cs' } else { 'ManageRuntimeCompilation.cs' }
    $SourcePath = Join-Path $PSScriptRoot ('../../../../CustomTools/RoslynRuntimeCompilation/' + $sourceName)
}
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$mono = Join-Path $UnityData 'MonoBleedingEdge/lib/mono'
$roslyn = Join-Path $mono '4.5'
$framework = Join-Path $mono '4.8-api'
$json = Join-Path $SdkPath 'Sdks/Microsoft.NET.Sdk/tools/net472/Newtonsoft.Json.dll'
$compiler = Join-Path $SdkPath 'Roslyn/bincore/csc.dll'
$work = if ($WorkPath) { [IO.Path]::GetFullPath($WorkPath) } else { Join-Path ([IO.Path]::GetTempPath()) ('custom-roslyn-path-' + [Guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
    $references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    $dependencies = @('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll', 'System.Collections.Immutable.dll',
        'System.Reflection.Metadata.dll', 'System.Memory.dll', 'System.Numerics.Vectors.dll', 'System.Runtime.CompilerServices.Unsafe.dll', 'System.Threading.Tasks.Extensions.dll')
    $dependencyPaths = @{}
    foreach ($name in $dependencies) {
        $path = Join-Path $roslyn $name
        if ($name -eq 'System.Numerics.Vectors.dll') { $path = Join-Path $SdkPath 'Roslyn/binfx/System.Numerics.Vectors.dll' }
        $dependencyPaths[$name] = $path
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
    }
    $references += $json
    Copy-Item -LiteralPath $json -Destination $work
    $configuration = [xml]'<configuration><runtime><assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1" /></runtime></configuration>'
    $binding = $configuration.configuration.runtime.FirstChild
    foreach ($name in $dependencies) {
        $identity = [Reflection.AssemblyName]::GetAssemblyName($dependencyPaths[$name])
        $dependent = $configuration.CreateElement('dependentAssembly', $binding.NamespaceURI)
        $assembly = $configuration.CreateElement('assemblyIdentity', $binding.NamespaceURI)
        $assembly.SetAttribute('name', $identity.Name)
        $assembly.SetAttribute('publicKeyToken', ([BitConverter]::ToString($identity.GetPublicKeyToken()).Replace('-', '').ToLowerInvariant()))
        $assembly.SetAttribute('culture', 'neutral')
        $redirect = $configuration.CreateElement('bindingRedirect', $binding.NamespaceURI)
        $redirect.SetAttribute('oldVersion', '0.0.0.0-65535.65535.65535.65535')
        $redirect.SetAttribute('newVersion', $identity.Version.ToString())
        $dependent.AppendChild($assembly) | Out-Null
        $dependent.AppendChild($redirect) | Out-Null
        $binding.AppendChild($dependent) | Out-Null
    }
    $configuration.Save((Join-Path $work 'PathRegression.exe.config'))
    $arguments = @($compiler, '/nologo', '/noconfig', '/nostdlib+', '/target:exe', ('/define:USE_ROSLYN,' + $UnityDefines),
        ('/out:' + (Join-Path $work 'PathRegression.exe')))
    if ($CompanionProbe) { $arguments += '/define:COMPANION_PROBE' }
    if ($WarningsAsErrors) { $arguments += '/warnaserror+' }
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    $shim = (Resolve-Path (Join-Path $PSScriptRoot '../../../../MCPForUnity/Runtime/Helpers/UnityFindObjectsCompat.cs')).Path
    $arguments += @($source, $shim, (Join-Path $PSScriptRoot 'PathRegressionHarness.cs'))
    Write-Output ('SOURCE_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
    Write-Output ('SHIM_SHA256: ' + (Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash)
    Write-Output ('UNITY_DEFINES: ' + $UnityDefines)
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Harness compilation failed' }
    & (Join-Path $work 'PathRegression.exe') $work
    $result = $LASTEXITCODE
} finally {
    if (!$WorkPath) {
        $resolved = [IO.Path]::GetFullPath($work)
        $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('custom-roslyn-path-')) {
            throw 'Refusing cleanup outside the owned temporary test directory'
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
exit $result
