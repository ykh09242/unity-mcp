[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$UnityData,
    [Parameter(Mandatory=$true)][string]$SdkPath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$work = Join-Path ([IO.Path]::GetTempPath()) ('transport-architecture-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $references = @('mscorlib.dll','System.dll','System.Core.dll','System.Net.Http.dll') | ForEach-Object { Join-Path $framework $_ }
    $references += Join-Path $framework 'Facades/netstandard.dll'
    foreach ($name in @('Newtonsoft.Json.dll','nunit.framework.dll')) {
        $path = Join-Path $root ('.compile-refs/' + $name)
        $references += $path
        Copy-Item -LiteralPath $path -Destination $work
    }
    $sources = @(
        'MCPForUnity/Editor/Services/Transport/ConnectionCommandWork.cs',
        'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs',
        'MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs',
        'MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs',
        'MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs',
        'MCPForUnity/Editor/Services/Transport/TransportState.cs',
        'MCPForUnity/Editor/Services/Transport/IMcpTransportClient.cs',
        'MCPForUnity/Editor/Services/IToolDiscoveryService.cs',
        'MCPForUnity/Editor/Models/Command.cs',
        'MCPForUnity/Editor/Tools/CommandRegistry.cs',
        'MCPForUnity/Editor/Tools/BatchExecute.cs',
        'MCPForUnity/Editor/Helpers/ParamCoercion.cs',
        'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs',
        'MCPForUnity/Editor/Helpers/StringCaseUtility.cs',
        'MCPForUnity/Editor/Helpers/Response.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/TransportArchitectureTests.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/CooperativeCancellationTests.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/CooperativeHandlerCancellationTests.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/BatchExecuteCancellationTests.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/TransportCommandDispatcherTests.cs',
        'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/WebSocketTransportClientTests.cs'
    ) | ForEach-Object { Join-Path $root $_ }
    $sources += Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | ForEach-Object { $_.FullName }
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo','/noconfig','/nostdlib+','/target:exe','/langversion:latest',('/out:'+(Join-Path $work 'TransportArchitecture.exe')))
    $arguments += $references | ForEach-Object { '/reference:' + $_ }
    $arguments += $sources
    foreach ($source in $sources) { Write-Output ((Split-Path -Leaf $source) + '_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash) }
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Transport architecture compilation failed' }
    & (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') (Join-Path $work 'TransportArchitecture.exe')
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('transport-architecture-')) { throw 'Refusing cleanup outside owned temporary fixture' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
