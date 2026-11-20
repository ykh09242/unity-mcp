# Compare shader stripping in two Windows Players

The controlled scene draws one quad with the `MCP_REQUIRED_TINT` material keyword enabled. The Editor renders the reference image before building. The stripped Player removes the matching fragment variants from the shader compilation callback. The retained Player builds the same scene and material with those variants present. Each build starts with a clean Player build cache. [Unity shader build callback](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Build.IPreprocessShaders.OnProcessShader.html)

In a licensed Windows CI job with Windows Standalone Mono, the locked server Python environment, and a Direct3D 11 device, prepare a fresh project with the repository's package preparation command. Then run the pair from the repository root.

```powershell
uv run --project Server --locked python tools/run_shader_variant_pair.py --editor $editor --project .unity-ci/shader-project/project --output .unity-ci/shader-observations
```

Open `comparison.json` and the three `frame.png` files. The camera renders to a 64 by 64 texture with a blue background. The central 8 by 8 region should be green in the Editor, red in the stripped Player and green in the retained Player. Each Player also reports its material, keyword, camera, graphics device, build GUID and measured bytes. The driver checks the bytes against the saved PNG and links the build GUID to the Editor's native build report.

Each phase retains shader preprocessing counts in `build.json`, file digests in `payload.json`, process records, and the observed Player image. The comparison checks that the scene, material, shader, probes and corresponding metadata stayed identical. A missing variant callback, missing GPU readback, changed device or unsuccessful process fails the comparison.
