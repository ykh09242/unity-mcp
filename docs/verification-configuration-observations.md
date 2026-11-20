# Compare a change in target compilation or texture import

Select the connected Editor with the active build target you want to investigate. Finish import and compilation, then capture the same selection before and after the edit. Both actions are read-only. Keep each JSON response as a separate file.

```sh
unity-mcp --no-project-context --instance Game@0123456789abcdef --format json verification observe_assembly --options '{"path":"Assets/Feature/Feature.asmdef","source":"Assets/Feature/Entry.cs"}' > before.json
unity-mcp verification-candidate configuration-diff before.json after.json
```

The assembly result includes membership of the selected source in the Editor and active-target Player compiler inputs, assembly-specific defines, asmdef platform rules, define constraints and resolved resource versions. Player inputs exclude test assemblies. The comparison identifies observed removal, records both compiler contexts and reports matching exclusions. Platform matching uses Unity's asmdef names for the active target. The reader can attribute a positive OR clause over observed version symbols and `UNITY_EDITOR`, and a single negated symbol. Other constraints can remain unresolved. Assembly input membership describes all types from an omitted source. Conditional compilation inside an included source and managed stripping need separate evidence. [Unity compiler API](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Compilation.CompilationPipeline.GetAssemblies.html)

```sh
unity-mcp --no-project-context --instance Game@0123456789abcdef --format json verification observe_texture --options '{"path":"Assets/Art/Surface.png.meta","platform":"Standalone","search_folder":"Assets/Level"}' > before.json
unity-mcp verification-candidate configuration-diff before.json after.json --baseline-run /captures/baseline/runs/<job> --candidate-run /captures/candidate/runs/<job>
```

Texture observations include the GUID, source and metadata digests, sRGB flag, importer defaults and named platform override. Use the target's importer tab name, such as `Standalone`, `Android` or `iPhone`. The search follows direct material-to-texture and saved scene-to-material dependencies in the specified folder. Source and metadata hashing accept files up to 32 MiB each. A search accepts up to 512 candidate materials and scenes and 256 resulting edges. Choose a smaller folder if Unity reports the limit. Prefab indirection, unsaved edits and dynamic material assignments require other observations. The comparison reports the saved reference chains from both captures and compares their material, scene and metadata source digests. The 32 MiB aggregate budget for these additional files can leave individual inputs unknown. In that case `only_meta_changed_in_selected_inputs` is null. A possible change in rendering is a prediction. [Unity texture importer API](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/TextureImporter.GetPlatformTextureSettings.html)

To compare actual rendered samples from a Standalone target, build each scenario Player with its texture `observation_revision` as `build_source_revision`, run it with payload verification, and supply both returned job directories. The project probe renders the selected scene's material to a render target, calls `ReadPixels` and logs one line starting with `MCP_CONFIGURATION_PIXEL ` followed by this JSON shape. Use the native job ID from the scenario request directory. The context fields identify the same sample in both jobs. The RGBA channels are measured bytes. [Unity render target readback](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Texture2D.ReadPixels.html)

```json
{"job_id":"0123456789abcdef0123456789abcdef","scene":"Assets/Level/Main.unity","material":"Assets/Level/Surface.mat","sample":"center-linear-rgba8","method":"render_texture_read_pixels","device":"Direct3D11","color_space":"Linear","rgba":[55,55,55,255]}
```

The reader checks the native report against its launch request, process result, observation digest and selected scene/material/texture chain. Without both runs, `player_observation.state` is `unavailable`. The comparison describes the selected pixel. A full screen comparison requires additional samples or image evidence.

The licensed Windows Player assurance long Mono profile exercises the controls in a dedicated disposable project. It compares a version-defined assembly and an Editor-only platform rule with their common baseline compiler inputs. For the rendering control it holds the fixture's source, material and scene files fixed, changes only the texture's sRGB metadata, builds two Players, and checks the independently read linear RGBA sample from each process. Look for `configuration-observations/comparison.json` and the corresponding native artifacts in the run. A skipped licensed job provides no native observation.
