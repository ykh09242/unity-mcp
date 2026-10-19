using System;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageMaterialRenderStateTests
    {
        private Material _material;
        private GameObject _target;
        private GameObject _peer;
        private Renderer _renderer;
        private Renderer _peerRenderer;

        [SetUp]
        public void SetUp()
        {
            var shader = Shader.Find("Hidden/MCPForUnityTests/MaterialRenderState");
            Assert.IsNotNull(shader, "Owned shader fixture must be imported.");
            _material = new Material(shader);
            _target = new GameObject("RenderStateTarget", typeof(MeshRenderer));
            _peer = new GameObject("RenderStatePeer", typeof(MeshRenderer));
            _renderer = _target.GetComponent<Renderer>();
            _peerRenderer = _peer.GetComponent<Renderer>();
            _renderer.sharedMaterials = new[] { _material, _material };
            _peerRenderer.sharedMaterial = _material;
            Undo.ClearAll();
        }

        [TearDown]
        public void TearDown()
        {
            var selected = _renderer != null ? _renderer.sharedMaterials[1] : null;
            UnityEngine.Object.DestroyImmediate(_target);
            UnityEngine.Object.DestroyImmediate(_peer);
            if (selected != null && selected != _material)
                UnityEngine.Object.DestroyImmediate(selected);
            UnityEngine.Object.DestroyImmediate(_material);
            Undo.ClearAll();
        }

        private JObject Request(string action = "get_render_state", string mode = null, JObject state = null)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["target"] = _target.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
                ["slot"] = 1,
            };
            if (mode != null)
                request["mode"] = mode;
            if (state != null)
                request["renderState"] = state;
            return request;
        }

        private static JObject Patch()
        {
            return new JObject
            {
                ["render_queue"] = 3001,
                ["keywords"] = new JObject { ["FIXTURE_LOCAL"] = true },
                ["shader_passes"] = new JObject { ["FixtureForward"] = false },
                ["texture_transforms"] = new JObject
                {
                    ["_MainTex"] = new JObject { ["scale"] = new JArray(0, 2), ["offset"] = new JArray(-1, 0.25) },
                },
            };
        }

        [Test]
        public void GetRenderState_ReadsSharedSlotWithoutInstantiationOrDirtying()
        {
            // Given two renderers and two slots sharing one material.
            bool materialDirty = EditorUtility.IsDirty(_material);
            bool rendererDirty = EditorUtility.IsDirty(_renderer);
            // When state is inspected through the public dispatcher.
            var result = ToJObject(ManageMaterial.HandleCommand(Request()));
            // Then identity, keyword declaration and LightMode evidence are observable without mutations.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(_material.GetInstanceIDCompat(), result["data"]["material"]["instance_id"].Value<int>());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
            Assert.AreEqual(materialDirty, EditorUtility.IsDirty(_material));
            Assert.AreEqual(rendererDirty, EditorUtility.IsDirty(_renderer));
            Assert.IsTrue(result["data"]["keywords"].ToString().Contains("FIXTURE_LOCAL"));
            Assert.AreEqual("FixtureForward", result["data"]["shader_passes"][0]["light_mode"].Value<string>());
            Assert.IsFalse(result["data"]["shader_passes"][1]["controllable"].Value<bool>());
            Assert.AreEqual("unsupported", result["data"]["render_mode_coupling"]["status"].Value<string>());
        }

        [Test]
        public void SetRenderState_InstanceChangesOnlySelectedSlotAndReadbackMatchesActualState()
        {
            // Given a fully validated queue/keyword/pass/UV patch.
            int queue = _material.renderQueue;
            // When one selected slot is edited in instance mode.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", Patch())));
            // Then only the selected slot has a clone with actual state readback.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var instance = _renderer.sharedMaterials[1];
            Assert.AreNotSame(_material, instance);
            Assert.AreSame(_material, _renderer.sharedMaterials[0]);
            Assert.AreSame(_material, _peerRenderer.sharedMaterial);
            Assert.AreEqual(queue, _material.renderQueue);
            Assert.IsFalse(_material.IsKeywordEnabled("FIXTURE_LOCAL"));
            Assert.IsTrue(_material.GetShaderPassEnabled("FixtureForward"));
            Assert.AreEqual(Vector2.one, _material.GetTextureScale("_MainTex"));
            Assert.AreEqual(3001, instance.renderQueue);
            Assert.IsTrue(instance.IsKeywordEnabled("FIXTURE_LOCAL"));
            Assert.IsFalse(instance.GetShaderPassEnabled("FixtureForward"));
            Assert.AreEqual(new Vector2(0, 2), instance.GetTextureScale("_MainTex"));
            Assert.AreEqual(new Vector2(-1, 0.25f), instance.GetTextureOffset("_MainTex"));
            Assert.AreEqual(instance.GetInstanceIDCompat(), result["data"]["material"]["instance_id"].Value<int>());
            Assert.AreEqual(instance.renderQueue, result["data"]["render_queue"].Value<int>());
            Assert.IsFalse(result["data"]["shader_passes"][0]["enabled"].Value<bool>());
            Assert.AreEqual(0, result["data"]["texture_transforms"]["_MainTex"]["scale"][0].Value<float>());
        }

        [Test]
        public void SetRenderState_InstanceSingleUndoRestoresReferencesAndDestroysClone()
        {
            // Given an instance edit with its own Undo group.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", Patch())));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var clone = _renderer.sharedMaterials[1];
            Undo.FlushUndoRecordObjects();
            // When one Undo is performed.
            Undo.PerformUndo();
            // Then both references and created-object lifetime are restored.
            Assert.AreSame(_material, _renderer.sharedMaterials[0]);
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
            Assert.AreSame(_material, _peerRenderer.sharedMaterial);
            Assert.IsTrue(clone == null, "Single Undo must destroy the created clone.");
        }

        [Test]
        public void SetRenderState_SharedModePropagatesAndUndoRestoresState()
        {
            // Given an explicit shared edit.
            int queue = _material.renderQueue;
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "shared", Patch())));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
            Assert.AreEqual(3001, _peerRenderer.sharedMaterial.renderQueue);
            Undo.FlushUndoRecordObjects();
            // When that shared edit is undone.
            Undo.PerformUndo();
            // Then all shared observers see the restored material.
            Assert.AreEqual(queue, _peerRenderer.sharedMaterial.renderQueue);
            Assert.IsFalse(_material.IsKeywordEnabled("FIXTURE_LOCAL"));
            Assert.IsTrue(_material.GetShaderPassEnabled("FixtureForward"));
            Assert.AreEqual(Vector2.one, _material.GetTextureScale("_MainTex"));
        }

        [TestCase("{\"render_queue\":true}")]
        [TestCase("{\"render_queue\":5001}")]
        [TestCase("{\"render_queue\":1.5}")]
        [TestCase("{\"render_queue\":null}")]
        [TestCase("{\"render_queue\":3001,\"keywords\":{\"UNDECLARED\":true}}")]
        [TestCase("{\"render_queue\":3001,\"keywords\":{\"FIXTURE_LOCAL\":1}}")]
        [TestCase("{\"render_queue\":3001,\"shader_passes\":{\"DISPLAY_NAME_IS_NOT_LIGHTMODE\":false}}")]
        [TestCase("{\"render_queue\":3001,\"texture_transforms\":{\"_Value\":{\"scale\":[1,2]}}}")]
        [TestCase("{\"render_queue\":3001,\"texture_transforms\":{\"_MainTex\":{\"scale\":[1,2,3]}}}")]
        [TestCase("{\"render_queue\":3001,\"render_mode\":\"transparent\"}")]
        public void SetRenderState_InvalidMixedPatchHasNoAssignmentCloneOrMutation(string json)
        {
            // Given a patch with an invalid member, even after a valid queue edit.
            int queue = _material.renderQueue;
            int materialCount = UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length;
            // When instance editing is requested.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", JObject.Parse(json))));
            // Then validation rejects the entire patch before creating or assigning anything.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
            Assert.AreSame(_material, _peerRenderer.sharedMaterial);
            Assert.AreEqual(queue, _material.renderQueue);
            Assert.AreEqual(materialCount, UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length);
        }

        [TestCase(null)]
        [TestCase("property_block")]
        [TestCase("create_unique")]
        public void SetRenderState_RequiresExplicitSupportedMode(string mode)
        {
            // Given an omitted or unsupported mode.
            var request = Request("set_render_state", mode, Patch());
            // When submitted to the dispatcher.
            var result = ToJObject(ManageMaterial.HandleCommand(request));
            // Then the shared material reference is preserved.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
        }

        [Test]
        public void SetRenderState_QueueResetAndCaseInsensitiveLightModeReadback()
        {
            // Given a queue override and a case-insensitive LightMode identifier.
            _material.renderQueue = 3001;
            var state = new JObject
            {
                ["render_queue"] = -1,
                ["shader_passes"] = new JObject { ["fixtureforward"] = false },
            };
            // When shader-default queue and disabled pass are requested.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "shared", state)));
            // Then readback reports the effective shader queue and canonical pass identity.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(_material.shader.renderQueue, result["data"]["render_queue"].Value<int>());
            Assert.IsFalse(_material.GetShaderPassEnabled("FixtureForward"));
        }

        [Test]
        public void SetRenderState_LocalKeywordDoesNotMutateGlobalState()
        {
            // Given an overridable declared keyword and its current global state.
            bool globalEnabled = Shader.IsKeywordEnabled("FIXTURE_OVERRIDABLE");
            int globalCount = Shader.globalKeywords.Length;
            var state = new JObject { ["keywords"] = new JObject { ["FIXTURE_OVERRIDABLE"] = false } };
            // When a local material state is edited.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "shared", state)));
            // Then the global override and declaration inventory are preserved and reported separately.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(globalEnabled, Shader.IsKeywordEnabled("FIXTURE_OVERRIDABLE"));
            Assert.AreEqual(globalCount, Shader.globalKeywords.Length);
            JObject observed = null;
            foreach (JObject keyword in (JArray)result["data"]["keywords"])
                if (keyword.Value<string>("name") == "FIXTURE_OVERRIDABLE")
                    observed = keyword;
            Assert.IsNotNull(observed);
            Assert.IsFalse(observed.Value<bool>("local_enabled"));
            Assert.IsTrue(observed.Value<bool>("is_overridable"));
            Assert.AreEqual(globalEnabled, observed.Value<bool>("global_enabled"));
            Assert.AreEqual(globalEnabled, observed.Value<bool>("effective_enabled"));
        }

        [Test]
        public void SetRenderState_RejectsOversizedMapsAndNonfiniteUvWithoutCloning()
        {
            // Given either an oversized collection or a nonfinite transform.
            var keywords = new JObject();
            for (int i = 0; i < 129; i++)
                keywords["KEYWORD_" + i] = false;
            var states = new[]
            {
                new JObject { ["keywords"] = keywords },
                new JObject { ["texture_transforms"] = new JObject { ["_MainTex"] = new JObject { ["scale"] = new JArray(float.PositiveInfinity, 1) } } },
                new JObject
                {
                    ["shader_passes"] = new JObject { ["FixtureForward"] = true, ["fixtureforward"] = false },
                },
            };
            // When each unsupported patch is submitted.
            foreach (var state in states)
            {
                var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", state)));
                // Then the original material and its UV state survive unchanged.
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.AreSame(_material, _renderer.sharedMaterials[1]);
                Assert.AreEqual(Vector2.one, _material.GetTextureScale("_MainTex"));
            }
        }

        [TestCase("-1")]
        [TestCase("0.5")]
        [TestCase("true")]
        [TestCase("\"1\"")]
        [TestCase("2")]
        public void GetRenderState_RejectsInvalidSlots(string json)
        {
            // Given a malformed or out-of-range selected slot.
            var request = Request();
            request["slot"] = JToken.Parse(json);
            // When a read is submitted.
            var result = ToJObject(ManageMaterial.HandleCommand(request));
            // Then invalid addressing cannot instantiate or change the material.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
        }

        [Test]
        public void GetRenderState_RejectsAmbiguousAddress()
        {
            // Given both asset and renderer addressing.
            var request = Request();
            request["materialPath"] = "Assets/Unused.mat";
            // When inspected.
            var result = ToJObject(ManageMaterial.HandleCommand(request));
            // Then the address is rejected instead of choosing one silently.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
        }

        [Test]
        public void SetRenderState_AssetAddressRequiresSharedAndReturnsPersistentIdentity()
        {
            // Given a uniquely owned material asset.
            string path = "Assets/RenderStateFixture_" + Guid.NewGuid().ToString("N") + ".mat";
            var asset = new Material(_material);
            AssetDatabase.CreateAsset(asset, path);
            try
            {
                var rejected = ToJObject(
                    ManageMaterial.HandleCommand(
                        new JObject
                        {
                            ["action"] = "set_render_state",
                            ["materialPath"] = path,
                            ["mode"] = "instance",
                            ["renderState"] = Patch(),
                        }
                    )
                );
                Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
                // When the asset is edited with explicit shared mode.
                var result = ToJObject(
                    ManageMaterial.HandleCommand(
                        new JObject
                        {
                            ["action"] = "set_render_state",
                            ["materialPath"] = path,
                            ["mode"] = "shared",
                            ["renderState"] = Patch(),
                        }
                    )
                );
                // Then readback identifies the actual persistent asset.
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual(path, result["data"]["material"]["asset_path"].Value<string>());
                Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), result["data"]["material"]["asset_guid"].Value<string>());
                Assert.IsTrue(result["data"]["material"]["persistent"].Value<bool>());
                Assert.AreEqual(3001, asset.renderQueue);
            }
            finally
            {
                Undo.ClearAll();
                AssetDatabase.DeleteAsset(path);
            }
        }

        [TestCase("opaque", 0, 1, 0, 1, "Opaque", 2000, false, false, false)]
        [TestCase("cutout", 1, 1, 0, 1, "TransparentCutout", 2450, true, false, false)]
        [TestCase("fade", 2, 5, 10, 0, "Transparent", 3000, false, true, false)]
        [TestCase("transparent", 3, 1, 10, 0, "Transparent", 3000, false, false, true)]
        public void SetRenderState_StandardPresetCouplesModeBlendKeywordsTagAndQueue(
            string mode,
            int numericMode,
            int sourceBlend,
            int destinationBlend,
            int zWrite,
            string renderType,
            int queue,
            bool alphaTest,
            bool alphaBlend,
            bool alphaPremultiply
        )
        {
            // Given Built-in Standard with two shared observers and a deliberately overridden queue.
            if (UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null)
                Assert.Ignore("Standard preset capability is restricted to the Built-in pipeline.");
            var shader = Shader.Find("Standard");
            Assert.IsNotNull(shader);
            _material.shader = shader;
            _material.renderQueue = 3100;
            // When an explicit preset is applied to only one slot.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", new JObject { ["render_mode"] = mode })));
            // Then mode-dependent fields agree and original shared observers remain unchanged.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var clone = _renderer.sharedMaterials[1];
            Assert.AreEqual(numericMode, clone.GetFloat("_Mode"));
            Assert.AreEqual(sourceBlend, clone.GetFloat("_SrcBlend"));
            Assert.AreEqual(destinationBlend, clone.GetFloat("_DstBlend"));
            Assert.AreEqual(zWrite, clone.GetFloat("_ZWrite"));
            Assert.AreEqual(renderType, clone.GetTag("RenderType", false, ""));
            Assert.AreEqual(queue, clone.renderQueue);
            Assert.AreEqual(alphaTest, clone.IsKeywordEnabled("_ALPHATEST_ON"));
            Assert.AreEqual(alphaBlend, clone.IsKeywordEnabled("_ALPHABLEND_ON"));
            Assert.AreEqual(alphaPremultiply, clone.IsKeywordEnabled("_ALPHAPREMULTIPLY_ON"));
            Assert.AreEqual(3100, _peerRenderer.sharedMaterial.renderQueue);
            Assert.AreSame(_material, _renderer.sharedMaterials[0]);
            var observed = result["data"]["render_mode_coupling"];
            Assert.AreEqual("supported", observed["status"].Value<string>());
            Assert.AreEqual(numericMode, observed["properties"]["_Mode"].Value<float>());
            Assert.AreEqual(sourceBlend, observed["properties"]["_SrcBlend"].Value<float>());
            Assert.AreEqual(destinationBlend, observed["properties"]["_DstBlend"].Value<float>());
            Assert.AreEqual(zWrite, observed["properties"]["_ZWrite"].Value<float>());
            Assert.AreEqual(renderType, observed["render_type"].Value<string>());
            Assert.AreEqual(alphaTest, observed["alpha_keywords"]["_ALPHATEST_ON"].Value<bool>());
            Assert.AreEqual(queue, result["data"]["render_queue"].Value<int>());
        }

        [Test]
        public void SetRenderState_StandardSharedPresetUndoRestoresCoupledState()
        {
            // Given a Standard material with manually authored mode state.
            if (UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null)
                Assert.Ignore("Standard preset capability is restricted to the Built-in pipeline.");
            _material.shader = Shader.Find("Standard");
            _material.SetFloat("_Mode", 2);
            _material.SetFloat("_SrcBlend", 5);
            _material.SetFloat("_DstBlend", 10);
            _material.SetFloat("_ZWrite", 0);
            _material.SetOverrideTag("RenderType", "Transparent");
            _material.EnableKeyword("_ALPHABLEND_ON");
            _material.renderQueue = 3123;
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "shared", new JObject { ["render_mode"] = "cutout" })));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Undo.FlushUndoRecordObjects();
            // When the preset is undone once.
            Undo.PerformUndo();
            // Then the original mode, tag, keyword and explicit queue are restored for both observers.
            Assert.AreEqual(2, _material.GetFloat("_Mode"));
            Assert.AreEqual(5, _material.GetFloat("_SrcBlend"));
            Assert.AreEqual(10, _material.GetFloat("_DstBlend"));
            Assert.AreEqual(0, _material.GetFloat("_ZWrite"));
            Assert.AreEqual("Transparent", _material.GetTag("RenderType", false, ""));
            Assert.IsTrue(_material.IsKeywordEnabled("_ALPHABLEND_ON"));
            Assert.IsFalse(_material.IsKeywordEnabled("_ALPHATEST_ON"));
            Assert.AreEqual(3123, _peerRenderer.sharedMaterial.renderQueue);
        }

        [TestCase("{\"render_mode\":\"opaque\",\"render_queue\":2000}")]
        [TestCase("{\"render_mode\":\"cutout\",\"keywords\":{\"_ALPHATEST_ON\":true}}")]
        [TestCase("{\"render_mode\":\"unsupported_mode\"}")]
        public void SetRenderState_StandardPresetConflictsRejectBeforeClone(string json)
        {
            // Given Standard and an ambiguous or unsupported preset patch.
            if (UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null)
                Assert.Ignore("Standard preset capability is restricted to the Built-in pipeline.");
            _material.shader = Shader.Find("Standard");
            int queue = _material.renderQueue;
            // When submitted as an instance edit.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", JObject.Parse(json))));
            // Then whole-patch validation leaves identity and state unchanged.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(_material, _renderer.sharedMaterials[1]);
            Assert.AreEqual(queue, _material.renderQueue);
        }

        [Test]
        public void SetRenderState_SpecularPresetAllowsIndependentKeywordAndUvEdits()
        {
            // Given the other explicitly supported Standard shader and independent state edits.
            if (UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null)
                Assert.Ignore("Standard preset capability is restricted to the Built-in pipeline.");
            var shader = Shader.Find("Standard (Specular setup)");
            Assert.IsNotNull(shader);
            _material.shader = shader;
            var patch = new JObject
            {
                ["render_mode"] = "transparent",
                ["keywords"] = new JObject { ["_NORMALMAP"] = false },
                ["texture_transforms"] = new JObject { ["_MainTex"] = new JObject { ["offset"] = new JArray(0, 0.5) } },
            };
            // When the opt-in preset is applied alongside unrelated keyword and UV edits.
            var result = ToJObject(ManageMaterial.HandleCommand(Request("set_render_state", "instance", patch)));
            // Then all edits are applied without broad ShaderGUI normalization.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var clone = _renderer.sharedMaterials[1];
            Assert.AreEqual(3, clone.GetFloat("_Mode"));
            Assert.IsTrue(clone.IsKeywordEnabled("_ALPHAPREMULTIPLY_ON"));
            Assert.IsFalse(clone.IsKeywordEnabled("_NORMALMAP"));
            Assert.AreEqual(new Vector2(0, 0.5f), clone.GetTextureOffset("_MainTex"));
            Assert.AreEqual(Vector2.zero, _peerRenderer.sharedMaterial.GetTextureOffset("_MainTex"));
        }
    }
}
