# Windows worktrees

`TestProjects/AssetStoreUploads` is a tracked Unity upload project, including an embedded
Unity Asset Store Tools package. It is not a generated upload cache. Do not ignore or
delete its tracked files to make a worktree removable.

## Long paths

Deep worktree roots can put the embedded package's source paths beyond the traditional
Windows path limit. Git may report `Filename too long` while inspecting or removing
these files. Set Git for Windows long-path support in this repository's local config:

```powershell
git config --local core.longpaths true
```

The common repository config is shared by its linked worktrees. This does not change
global Git config, the Windows registry, or other applications' path handling. A
per-command alternative, including when setting up a new checkout, is:

```powershell
git -c core.longpaths=true worktree add <path> <branch>
git -c core.longpaths=true -C <path> status --short
git -c core.longpaths=true worktree remove <path>
```

Managed app worktrees should be archived through the app's worktree lifecycle tools.
Preserve active work and needed ignored files before removal or archival. Check command
errors as well as exit codes: a path access error does not establish that a tree is clean.

## False vendor modifications

The Asset Store Tools C# and JSON files were originally committed with CRLF bytes.
The authored `text eol=lf` rules previously made unchanged historical blobs appear
modified after checkout. The narrow vendor exceptions in `.gitattributes` preserve
those bytes without hiding real edits or changing Unity metadata.

Old branches may still carry the earlier attribute rules. Before cleaning one up,
compare every reported vendor file's raw Git blob hash with its index and inspect
staged changes separately. Equal raw hashes establish that those particular changes
are conversion artifacts; whitespace-insensitive diffs alone do not establish that.
Preserve any actual changes. Do not use forced removal or bulk normalization to bypass
an unexplained dirty status.

The regression in `tools/tests/test_vendor_worktree_checkout.py` creates real deep
worktrees with both `core.autocrlf` settings, checks vendor byte identity and authored
LF output, verifies that a real edit prevents removal, and removes the clean fixture.
