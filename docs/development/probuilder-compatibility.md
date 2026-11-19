# ProBuilder mesh compatibility

`manage_probuilder` recognizes the native `ProBuilder.Core.pb_Object` component in the 3.x `ProBuilderCore.dll` and `UnityEngine.ProBuilder.ProBuilderMesh` in the 4.x–6.x `Unity.ProBuilder` assembly. Project readiness reports whether a native component API is present. Individual operations require a compatible package implementation on the running Editor. Package installation remains optional.

The 3.x tools read native vertices, face triangles, edge endpoints and shared index groups. The topology page resolves each face's material against the renderer's submeshes. The shape generators use the package's dimensions. Mesh operations bind their native signatures, including 3.x methods that return status separately from their output geometry. Operations that require an in-place combine target or insertion at an explicit point require the corresponding native overload. A detach method that always deletes the source faces requires `deleteSourceFaces: true`.

On Unity 2021.3.45f2, ProBuilder 3.0.0 can author geometry. The published 3.0.2, 3.0.3, 3.0.6, 3.0.8, 3.0.9 and 3.10.1 binaries reference the removed `UnityEngine.Experimental.Rendering.RenderPipelineAsset` type in their default material getter. The 3.0.0 and 3.0.1 binaries omit this reference. Write requests check the getter before creating an object or recording Undo. Full 4.0.2 and 4.5.2 packages on this Editor have upstream editor-script compilation errors in `Snapping` and `PrefabStage` respectively. A runtime-only check with 4.0.2 exercises native mesh operations independently of those editor scripts. Full-package 5.2.4 runs on Unity 2021.3.45f2 and 6.1.4 on Unity 6000.0.69f1. [Unity package registry](https://packages.unity.com/com.unity.probuilder)

`repair_mesh` binds these public `RemoveDegenerateTriangles` contracts before invoking the package.

| Package API | Native contract |
| --- | --- |
| 3.x `pb_TriangleOps` | `bool (pb_Object, out int[])` |
| Early 4.x `DeleteElements` | `int[] (ProBuilderMesh)` |
| `MeshValidation` including 6.1.4 | `bool (ProBuilderMesh, List<int>)` |

`degenerateTrianglesRemoved` counts the change in triangles. The 3.x `pb_Object.triangleCount` getter counts triangle indices, so its difference is divided by three. The modern getter counts triangles directly. Native return values and output collections can describe removed vertices. The installed-package regression tests exercise a collinear triangle whose removal deletes three unused vertices, and a clean mesh whose topology remains unchanged.

An unavailable or incompatible signature returns an error before recording Undo or invoking the package. A package invocation failure returns the underlying error instead of reporting successful repair. Inspect the mesh before retrying an invocation failure because the package may have partially changed it. A successful call reports zero triangles removed only after the supported package method has completed and the topology has been counted.

Unity's source defines the modern repair signature and triangle count. [MeshValidation.cs](https://github.com/Unity-Technologies/com.unity.probuilder/blob/abe59c1d568add17ab0817848edff7af604871db/Runtime/MeshOperations/MeshValidation.cs) and [ProBuilderMesh.cs](https://github.com/Unity-Technologies/com.unity.probuilder/blob/abe59c1d568add17ab0817848edff7af604871db/Runtime/Core/ProBuilderMesh.cs).

The optional-package EditMode profile also exercises the vertex-color shader fallback supported by the tool: ProBuilder Standard Vertex Color, ProBuilder Diffuse Vertex Color, then Sprites/Default. ProBuilder 6.1.4 does not ship the first shader, so requiring that shader alone would skip valid fallback coverage. The regression requires an available supported shader and checks repeated edits reuse the selected material.
