using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class AddressablesActualBuildTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();

        [OneTimeSetUp]
        public void Prepare() => scenes.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => scenes.RestoreRunnerBootstrap();

        private static Type PackageType(string name) => Type.GetType(name + ", Unity.Addressables.Editor", true);

        private static object Get(object value, string name) => AddressablesAuthoringTests.Get(value, name);

        private static object Invoke(object value, string name, params object[] args) => AddressablesAuthoringTests.Invoke(value, name, args);

        private sealed class TemporaryStatic
        {
            private readonly Type type;
            private readonly string name;
            private readonly object original,
                assigned;

            internal TemporaryStatic(Type type, string name, object assigned)
            {
                this.type = type;
                this.name = name;
                this.assigned = assigned;
                original = Read();
                Write(assigned);
            }

            private object Read()
            {
                const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
                return type.GetProperty(name, flags)?.GetValue(null) ?? type.GetField(name, flags).GetValue(null);
            }

            private void Write(object value)
            {
                const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
                PropertyInfo property = type.GetProperty(name, flags);
                if (property != null)
                    property.SetValue(null, value);
                else
                    type.GetField(name, flags).SetValue(null, value);
            }

            internal bool Restore()
            {
                if (!Equals(Read(), assigned))
                    return false;
                Write(original);
                return Equals(Read(), original);
            }
        }

        private static JObject Analyze(string path, int page = 1, string revision = null)
        {
            Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor");
            Assert.IsNotNull(handler, "The dedicated Addressables handler is required before the explicit content build.");
            var args = new JObject
            {
                ["action"] = "analyze_layout",
                ["layout_path"] = path,
                ["page_number"] = page,
                ["page_size"] = 1,
            };
            if (revision != null)
                args["expected_revision"] = revision;
            return JObject.FromObject(handler.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { args }));
        }

        [Test, Explicit("Root alone authorizes this real local Addressables content build in the reused isolated project.")]
        public void ActualLocalBuildLayoutReportsDuplicatedSharedImplicitObjects()
        {
            string project = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath));
            string allowed = Environment.GetEnvironmentVariable("MCP_SPEC15_PROJECT_ROOT");
            string nonce = Environment.GetEnvironmentVariable("MCP_SPEC15_NONCE");
            if (
                string.IsNullOrEmpty(allowed)
                || !string.Equals(project.TrimEnd('/', '\\'), Path.GetFullPath(allowed).TrimEnd('/', '\\'), StringComparison.OrdinalIgnoreCase)
                || nonce == null
                || nonce.Length != 32
                || nonce.Any(c => !Uri.IsHexDigit(c))
            )
                Assert.Ignore("Root must bind MCP_SPEC15_PROJECT_ROOT and a unique32hex MCP_SPEC15_NONCE for the explicit local content build.");
            Assert.IsNotNull(
                Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor"),
                "Do not build before the product handler is present."
            );
            Type settingsType = PackageType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings");
            Assert.AreEqual("2.7.6", UnityEditor.PackageManager.PackageInfo.FindForAssembly(settingsType.Assembly).version);
            string root = "Assets/__McpAddressablesBuild_" + nonce;
            string library = "Library/MCPForUnity/Addressables/" + nonce + "/";
            string output = Path.GetFullPath(Path.Combine(project, library));
            StringAssert.StartsWith(Path.Combine(project, "Library", "MCPForUnity", "Addressables") + Path.DirectorySeparatorChar, output);
            Assert.IsFalse(AssetDatabase.IsValidFolder(root), "The nonce-owned asset root must be new.");
            Assert.IsFalse(Directory.Exists(output), "The nonce-owned build output must be new; existing output is preserved.");
            var overrides = new List<TemporaryStatic>();
            string rootGuid = null;
            try
            {
                var scene = scenes.Create("McpAddressablesBuild_", nonce);
                rootGuid = AssetDatabase.CreateFolder("Assets", root.Substring(7));
                AssetDatabase.CreateFolder(root, "Config");
                var temporary = (UnityEngine.Object)
                    settingsType
                        .GetMethod("Create", new[] { typeof(string), typeof(string), typeof(bool), typeof(bool) })
                        .Invoke(null, new object[] { root + "/Config", "Settings", false, false });
                string settingsPath = root + "/Config/Settings.asset";
                AssetDatabase.CreateAsset(temporary, settingsPath);
                AssetDatabase.SaveAssetIfDirty(temporary);
                UnityEngine.Resources.UnloadAsset(temporary);
                object settings = AssetDatabase.LoadAssetAtPath(settingsPath, settingsType);
                Assert.IsTrue((bool)Get(settings, "IsPersisted"));
                object profiles = Get(settings, "profileSettings");
                string profile = (string)Get(settings, "activeProfileId");
                string localOutput = output.Replace('\\', '/').TrimEnd('/') + "/";
                Invoke(profiles, "SetValue", profile, "Local.BuildPath", localOutput + "bundles/[BuildTarget]");
                Invoke(profiles, "SetValue", profile, "Local.LoadPath", new Uri(localOutput).AbsoluteUri + "bundles/[BuildTarget]");
                settingsType.GetProperty("ContentStateBuildPath").SetValue(settings, localOutput + "content-state");
                Get(settings, "buildSettings").GetType().GetProperty("bundleBuildPath").SetValue(Get(settings, "buildSettings"), library + "intermediate");
                Type bundled = PackageType("UnityEditor.AddressableAssets.Settings.GroupSchemas.BundledAssetGroupSchema");
                Type update = PackageType("UnityEditor.AddressableAssets.Settings.GroupSchemas.ContentUpdateGroupSchema");
                object first = Invoke(settings, "CreateGroup", "OwnedBuildA", true, false, false, null, new[] { bundled, update });
                object second = Invoke(settings, "CreateGroup", "OwnedBuildB", false, false, false, null, new[] { bundled, update });
                foreach (object group in new[] { first, second })
                {
                    object schema = Invoke(group, "GetSchema", bundled);
                    Assert.IsNotNull(schema);
                    Invoke(Get(schema, "BuildPath"), "SetVariableByName", settings, "Local.BuildPath");
                    Invoke(Get(schema, "LoadPath"), "SetVariableByName", settings, "Local.LoadPath");
                    bundled.GetProperty("UseDefaultSchemaSettings").SetValue(schema, false);
                    bundled.GetProperty("BundleMode").SetValue(schema, Enum.Parse(bundled.GetProperty("BundleMode").PropertyType, "PackTogether"));
                }
                var texture = new Texture2D(4, 4, TextureFormat.RGBA32, false);
                texture.SetPixels(Enumerable.Repeat(Color.magenta, 16).ToArray());
                texture.Apply();
                AssetDatabase.CreateAsset(texture, root + "/SharedTexture.asset");
                Shader shader = Shader.Find("Universal Render Pipeline/Unlit") ?? Shader.Find("Unlit/Texture");
                Assert.IsNotNull(shader, "A real installed unlit shader is required.");
                var material = new Material(shader);
                if (material.HasProperty("_BaseMap"))
                    material.SetTexture("_BaseMap", texture);
                if (material.HasProperty("_MainTex"))
                    material.SetTexture("_MainTex", texture);
                Assert.IsTrue(material.HasProperty("_BaseMap") || material.HasProperty("_MainTex"));
                AssetDatabase.CreateAsset(material, root + "/SharedMaterial.mat");
                string textureGuid = AssetDatabase.AssetPathToGUID(root + "/SharedTexture.asset");
                string materialGuid = AssetDatabase.AssetPathToGUID(root + "/SharedMaterial.mat");
                foreach (object group in new[] { first, second })
                {
                    var owner = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    try
                    {
                        owner.GetComponent<MeshRenderer>().sharedMaterial = material;
                        string path = root + "/" + Get(group, "Name") + ".prefab";
                        Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(owner, path));
                        object entry = Invoke(settings, "CreateOrMoveEntry", AssetDatabase.AssetPathToGUID(path), group, false, false);
                        Invoke(entry, "SetAddress", "owned/" + Get(group, "Name"), false);
                    }
                    finally
                    {
                        UnityEngine.Object.DestroyImmediate(owner);
                    }
                }
                Assert.IsTrue(
                    EditorSceneManager.SaveScene(scene, root + "/McpAddressablesBuild_" + nonce + ".unity"),
                    "Save only the owned scene before the native build."
                );
                foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
                {
                    UnityEngine.Object asset = AssetDatabase.LoadMainAssetAtPath(AssetDatabase.GUIDToAssetPath(guid));
                    if (asset != null)
                        AssetDatabase.SaveAssetIfDirty(asset);
                }
                Type addressables = Type.GetType("UnityEngine.AddressableAssets.Addressables, Unity.Addressables", true);
                Type layoutTask = PackageType("UnityEditor.AddressableAssets.Build.BuildPipelineTasks.BuildLayoutGenerationTask");
                // Capture task prefixes before changing the public output fields; its static initialization is version-specific.
                string legacyPrefix = (string)layoutTask.GetField("m_LayoutFilePath", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                string reportsPrefix = (string)layoutTask.GetField("m_ReportsFilePath", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                Assert.IsNotNull(legacyPrefix);
                Assert.IsNotNull(reportsPrefix);
                overrides.Add(new TemporaryStatic(addressables, "LibraryPath", library));
                overrides.Add(new TemporaryStatic(addressables, "BuildReportPath", library + "BuildReports/"));
                overrides.Add(new TemporaryStatic(layoutTask, "m_LayoutFilePath", library + "buildlayout"));
                overrides.Add(new TemporaryStatic(layoutTask, "m_ReportsFilePath", library + "BuildReports/buildlayout"));
                Type config = PackageType("UnityEditor.AddressableAssets.Settings.ProjectConfigData");
                overrides.Add(new TemporaryStatic(config, "GenerateBuildLayout", true));
                overrides.Add(
                    new TemporaryStatic(
                        config,
                        "BuildLayoutReportFileFormat",
                        Enum.Parse(config.GetProperty("BuildLayoutReportFileFormat").PropertyType, "JSON")
                    )
                );
                overrides.Add(new TemporaryStatic(config, "AutoOpenAddressablesReport", false));
                overrides.Add(new TemporaryStatic(config, "UserHasBeenInformedAboutBuildReportSettingPreBuild", true));
                Type builderType = PackageType("UnityEditor.AddressableAssets.Build.DataBuilders.BuildScriptPackedMode");
                var builder = ScriptableObject.CreateInstance(builderType);
                AssetDatabase.CreateAsset(builder, root + "/Config/PackedBuilder.asset");
                Invoke(settings, "AddDataBuilder", builder, false);
                AssetDatabase.SaveAssetIfDirty((UnityEngine.Object)settings);
                AssetDatabase.SaveAssetIfDirty(builder);
                Type inputType = PackageType("UnityEditor.AddressableAssets.Build.AddressablesDataBuilderInput");
                object input = Activator.CreateInstance(inputType, new[] { settings, nonce });
                Type resultType = PackageType("UnityEditor.AddressableAssets.Build.AddressablesPlayerBuildResult");
                MethodInfo build = builderType.GetMethods().Single(m => m.Name == "BuildData" && m.IsGenericMethodDefinition && m.GetParameters().Length == 1);
                object result = build.MakeGenericMethod(resultType).Invoke(builder, new[] { input });
                Assert.IsNotNull(result, "Native content build must return its actual result.");
                Assert.IsTrue(string.IsNullOrEmpty((string)Get(result, "Error")), "Native content build failed: " + Get(result, "Error"));
                string[] reports = Directory.GetFiles(Path.Combine(output, "BuildReports"), "buildlayout_*.json");
                Assert.AreEqual(1, reports.Length, "Exactly one genuine report from this owned build is required.");
                string analysisPath = library + "actual-layout.json";
                File.Copy(reports[0], Path.Combine(project, analysisPath), false);
                JObject response = Analyze(analysisPath);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                JObject data = (JObject)response["data"];
                Assert.IsTrue(data.Value<bool>("analysis_complete"), response.ToString());
                Assert.IsFalse(data.Value<bool>("content_build_started"));
                Assert.AreEqual("2.7.6", data.Value<string>("package_version"));
                int total = data.Value<int>("total");
                Assert.That(total, Is.InRange(1, 256), "The tiny real build must yield a bounded nonempty implicit-duplication report.");
                string revision = data.Value<string>("revision");
                var rows = new JArray((JArray)data["items"]);
                for (int page = 2; page <= total; page++)
                {
                    JObject next = Analyze(analysisPath, page, revision);
                    Assert.IsTrue(next.Value<bool>("success"), next.ToString());
                    Assert.AreEqual(revision, next["data"].Value<string>("revision"));
                    foreach (JToken row in (JArray)next["data"]["items"])
                        rows.Add(row);
                }
                Assert.AreEqual(total, rows.Count, "All duplicate objects must be paged without omission.");
                JToken[] shared = rows.Where(row => row.Value<string>("asset_guid") == textureGuid || row.Value<string>("asset_guid") == materialGuid)
                    .ToArray();
                Assert.IsNotEmpty(shared, "The actual shared implicit material/texture must be identified by native asset GUID.");
                Assert.IsTrue(
                    shared.Any(row =>
                        row.Value<int>("bundle_count") >= 2
                        && row.Value<bool>("byte_evidence_complete")
                        && row.Value<ulong>("repeated_serialized_bytes") + row.Value<ulong>("repeated_streamed_bytes") > 0
                    ),
                    "Require real duplicated object byte evidence across the two native bundles."
                );
                File.WriteAllText(
                    Path.Combine(output, "acceptance-evidence.json"),
                    new JObject
                    {
                        ["source_report"] = reports[0],
                        ["analysis_path"] = analysisPath,
                        ["revision"] = revision,
                        ["package_version"] = "2.7.6",
                        ["shared_texture_guid"] = textureGuid,
                        ["shared_material_guid"] = materialGuid,
                        ["duplicate_objects"] = rows,
                        ["native_duration_seconds"] = JToken.FromObject(Get(result, "Duration")),
                    }.ToString()
                );
                File.AppendAllText(Path.Combine(project, analysisPath), "\n");
                JObject stale = Analyze(analysisPath, 2, revision);
                Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
                Assert.AreEqual("stale_addressables_revision", stale.Value<string>("code"));
                Debug.Log("SPEC15 actual local build evidence: " + Path.Combine(output, "acceptance-evidence.json"));
            }
            finally
            {
                var restoreFailures = new List<string>();
                foreach (TemporaryStatic setting in overrides.AsEnumerable().Reverse())
                    if (!setting.Restore())
                        restoreFailures.Add("A changed package setting was preserved rather than overwritten.");
                scenes.Close();
                if (rootGuid != null)
                {
                    Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(root));
                    Assert.IsTrue(
                        AssetDatabase.DeleteAsset(root),
                        "Remove only the nonce-owned authoring assets; retain owned native reports and build evidence."
                    );
                }
                Assert.IsEmpty(restoreFailures, string.Join("; ", restoreFailures));
            }
        }
    }

    [Parallelizable(ParallelScope.None)]
    public class AddressablesRetainedLayoutTests
    {
        // Captured from root GREEN025's actual2.7.6 native report, not a synthesized layout.
        private const string ReceiptHash = "bdaa14780933c48dd55a186ce2028fe40e837a34a6a291a5c56abb94ce898e21";
        private const string TextureGuid = "326dcd191ea37014b9db1ba42375adcc";
        private const string MaterialGuid = "2782b09f629ecb74dacaa7d9db67db26";

        private static string Hash(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static JObject Analyze(string path, int page = 1, string revision = null)
        {
            Type handler = Type.GetType("MCPForUnity.Editor.Tools.Addressables.ManageAddressables, MCPForUnity.Editor");
            Assert.IsNotNull(handler, "The dedicated Addressables handler must analyze the retained genuine native report.");
            var args = new JObject
            {
                ["action"] = "analyze_layout",
                ["layout_path"] = path,
                ["page_number"] = page,
                ["page_size"] = 1,
            };
            if (revision != null)
                args["expected_revision"] = revision;
            return JObject.FromObject(handler.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { args }));
        }

        [Test, Explicit("Root reuses the retained GREEN025 genuine content-build report; this test never starts a content build.")]
        public void RetainedActualLayoutVerifiesDeclaredVersionAndDuplicateObjectBytes()
        {
            string project = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath));
            string allowed = Environment.GetEnvironmentVariable("MCP_SPEC15_PROJECT_ROOT");
            string relative = Environment.GetEnvironmentVariable("MCP_SPEC15_RETAINED_LAYOUT");
            if (
                string.IsNullOrEmpty(allowed)
                || !string.Equals(project.TrimEnd('/', '\\'), Path.GetFullPath(allowed).TrimEnd('/', '\\'), StringComparison.OrdinalIgnoreCase)
                || string.IsNullOrEmpty(relative)
            )
                Assert.Ignore("Root must bind MCP_SPEC15_PROJECT_ROOT and MCP_SPEC15_RETAINED_LAYOUT to its retained GREEN025 native report.");
            Assert.IsTrue(
                relative.StartsWith("Library/MCPForUnity/Addressables/", StringComparison.Ordinal)
                    && !relative.Contains("\\")
                    && relative.Split('/').All(part => part != "" && part != "." && part != "..")
            );
            string source = Path.GetFullPath(Path.Combine(project, relative));
            Assert.IsTrue(File.Exists(source), "The genuine report must already exist; this read-only fixture never rebuilds.");
            Assert.AreEqual(21989, new FileInfo(source).Length, "Bind the exact retained native input before reading it.");
            byte[] original = File.ReadAllBytes(source);
            Assert.AreEqual(ReceiptHash, Hash(original), "Require the exact actual-build receipt bytes, including its native managed-reference graph.");
            JObject native = JObject.Parse(System.Text.Encoding.UTF8.GetString(original));
            Assert.AreEqual("com.unity.addressables: 2.7.6", native.Value<string>("PackageVersion"));
            Assert.AreEqual("", native.Value<string>("BuildError"));
            Assert.AreEqual(5, ((JArray)native["DuplicatedAssets"]).Count);
            string copyDirectory = "Library/MCPForUnity/Addressables/RetainedChecks_" + Guid.NewGuid().ToString("N");
            string copyRelative = copyDirectory + "/parser-copy.json";
            string copyPath = Path.Combine(project, copyRelative);
            string fullDirectory = Path.Combine(project, copyDirectory);
            Assert.IsFalse(Directory.Exists(fullDirectory));
            try
            {
                JObject first = Analyze(relative);
                Assert.IsTrue(first.Value<bool>("success"), "The actual native package declaration must be accepted: " + first);
                JObject firstData = (JObject)first["data"];
                Assert.IsTrue(firstData.Value<bool>("analysis_complete"), first.ToString());
                Assert.IsFalse(firstData.Value<bool>("content_build_started"));
                Assert.AreEqual("2.7.6", firstData.Value<string>("package_version"));
                Assert.AreEqual(5, firstData.Value<int>("total"));
                string revision = firstData.Value<string>("revision");
                var rows = new JArray((JArray)firstData["items"]);
                for (int page = 2; page <= 5; page++)
                {
                    JObject response = Analyze(relative, page, revision);
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    JObject data = (JObject)response["data"];
                    Assert.AreEqual(revision, data.Value<string>("revision"));
                    Assert.AreEqual(page < 5, data.Value<bool>("has_more"));
                    Assert.IsFalse(data.Value<bool>("content_build_started"));
                    foreach (JToken row in (JArray)data["items"])
                        rows.Add(row);
                }
                Assert.AreEqual(5, rows.Count);
                Assert.AreEqual(
                    5,
                    rows.Select(row => row.Value<string>("asset_guid") + "|" + row.Value<string>("local_file_id")).Distinct().Count(),
                    "Every actual duplicated object must appear exactly once across pages."
                );
                foreach (
                    var expected in new[]
                    {
                        new
                        {
                            Guid = TextureGuid,
                            LocalId = "2800000",
                            Bytes = 192UL,
                        },
                        new
                        {
                            Guid = MaterialGuid,
                            LocalId = "2100000",
                            Bytes = 564UL,
                        },
                    }
                )
                {
                    JToken row = rows.Single(item =>
                        item.Value<string>("asset_guid") == expected.Guid && item.Value<string>("local_file_id") == expected.LocalId
                    );
                    Assert.AreEqual(2, row.Value<int>("file_count"), row.ToString());
                    Assert.AreEqual(2, row.Value<int>("bundle_count"), row.ToString());
                    Assert.IsTrue(row.Value<bool>("byte_evidence_complete"), row.ToString());
                    Assert.AreEqual(expected.Bytes, row.Value<ulong>("repeated_serialized_bytes"));
                    Assert.AreEqual(0UL, row.Value<ulong>("repeated_streamed_bytes"));
                    foreach (JToken copy in (JArray)row["copies"])
                    {
                        Assert.AreEqual(expected.Bytes, copy.Value<ulong>("serialized_bytes"));
                        Assert.AreEqual(0UL, copy.Value<ulong>("streamed_bytes"));
                    }
                }
                Directory.CreateDirectory(fullDirectory);
                foreach (
                    string declaration in new[]
                    {
                        "2.7.6",
                        "com.unity.addressables: 2.7.5",
                        "com.other.package: 2.7.6",
                        "com.unity.addressables: 2.7.6-extra",
                        null,
                    }
                )
                {
                    JObject invalid = (JObject)native.DeepClone();
                    if (declaration == null)
                        invalid.Remove("PackageVersion");
                    else
                        invalid["PackageVersion"] = declaration;
                    File.WriteAllText(copyPath, invalid.ToString());
                    JObject refused = Analyze(copyRelative);
                    Assert.IsFalse(
                        refused.Value<bool>("success"),
                        "The installed package version must never replace a missing or unsupported report declaration: " + declaration
                    );
                    Assert.AreEqual("addressables_layout_version_unsupported", refused.Value<string>("code"), refused.ToString());
                }
                File.WriteAllBytes(copyPath, original);
                JObject current = Analyze(copyRelative);
                Assert.IsTrue(current.Value<bool>("success"), current.ToString());
                string before = current["data"].Value<string>("revision");
                File.AppendAllText(copyPath, "\n");
                JObject stale = Analyze(copyRelative, 2, before);
                Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
                Assert.AreEqual("stale_addressables_revision", stale.Value<string>("code"));
                Debug.Log(
                    "SPEC15 retained actual native layout accepted: "
                        + relative
                        + " SHA256="
                        + ReceiptHash
                        + " objects=5 textureRepeatedSerialized=192 materialRepeatedSerialized=564; no content build."
                );
            }
            finally
            {
                Assert.AreEqual(ReceiptHash, Hash(File.ReadAllBytes(source)), "Reads and negative-copy cases must preserve the genuine native report bytes.");
                if (File.Exists(copyPath))
                    File.Delete(copyPath);
                if (Directory.Exists(fullDirectory))
                    Directory.Delete(fullDirectory, false);
            }
        }
    }
}
