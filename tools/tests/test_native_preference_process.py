"""Exercise the actual PowerShell controller's process rules without starting Unity."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native controller contract")
@pytest.mark.parametrize(
    "check",
    [
        "descendant",
        "reused-parent",
        "baseline-child",
        "licensing-child",
        "wrong-start",
        "baseline-kill",
        "valid-kill",
        "unowned-lock",
        "live-project-lock",
        "owned-stale-lock",
        "cim-precision",
        "precision-child",
        "unknown-command-line",
        "lock-normalization",
        "lock-arbitrary-source",
        "lock-top-level-change",
        "unknown-creation-time",
        "unknown-process-start",
        "protected-unchanged",
        "protected-changed",
        "protected-unreadable",
        "cim-only-kill",
    ],
)
def test_controller_process_contract(check, tmp_path):
    helper = Path(__file__).parents[1] / "native_preference_process.ps1"
    program = r"""
param($Helper, $Check)
$ErrorActionPreference='Stop'
. $Helper
$start=[datetime]::new(2026,10,24,0,0,0,[DateTimeKind]::Utc)
$baseline=[Collections.Generic.HashSet[int]]::new()
$owned=@{ 100=@{pid=100; started_ticks=$start.Ticks; root_pid=100; name='Unity.exe'; identity_source='process'} }
$parent=[PSCustomObject]@{ ProcessId=100; ParentProcessId=1; CreationDate=$start; exact_started_ticks=$start.Ticks; Name='Unity.exe'; CommandLine='Unity -projectPath C:/owned/p' }
$child=[PSCustomObject]@{ ProcessId=200; ParentProcessId=100; CreationDate=$start.AddSeconds(1); exact_started_ticks=$start.AddSeconds(1).Ticks; Name='UnityShaderCompiler.exe'; CommandLine='' }
$before='{"dependencies":{"com.ykh09242.unity-mcp":{"version":"file:../../package","source":"local","depth":0,"dependencies":{"engine":"1.0.0"}}}}' | ConvertFrom-Json
$after='{"dependencies":{"com.ykh09242.unity-mcp":{"version":"file:com.ykh09242.unity-mcp","source":"embedded","depth":0,"dependencies":{"engine":"1.0.0"}}}}' | ConvertFrom-Json
switch($Check) {
 'descendant' { Add-OwnedDescendants @($parent,$child) $baseline $owned; if(-not $owned.ContainsKey(200)){throw 'child omitted'} }
 'reused-parent' { $parent.exact_started_ticks=$start.AddSeconds(2).Ticks; Add-OwnedDescendants @($parent,$child) $baseline $owned; if($owned.ContainsKey(200)){throw 'reused parent admitted'} }
 'baseline-child' { [void]$baseline.Add(200); Add-OwnedDescendants @($parent,$child) $baseline $owned; if($owned.ContainsKey(200)){throw 'baseline admitted'} }
 'licensing-child' { $child.Name='Unity.Licensing.Client.exe'; Add-OwnedDescendants @($parent,$child) $baseline $owned; if($owned.ContainsKey(200)){throw 'licensing admitted'} }
 'wrong-start' { $parent.exact_started_ticks=$start.AddTicks(1).Ticks; try {Assert-OwnedProcessIdentity $owned[100] $parent $baseline} catch {exit 0}; throw 'changed start accepted' }
 'baseline-kill' { [void]$baseline.Add(100); try {Assert-OwnedProcessIdentity $owned[100] $parent $baseline} catch {exit 0}; throw 'baseline kill accepted' }
 'valid-kill' { Assert-OwnedProcessIdentity $owned[100] $parent $baseline }
 'unowned-lock' { try {Assert-OwnedStaleProjectLock 'C:\owned\p' $null @()} catch {exit 0}; throw 'unowned lock accepted' }
 'live-project-lock' { $owner=@{project='C:\owned\p';exited=$true}; try {Assert-OwnedStaleProjectLock 'C:\owned\p' $owner @($parent)} catch {exit 0}; throw 'live lock accepted' }
 'owned-stale-lock' { Assert-OwnedStaleProjectLock 'C:\owned\p' @{project='C:\owned\p';exited=$true} @() }
 'cim-precision' { if(-not (Test-CimIdentityAssociation $start.Ticks ($start.Ticks+8))){throw 'microseconds refused'}; if(Test-CimIdentityAssociation $start.Ticks ($start.Ticks+10)){throw 'different microseconds admitted'} }
 'precision-child' { $owned[100].started_ticks+=8; $parent.exact_started_ticks+=8; $child.exact_started_ticks+=9; Add-OwnedDescendants @($parent,$child) $baseline $owned; if(-not $owned.ContainsKey(200) -or $owned[200].started_ticks -ne $child.exact_started_ticks){throw 'exact child identity lost'} }
 'unknown-command-line' { $parent.CommandLine=$null; try {Assert-OwnedStaleProjectLock 'C:\owned\p' @{project='C:\owned\p';exited=$true} @($parent)} catch {exit 0}; throw 'unknown Editor visibility accepted' }
 'lock-normalization' { if((Package-LockSignature $before 'file:../../package') -ne (Package-LockSignature $after 'file:../../package' $true)){throw 'expected normalization refused'} }
 'lock-arbitrary-source' { $after.dependencies.'com.ykh09242.unity-mcp'.version='unexpected-version'; try {Package-LockSignature $after 'file:../../package' $true} catch {exit 0}; throw 'arbitrary version accepted' }
 'lock-top-level-change' { $after | Add-Member -NotePropertyName unexpected_top_level -NotePropertyValue $true; if((Package-LockSignature $before 'file:../../package') -eq (Package-LockSignature $after 'file:../../package' $true)){throw 'top-level change omitted'} }
 'unknown-creation-time' { $row=[PSCustomObject]@{ProcessId=$PID;CreationDate=$null}; $result=@(Resolve-ProcessSnapshotIdentity @($row)); if($result.Count -ne 1 -or $null -ne $result[0].exact_started_ticks){throw 'unknown timestamp became verified'} }
 'unknown-process-start' { $row=[PSCustomObject]@{ProcessId=0;CreationDate=[datetime]::UtcNow}; $result=@(Resolve-ProcessSnapshotIdentity @($row)); if($result.Count -ne 1 -or $null -ne $result[0].exact_started_ticks){throw 'system metadata became verified'} }
 'protected-unchanged' { $parent.exact_started_ticks=$null; $identity=New-ProtectedProcessIdentity $parent; if($identity.identity_source -ne 'cim_observation_only' -or -not(Test-ProtectedProcessPreserved $identity @($parent))){throw 'unchanged protected observation failed'} }
 'protected-changed' { $parent.exact_started_ticks=$null; $identity=New-ProtectedProcessIdentity $parent; $parent.CreationDate=$start.AddSeconds(1); if(Test-ProtectedProcessPreserved $identity @($parent)){throw 'changed protected observation passed'} }
 'protected-unreadable' { $parent.exact_started_ticks=$null; $identity=New-ProtectedProcessIdentity $parent; $parent.CreationDate=$null; if(Test-ProtectedProcessPreserved $identity @($parent)){throw 'unreadable protected observation passed'} }
 'cim-only-kill' { $expected=@{pid=100;started_ticks=$start.Ticks;identity_source='cim_observation_only'}; try {Assert-OwnedProcessIdentity $expected $parent $baseline} catch {exit 0}; throw 'CIM-only termination accepted' }
 default {throw 'unknown check'}
}
"""
    script = tmp_path / "contract.ps1"
    script.write_text(program, encoding="utf-8")
    result = subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-File", str(script), str(helper), check],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
