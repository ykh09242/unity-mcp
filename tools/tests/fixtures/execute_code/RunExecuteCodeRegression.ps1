[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourcePath,
    [string]$CaseFilter = '',
    [switch]$CompileEditModeTests,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$SourcePath) { $SourcePath = Join-Path $root 'MCPForUnity/Editor/Tools/ExecuteCode.cs' }
$source = (Resolve-Path -LiteralPath $SourcePath).Path
$mono = Join-Path $UnityData 'MonoBleedingEdge/lib/mono'
$framework = Join-Path $mono '4.5'
$roslyn = Join-Path $mono '4.5'
$work = Join-Path ([IO.Path]::GetTempPath()) ('execute-code-regression-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    $references = @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'Microsoft.CSharp.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    $dependencies = @('Microsoft.CodeAnalysis.dll', 'Microsoft.CodeAnalysis.CSharp.dll', 'System.Collections.Immutable.dll',
        'System.Reflection.Metadata.dll', 'System.Memory.dll', 'System.Numerics.Vectors.dll', 'System.Runtime.CompilerServices.Unsafe.dll', 'System.Threading.Tasks.Extensions.dll')
    $configuration = [xml]'<configuration><runtime><assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1" /></runtime></configuration>'
    $binding = $configuration.configuration.runtime.FirstChild
    foreach ($name in $dependencies) {
        $path = Join-Path $roslyn $name
        if ($name -eq 'System.Numerics.Vectors.dll') { $path = Join-Path $SdkPath 'Roslyn/binfx/System.Numerics.Vectors.dll' }
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
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
    $configuration.Save((Join-Path $work 'ExecuteCodeRegression.exe.config'))
    foreach ($path in @((Join-Path $root '.compile-refs/Newtonsoft.Json.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.CoreModule.dll'),
        (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.SharedInternalsModule.dll'))) {
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
    }
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest',
        ('/out:' + (Join-Path $work 'ExecuteCodeRegression.exe')))
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    $arguments += @($source, (Join-Path $PSScriptRoot 'ExecuteCodeRegressionHarness.cs'),
        (Join-Path $root 'MCPForUnity/Editor/Helpers/Response.cs'),
        (Join-Path $root 'MCPForUnity/Editor/Helpers/UnityJsonSerializer.cs'),
        (Join-Path $root 'MCPForUnity/Runtime/Helpers/UnityAssembliesCompat.cs'),
        (Join-Path $root 'MCPForUnity/Runtime/Helpers/UnityObjectIdCompat.cs'),
        (Join-Path $root 'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs'),
        (Join-Path $root 'MCPForUnity/Runtime/Serialization/UnityTypeConverters.cs'))
    Write-Output ('SOURCE: ' + $source)
    Write-Output ('SOURCE_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Harness compilation failed' }
    if ($CompileEditModeTests) {
        $tests = Join-Path $root 'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ExecuteCodeTests.cs'
        $testArguments = @($arguments | Where-Object { $_ -notlike '/target:*' -and $_ -notlike '/out:*' })
        $testArguments += @('/target:library', '/define:EDITMODE_COMPILE', ('/out:' + (Join-Path $work 'ExecuteCodeEditModeCompile.dll')),
            ('/reference:' + (Join-Path $root '.compile-refs/nunit.framework.dll')), $tests)
        Write-Output ('EDITMODE_TESTS_SHA256: ' + (Get-FileHash -LiteralPath $tests -Algorithm SHA256).Hash)
        & $DotnetPath @testArguments
        if ($LASTEXITCODE -ne 0) { throw 'EditMode tests compile-only validation failed' }
        Write-Output 'COMPILE_ONLY: actual ExecuteCodeTests.cs passed; Editor tests were not executed'
    }
    # All CodeDom outputs remain within the runner-owned directory, including on failures.
    $env:TEMP = $work
    $env:TMP = $work
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') (Join-Path $work 'ExecuteCodeRegression.exe') $work $CaseFilter
    $result = $LASTEXITCODE
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('execute-code-regression-')) {
        throw 'Refusing cleanup outside the owned temporary test directory'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
