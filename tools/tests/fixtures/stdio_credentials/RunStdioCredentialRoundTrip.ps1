[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$PythonPath,
    [string]$EvidencePath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$PythonPath) { $PythonPath = Join-Path $root 'Server/.venv/Scripts/python.exe' }
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$reader = Join-Path $root 'Server/src/transport/legacy/stdio_credentials.py'
$sources = @(
    'MCPForUnity/Editor/Security/SecureKeyStore/ISecureKeyStore.cs',
    'MCPForUnity/Editor/Security/SecureKeyStore/SecureKeyStoreConstants.cs',
    'MCPForUnity/Editor/Security/SecureKeyStore/WindowsCredentialKeyStore.cs',
    'MCPForUnity/Editor/Services/Transport/Transports/StdioLaunchCredential.cs'
) | ForEach-Object { Join-Path $root $_ }
$sources += Join-Path $PSScriptRoot 'StdioCredentialRoundTripHarness.cs'
$trackedInputs = @($sources) + @($reader, (Join-Path $PSScriptRoot 'read_owned_credential.py'))
$before = @($trackedInputs | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } })
$work = Join-Path ([IO.Path]::GetTempPath()) ('stdio-credential-roundtrip-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $executable = Join-Path $work 'StdioCredentialRoundTrip.exe'
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+',
        '/target:exe', '/langversion:latest', '/define:UNITY_EDITOR_WIN', ('/out:' + $executable))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += $sources
    & $DotnetPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Production Windows credential fixture compilation failed' }
    $fixtureArguments = @($executable, $python, (Join-Path $PSScriptRoot 'read_owned_credential.py'), $reader)
    $output = @(& (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') @fixtureArguments)
    $result = $LASTEXITCODE
    $output | ForEach-Object { Write-Output $_ }
    $fresh = $true
    foreach ($entry in $before) {
        if ((Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash -ne $entry.sha256) { $fresh = $false }
        Write-Output ($entry.path + ' SHA256=' + $entry.sha256)
    }
    if (!$fresh) { $result = 1; Write-Output 'FAIL: input source changed during roundtrip' }
    if ($EvidencePath) {
        $evidence = [IO.Path]::GetFullPath($EvidencePath)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($evidence)) | Out-Null
        @{ sources = $before; source_freshness_confirmed = $fresh; exit_code = $result; output = $output;
            namespace = 'MCPForUnity.Stdio'; production_python_api = 'read_stdio_token';
            credential_count = 1; expected_token_channel = 'owned redirected child stdin';
            token_logged_or_written_to_plain_file = $false;
            boundary = 'Actual Windows Credential Manager APIs; complete production C# writer and Python reader; synthetic exact owned entry only' } |
            ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $evidence -Encoding utf8
    }
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('stdio-credential-roundtrip-')) {
        throw 'Refusing cleanup outside owned temporary fixture'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
