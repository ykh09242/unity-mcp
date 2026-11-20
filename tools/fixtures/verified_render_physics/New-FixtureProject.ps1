param(
    [Parameter(Mandatory)][ValidateSet('urp', 'physics')][string]$Kind,
    [Parameter(Mandatory)][string]$Output,
    [string]$PackageRevision = 'HEAD',
    [string]$PackageCache
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$outputRoot = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputRoot) {
    throw "Use a new fixture output directory: $outputRoot"
}

$commit = & git -C $repo rev-parse --verify --end-of-options "$PackageRevision^{commit}"
if ($LASTEXITCODE -ne 0) {
    throw 'Could not resolve the package revision.'
}
$commit = $commit.Trim()

$deps = [ordered]@{
    'com.ykh09242.unity-mcp' = 'file:../../MCPForUnity'
    'com.unity.editorcoroutines' = '1.1.0'
    'com.unity.ext.nunit' = '2.0.5'
    'com.unity.nuget.newtonsoft-json' = '3.2.2'
    'com.unity.test-framework' = '1.6.0'
    'com.unity.ugui' = '2.0.0'
}
if ($PackageCache) {
    $cacheRoot = (Resolve-Path -LiteralPath $PackageCache).Path
    foreach ($name in @($deps.Keys) | Select-Object -Skip 1) {
        $package = Join-Path $cacheRoot $name
        $metadata = Get-Content -LiteralPath (Join-Path $package 'package.json') -Raw | ConvertFrom-Json
        if ($metadata.name -ne $name -or $metadata.version -ne $deps[$name]) {
            throw "Package cache has an unexpected name or version for $name."
        }
        $deps[$name] = 'file:' + $package.Replace('\', '/')
    }
}
foreach ($module in @(
    'ai', 'animation', 'assetbundle', 'audio', 'director', 'hierarchycore', 'imageconversion',
    'imgui', 'jsonserialize', 'particlesystem', 'physics', 'physics2d', 'screencapture', 'subsystems',
    'terrain', 'terrainphysics', 'tilemap', 'ui', 'uielements', 'unitywebrequest', 'unitywebrequestassetbundle',
    'unitywebrequestaudio', 'unitywebrequesttexture', 'video', 'xr'
)) {
    $deps["com.unity.modules.$module"] = '1.0.0'
}
if ($Kind -eq 'urp') {
    $deps['com.unity.render-pipelines.universal'] = '17.0.4'
}

New-Item -ItemType Directory -Path $outputRoot | Out-Null
$archive = Join-Path $outputRoot 'mcp-package.tar'
& git -C $repo archive --format=tar "--output=$archive" $commit MCPForUnity
if ($LASTEXITCODE -ne 0) {
    throw 'Could not archive the tracked MCP package.'
}
& tar.exe -xf $archive -C $outputRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Could not extract the tracked MCP package.'
}

$project = Join-Path $outputRoot 'project'
foreach ($dir in @('Assets/Editor', 'ProjectSettings', 'Packages')) {
    New-Item -ItemType Directory -Path (Join-Path $project $dir) | Out-Null
}
$manifest = Join-Path $project 'Packages/manifest.json'
@{ dependencies = $deps } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifest -Encoding utf8NoBOM
'm_EditorVersion: 6000.0.69f1' | Set-Content -LiteralPath (Join-Path $project 'ProjectSettings/ProjectVersion.txt') -Encoding utf8NoBOM
New-Item -ItemType File -Path (Join-Path $project "ProjectSettings/VerifiedRenderPhysics-$Kind") | Out-Null

$files = [ordered]@{ 'FixtureRun.cs' = 'Assets/Editor/FixtureRun.cs' }
if ($Kind -eq 'urp') {
    $files['UrpCameraFixture.cs'] = 'Assets/Editor/UrpCameraFixture.cs'
} else {
    $files['PhysicsTriggerSetup.cs'] = 'Assets/Editor/PhysicsTriggerSetup.cs'
    $files['PhysicsTriggerProbe.cs'] = 'Assets/PhysicsTriggerProbe.cs'
}
$hashes = [ordered]@{}
foreach ($name in $files.Keys) {
    $destination = Join-Path $project $files[$name]
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $destination
    $hashes[$files[$name]] = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
}
[ordered]@{
    kind = $Kind
    package_revision = $commit
    manifest_sha256 = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
    fixture_sha256 = $hashes
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outputRoot 'fixture-inputs.json') -Encoding utf8NoBOM
$project
