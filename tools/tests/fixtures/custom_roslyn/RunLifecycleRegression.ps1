[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [string]$SourcePath,
    [string]$UnityDefines = 'UNITY_2022_3_OR_NEWER',
    [switch]$WarningsAsErrors,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
if (!$SourcePath) { $SourcePath = Join-Path $PSScriptRoot '../../../../CustomTools/RoslynRuntimeCompilation/RoslynRuntimeCompiler.cs' }
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$roslyn = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.8-api'
$compiler = Join-Path $SdkPath 'Roslyn/bincore/csc.dll'
New-Item -ItemType Directory -Force -Path $WorkPath | Out-Null
$references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
$references += Join-Path $framework 'Facades/netstandard.dll'
$dependencies = @('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll', 'System.Collections.Immutable.dll',
    'System.Reflection.Metadata.dll', 'System.Memory.dll', 'System.Numerics.Vectors.dll', 'System.Runtime.CompilerServices.Unsafe.dll', 'System.Threading.Tasks.Extensions.dll')
$configuration = [xml]'<configuration><runtime><assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1" /></runtime></configuration>'
$binding = $configuration.configuration.runtime.FirstChild
foreach ($name in $dependencies) {
    $path = Join-Path $roslyn $name
    if ($name -eq 'System.Numerics.Vectors.dll') { $path = Join-Path $SdkPath 'Roslyn/binfx/System.Numerics.Vectors.dll' }
    $references += $path
    Copy-Item -LiteralPath $path -Destination $WorkPath
    $identity = [Reflection.AssemblyName]::GetAssemblyName($path)
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
$executable = Join-Path $WorkPath 'LifecycleRegression.exe'
$configuration.Save($executable + '.config')
$arguments = @($compiler, '/nologo', '/noconfig', '/nostdlib+', '/target:exe', ('/define:UNITY_EDITOR,' + $UnityDefines), ('/out:' + $executable))
if ($WarningsAsErrors) { $arguments += '/warnaserror+' }
$arguments += $references | ForEach-Object { '/reference:' + $_ }
$shim = (Resolve-Path (Join-Path $PSScriptRoot '../../../../MCPForUnity/Runtime/Helpers/UnityFindObjectsCompat.cs')).Path
$arguments += @($source, $shim, (Join-Path $PSScriptRoot 'LifecycleRegressionHarness.cs'))
Write-Output ('SOURCE_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
Write-Output ('HARNESS_SHA256: ' + (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'LifecycleRegressionHarness.cs') -Algorithm SHA256).Hash)
Write-Output ('SHIM_SHA256: ' + (Get-FileHash -LiteralPath $shim -Algorithm SHA256).Hash)
Write-Output ('UNITY_DEFINES: ' + $UnityDefines)
& $DotnetPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Harness compilation failed' }
& $executable $WorkPath
exit $LASTEXITCODE
