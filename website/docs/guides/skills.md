---
title: Install and Update Client Skills
sidebar_label: Client Skills
description: Install the Unity instructions pinned by your package, preview updates, and preserve local edits.
---

# Install and Update Client Skills

The Unity MCP package includes instructions for clients that support skills, including Codex and Claude. The default installer selects the immutable skill revision declared by the installed package. Package version, Python server source and skill revision are independent.

## Install from the main window

Open **Window > Unity MCP (ykh09242) > Toggle MCP Window**. Under **Connect > Client Configuration > Per-client setup**, select the client and choose **Install Skills**. The button appears for clients that support this installation workflow.

This uses the installed package's skill revision. Existing branch preferences do not switch the default to a moving branch. The result reports the source and resolved commit, with any retained local changes or conflicts.

## Preview an update

Open **Window > Unity MCP (ykh09242) > Skill Installer**.

1. Keep **Skill source** set to **Installed package** for the package's declared revision.
2. Select **CLI** and verify **Install Dir**.
3. Choose **Preview package skills** and inspect the changes in **Output**.
4. Choose **Apply safe changes** to apply the prepared update.

The preview identifies the package name and version, a labelled package-identity fingerprint, repository/subdirectory, requested revision and resolved commit. If the installed package or its pin changes after preview, apply refuses the stale selection; create another preview.

## Choose the latest branch explicitly

Select **Latest from branch** in the same installer when you want the selected repository branch instead of the installed package's revision. Check **Repo URL** and **Branch**, then choose **Preview latest (beta/main)** for the selected branch and review its resolved commit before applying.

Each preview resolves the branch to an immutable commit. If the branch moves before apply, preview again. Returning to **Installed package** can select older content; existing local changes still take part in the same conflict checks.

## Local edits and failures

Synchronization compares the last recorded source, current local files and incoming files. It applies safe changes, preserves local edits and reports conflicts. Selecting a package revision means that revision supplied the incoming content; retained local changes can make the installed files differ from it.

A missing or invalid package pin is an error and does not silently select a branch. Install a package with a valid pin, or explicitly select **Latest from branch**. Download, commit/tree validation or recovery failures are reported separately. If a result says recovery is required, retain the reported evidence and resolve it before applying another update.

Maintainers declare the independent full commit in `MCPForUnity/package.json` as `mcpSkillRevision`. After verifying the intended content, `python tools/check_skill_pin.py --verify-git` checks the locally available commit and both distributed skill copies. This read-only check does not fetch history or change the pin.
