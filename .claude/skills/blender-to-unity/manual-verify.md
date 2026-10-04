# Optional Live Handoff Verification

Use this checklist only for a user-authorized live Blender/Unity handoff. Static skill validation does not execute these steps or prove fidelity.

- Confirm the intended Blender objects and Unity project/scene; no arbitrary selection or scene replacement.
- Confirm the hosts share the verified filesystem, or complete the specifically authorized transfer.
- Export a nonempty model file to an unlinked path inside the target Unity Assets folder without overwriting an unrelated file.
- Confirm the visible import tool/group and its current schema; no hidden-handler workaround.
- Confirm import success, returned Assets-relative path/GUID and required texture/animation subassets.
- Instantiate the returned asset in the requested scene and retain its ID.
- If a size was requested, compare measured world bounds before/after scaling, rather than assuming a fixed unit correction.
- For GLB, verify glTFast capability; missing support requires a format choice or a separate installation request.
- Check materials, normals, rig/morph data and clips relevant to this particular model. Playback verification is separate from import success.
- Inspect a relevant screenshot and console; report unavailable capture/runtime checks honestly.
- No credentials or model-file bytes should be placed in the import MCP payload; it carries a target-host file path.
