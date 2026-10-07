using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Animation;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Helpers
{
    public class LinkedAssetSecurityTests
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        private static void Link(string link, string target, bool directory)
        {
            bool created =
                Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, (directory ? 1 : 0) | 2) : symlink(target, link) == 0;
            if (!created)
                Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PersistentLinkedTextureIdIsRejectedBeforeAnyMaterialPropertyChanges(bool encoded)
        {
            Shader shader = Shader.Find("Standard");
            if (shader == null || shader.FindPropertyIndex("_Glossiness") < 0)
                Assert.Ignore("Standard shader is unavailable for the material reference regression.");
            string id = "LinkedReferenceId_" + Guid.NewGuid().ToString("N");
            string root = Path.Combine(Application.dataPath, id);
            string ownedTargetRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "../.omo/" + id));
            string relativePath = "Assets/" + id + "/Probe.asset";
            string fullPath = Path.Combine(root, "Probe.asset");
            string savedPath = Path.Combine(ownedTargetRoot, "Probe.asset");
            Directory.CreateDirectory(root);
            Directory.CreateDirectory(ownedTargetRoot);
            var texture = new Texture2D(1, 1);
            var material = new Material(shader);
            try
            {
                AssetDatabase.CreateAsset(texture, relativePath);
                var serializer = JsonSerializer.Create();
                serializer.Converters.Add(new UnityEngineObjectConverter());
                var reference = new JObject { ["instanceID"] = texture.GetInstanceIDCompat() };
                Assert.AreSame(texture, reference.ToObject<Texture>(serializer), "Ordinary persistent Assets IDs must remain usable.");
                File.Move(fullPath, savedPath);
                Link(fullPath, savedPath, false);
                Assert.AreEqual(relativePath, AssetDatabase.GetAssetPath(texture));
                float original = material.GetFloat("_Glossiness");
                JToken referenceToken = encoded ? new JValue(reference.ToString(Formatting.None)) : reference;
                var properties = new JObject
                {
                    ["float"] = new JObject { ["name"] = "_Glossiness", ["value"] = original == 1f ? 0f : 1f },
                    ["_MainTex"] = referenceToken,
                };
                Assert.Catch<Exception>(() => MaterialOps.ApplyProperties(material, properties, serializer));
                Assert.AreEqual(original, material.GetFloat("_Glossiness"), "Rejected reference partially changed the material.");
                Assert.Catch<Exception>(() => reference.ToObject<Texture>(serializer));
            }
            finally
            {
                // Restore the owned ordinary entry before asking Unity to delete it; remove only the link itself.
                File.Delete(fullPath);
                if (File.Exists(savedPath))
                    File.Move(savedPath, fullPath);
                AssetDatabase.DeleteAsset(relativePath);
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(material);
                if (Directory.Exists(root))
                    Directory.Delete(root);
                File.Delete(root + ".meta");
                Directory.Delete(ownedTargetRoot);
            }
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void AssetConsumersRejectFinalAndAncestorLinksIncludingBrokenTargets(bool directory, bool broken)
        {
            string id = "LinkedAssetSecurity_" + Guid.NewGuid().ToString("N");
            string root = Path.Combine(Application.dataPath, id);
            string outside = Path.GetFullPath(Path.Combine(Application.dataPath, "../" + id));
            string[] extensions = { ".cs", ".uss", ".png", ".prefab" };
            Directory.CreateDirectory(root);
            if (!broken)
                Directory.CreateDirectory(outside);
            try
            {
                if (!broken)
                    foreach (string ext in extensions)
                        File.WriteAllText(Path.Combine(outside, "Probe" + ext), "// sentinel");
                string relative = "Assets/" + id;
                if (directory)
                {
                    Link(Path.Combine(root, "Linked"), outside, true);
                    relative += "/Linked";
                }
                else
                    foreach (string ext in extensions)
                        Link(Path.Combine(root, "Probe" + ext), Path.Combine(outside, "Probe" + ext), false);

                Exception denied = Assert.Catch<Exception>(() => AssetPathUtility.GetFullAssetPath(relative + "/Probe.cs"));
                Assert.That(denied, Is.InstanceOf<InvalidOperationException>().Or.InstanceOf<IOException>());
                Assert.Catch<Exception>(() => AssetPathUtility.SanitizeAssetPath(relative + "/Probe.cs"));
                Assert.Catch<Exception>(() => AssetPathUtility.GetAssetReferencePath(relative + "/Probe.prefab", allowPackages: true));
                Assert.Catch<Exception>(() => UnityAssetPath.Resolve(relative + "/Probe.png", allowPackages: true, allowBuiltIn: true));
                Assert.IsFalse(AssetGenPaths.TryGetAssetsRelativePath(relative + "/Probe.png", out _));
                Assert.IsFalse(LocalImage.ResolveExisting(relative + "/Probe.png", out _, out _));
                Assert.Throws<UnauthorizedAccessException>(() => LocalImage.ToDataUri(relative + "/Probe.png"));
                foreach (string action in new[] { "read", "get_sha", "update", "delete", "apply_text_edits", "validate", "edit" })
                {
                    var result = JObject.FromObject(
                        ManageScript.HandleCommand(
                            new JObject
                            {
                                ["action"] = action,
                                ["path"] = relative,
                                ["name"] = "Probe",
                                ["contents"] = "// changed",
                            }
                        )
                    );
                    Assert.IsFalse(result.Value<bool>("success"), "script " + action);
                }
                foreach (string action in new[] { "read", "create", "update", "delete" })
                {
                    var result = JObject.FromObject(
                        ManageUI.HandleCommand(
                            new JObject
                            {
                                ["action"] = action,
                                ["path"] = relative + "/Probe.uss",
                                ["contents"] = ".a { color: red; }",
                            }
                        )
                    );
                    Assert.IsFalse(result.Value<bool>("success"), "UI " + action);
                }
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManageTexture\] Action 'delete' failed:"));
                Assert.IsFalse(
                    JObject
                        .FromObject(ManageTexture.HandleCommand(new JObject { ["action"] = "delete", ["path"] = relative + "/Probe.png" }))
                        .Value<bool>("success")
                );
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManagePrefabs\] Action 'get_info' failed:"));
                Assert.IsFalse(
                    JObject
                        .FromObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "get_info", ["path"] = relative + "/Probe.prefab" }))
                        .Value<bool>("success")
                );
                if (!broken)
                    foreach (string ext in extensions)
                        Assert.AreEqual("// sentinel", File.ReadAllText(Path.Combine(outside, "Probe" + ext)));
            }
            finally
            {
                // Delete links themselves without traversing the directory target.
                string directoryLink = Path.Combine(root, "Linked");
                if (directory)
                {
                    try
                    {
                        Directory.Delete(directoryLink);
                    }
                    catch (DirectoryNotFoundException) { }
                }
                else
                    foreach (string ext in extensions)
                        File.Delete(Path.Combine(root, "Probe" + ext));
                foreach (string file in Directory.GetFiles(root))
                    File.Delete(file);
                Directory.Delete(root);
                File.Delete(root + ".meta");
                if (Directory.Exists(outside))
                {
                    foreach (string ext in extensions)
                        File.Delete(Path.Combine(outside, "Probe" + ext));
                    Directory.Delete(outside);
                }
            }
        }

        [TestCase("create", false, true, false)]
        [TestCase("duplicate", false, true, false)]
        [TestCase("move", false, true, false)]
        [TestCase("rename", false, true, false)]
        [TestCase("create", false, true, true)]
        [TestCase("duplicate", false, false, true)]
        [TestCase("move", false, false, true)]
        [TestCase("duplicate", true, true, false)]
        [TestCase("move", true, true, false)]
        [TestCase("rename", true, false, false)]
        [TestCase("duplicate", true, false, true)]
        [TestCase("move", true, true, true)]
        public void AssetOperationsRejectLinksBeforeCreatingDirectories(string action, bool sourceLink, bool directory, bool broken)
        {
            string id = "LinkedAssetOperation_" + Guid.NewGuid().ToString("N");
            string root = Path.Combine(Application.dataPath, id);
            string outside = Path.Combine(root, "Outside");
            string link = Path.Combine(root, directory ? "Linked" : "Linked.asset");
            string normalSource = Path.Combine(root, "Source.asset");
            Directory.CreateDirectory(root);
            File.WriteAllText(normalSource, "source sentinel");
            if (!broken)
            {
                Directory.CreateDirectory(outside);
                File.WriteAllText(Path.Combine(outside, "Source.asset"), "outside sentinel");
            }
            try
            {
                Link(link, directory ? outside : Path.Combine(outside, "Source.asset"), directory);
                string relative = "Assets/" + id;
                string linkedPath = relative + (directory ? "/Linked/Source.asset" : "/Linked.asset");
                string destination = sourceLink ? relative + "/New/Probe.asset" : relative + (directory ? "/Linked/New/Probe.asset" : "/Linked.asset");
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManageAsset\] Action '" + action + @"' failed"));
                var result = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["path"] =
                                action == "create" ? destination
                                : sourceLink ? linkedPath
                                : relative + "/Source.asset",
                            ["destination"] = destination,
                            ["assetType"] = "PhysicsMaterial",
                        }
                    )
                );
                Assert.IsFalse(result.Value<bool>("success"));
                Assert.IsFalse(Directory.Exists(Path.Combine(root, "New")), "Rejected source must not prepare a destination.");
                Assert.IsFalse(Directory.Exists(Path.Combine(outside, "New")), "Rejected destination must not create linked children.");
                Assert.AreEqual("source sentinel", File.ReadAllText(normalSource));
                if (!broken)
                    Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(outside, "Source.asset")));
            }
            finally
            {
                // Remove the link itself first; every target belongs to this GUID-named tree.
                if (directory)
                {
                    try
                    {
                        Directory.Delete(link);
                    }
                    catch (DirectoryNotFoundException) { }
                }
                else
                    File.Delete(link);
                Directory.Delete(root, true);
                File.Delete(root + ".meta");
            }
        }

        [TestCase("material", true, false, false)]
        [TestCase("material", false, false, false)]
        [TestCase("material", false, true, true)]
        [TestCase("clip", true, false, false)]
        [TestCase("clip", false, false, false)]
        [TestCase("clip", false, true, true)]
        [TestCase("controller", true, false, false)]
        [TestCase("preset", true, false, false)]
        [TestCase("physics", true, false, false)]
        [TestCase("physics", false, true, false)]
        [TestCase("scriptable", true, false, false)]
        [TestCase("scriptable_modify", true, false, false)]
        [TestCase("import", true, false, false)]
        [TestCase("modify", true, false, false)]
        [TestCase("delete", true, false, false)]
        public void RemainingAssetMutationConsumersRejectLinkedPaths(string consumer, bool directory, bool broken, bool omitExtension)
        {
            string id = "LinkedMutation_" + Guid.NewGuid().ToString("N");
            string root = Path.Combine(Application.dataPath, id);
            string outside = Path.Combine(root, "Outside");
            string extension =
                consumer == "material" ? ".mat"
                : consumer == "controller" ? ".controller"
                : consumer == "physics" ? ".physicMaterial"
                : consumer == "clip" || consumer == "preset" ? ".anim"
                : ".asset";
            string link = Path.Combine(root, directory ? "Linked" : "Probe" + extension);
            Directory.CreateDirectory(root);
            if (!broken)
            {
                Directory.CreateDirectory(outside);
                File.WriteAllText(Path.Combine(outside, "Probe" + extension), "outside sentinel");
            }
            try
            {
                Link(link, directory ? outside : Path.Combine(outside, "Probe" + extension), directory);
                string relative = "Assets/" + id;
                bool existingTarget = consumer == "import" || consumer == "modify" || consumer == "delete" || consumer == "scriptable_modify";
                string assetPath =
                    relative
                    + (
                        directory
                            ? existingTarget
                                ? "/Linked/Probe"
                                : "/Linked/New/Probe"
                            : "/Probe"
                    )
                    + (omitExtension ? "" : extension);
                string folderPath = relative + "/Linked/New";
                JObject request;
                object result;
                if (consumer == "material")
                {
                    request = new JObject { ["action"] = "create", ["materialPath"] = assetPath };
                    result = ManageMaterial.HandleCommand(request);
                }
                else if (consumer == "clip" || consumer == "controller" || consumer == "preset")
                {
                    string action =
                        consumer == "controller" ? "controller_create"
                        : consumer == "preset" ? "clip_create_preset"
                        : "clip_create";
                    LogAssert.Expect(LogType.Error, new Regex(@"\[ManageAnimation\] Action '" + action + @"' failed"));
                    request = new JObject
                    {
                        ["action"] = action,
                        [consumer == "controller" ? "controllerPath" : "clipPath"] = assetPath,
                        ["preset"] = "bounce",
                    };
                    result = ManageAnimation.HandleCommand(request);
                }
                else if (consumer == "physics")
                {
                    LogAssert.Expect(LogType.Error, new Regex(@"\[ManagePhysics\] Action 'create_physics_material' failed"));
                    request = new JObject
                    {
                        ["action"] = "create_physics_material",
                        ["path"] = directory ? folderPath : relative,
                        ["name"] = "Probe",
                    };
                    result = ManagePhysics.HandleCommand(request);
                }
                else if (consumer == "scriptable" || consumer == "scriptable_modify")
                {
                    request =
                        consumer == "scriptable"
                            ? new JObject
                            {
                                ["action"] = "create",
                                ["folderPath"] = folderPath,
                                ["assetName"] = "Probe",
                                ["typeName"] = typeof(LinkedPathScriptableFixture).FullName,
                            }
                            : new JObject
                            {
                                ["action"] = "modify",
                                ["target"] = new JObject { ["path"] = assetPath },
                                ["patches"] = new JArray(),
                            };
                    result = ManageScriptableObject.HandleCommand(request);
                }
                else
                {
                    LogAssert.Expect(LogType.Error, new Regex(@"\[ManageAsset\] Action '" + consumer + @"' failed"));
                    request = new JObject
                    {
                        ["action"] = consumer,
                        ["path"] = assetPath,
                        ["properties"] = new JObject { ["name"] = "Changed" },
                    };
                    result = ManageAsset.HandleCommand(request);
                }
                Assert.IsFalse(JObject.FromObject(result).Value<bool>("success"));
                Assert.IsFalse(Directory.Exists(Path.Combine(outside, "New")));
                if (!broken)
                    Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(outside, "Probe" + extension)));
            }
            finally
            {
                if (directory)
                {
                    try
                    {
                        Directory.Delete(link);
                    }
                    catch (DirectoryNotFoundException) { }
                }
                else
                    File.Delete(link);
                Directory.Delete(root, true);
                File.Delete(root + ".meta");
            }
        }

        [Test]
        public void AbsentFilesAreAllowedOnlyInsideTheRoot()
        {
            string valid = SafePathUtility.ResolveWithinRoot(Application.dataPath, "NewFolder/NotCreated.cs");
            StringAssert.StartsWith(Path.GetFullPath(Application.dataPath), valid);
            Assert.Throws<InvalidOperationException>(() => SafePathUtility.ResolveWithinRoot(Application.dataPath, "../Outside.cs"));
        }
    }

    public class LinkedPathScriptableFixture : ScriptableObject
    {
        public int value;
    }
}
