# Unity tool compatibility

Unity MCP checks the installed API used by each operation. A package version in a capability response identifies the observed package. For example, listing Build Profiles can work even if the Editor lacks the overload for building a Player with a profile. A requested profile build must then stop before dispatch.

| Tool or integration | What determines support |
| --- | --- |
| Sprite Atlas | Read `capabilities.formats.V1` for `.spriteatlas` and `capabilities.formats.V2` for `.spriteatlasv2`. The native SpriteAtlas, SpriteAtlasAsset and SpriteAtlasImporter APIs determine the available settings. Preview requires a matching packing mode. V1 edits require a mode that preserves the file format. |
| Input System | The installed asset import/export methods, the identities that survive a native JSON round trip, and the available event, device and PlayerInput APIs determine the supported operations. Project input handling and the consumer still determine whether simulated input reaches the game. |
| Cinemachine | The camera, brain and pipeline APIs on the selected components determine the available controls. Existing virtual cameras from upgraded scenes retain their own component layout. |
| ProBuilder | The selected provider supplies a legacy `pb_Object` or a `ProBuilderMesh`. Topology reads, mesh edits and repairs check the native operations used for the selected mesh. See [ProBuilder compatibility](probuilder-compatibility.md). |
| Addressables | Settings, label storage, edit methods and Undo cache behavior are checked independently. Saved build reports also have to match the installed reader's serialized fields and reference graph. See [Addressables compatibility](addressables-compatibility.md). |
| Memory Profiler | The reader signature, buffer access and on-disk column layout must agree. Older captures without session metadata can supply native object counts and sizes. Object identity comparisons require compatible session evidence. |
| Netcode for GameObjects | Preflight reads inline prefab registrations, prefab list assets and stored registrations awaiting migration. It evaluates the installed provider's override semantics without starting a network session. |
| Localization | A string key identifies a row in a name-only table. Shared-key tables use their stored numeric IDs. Smart String support and the number of storable locale fallbacks depend on the installed API. |
| Unity Test Framework | Targeted cancellation and deadlines require a run GUID, a cancellation method, a final event and observable scheduler state. An ordinary run has a smaller set of prerequisites. |
| Build Profiles | Listing, reading, activation and building check their own bindings. Building with an inherited scene list reads the global list, including when another profile is active. |
| Prefabs and physics | Removed GameObject overrides report separate list, apply and revert capabilities. 3D contact observation binds the installed event and its fields. Optional collider and Rigidbody properties are read individually. |
| Screen Capture, frame timing and rendering | A screenshot identifies the source that supplied its pixels. Frame timing returns the fields exposed by the Editor. Skybox reflection reads the available texture property. |
| UI Toolkit, uGUI and Project Auditor | PanelRenderer checks its component members and reload methods. uGUI checks its event and canvas APIs. Project Auditor checks its analysis and reporting contracts. |
| Optional engine modules | Unity's package registration enables the assembly definition symbol for a present module, including prereleases. See [optional modules](../optional-unity-modules.md). |

Atlas preview reports the sprites that Unity actually bound, including sprites resolved from an existing texture or folder membership. On Editors with the newer importer API, the V1 packer can terminate the Editor on a newly created atlas or newly imported source. When this path requires reopening, the tool returns the reason before starting the native pack. Reopen the project, then read the atlas and its master for current references and revisions before retrying. The older Editor with the SpriteAtlasAsset settings API also passed a same-session V1 preview of newly authored assets.

The UPM manifest declares the package installation floor. Some engine APIs changed their C# types or names, such as collider IDs. Compile-time aliases select the signature that the Editor can compile so the same tool remains available. The Editor compile matrix checks these API surfaces, and native EditMode and Player tests exercise specific installed combinations. Binary format revisions, Play Scenario protocol versions and release source pins have independent meanings and retain their own validation.
