using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
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
            bool created = Application.platform == RuntimePlatform.WindowsEditor
                ? CreateSymbolicLinkW(link, target, (directory ? 1 : 0) | 2)
                : symlink(target, link) == 0;
            if (!created) Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
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
            if (!broken) Directory.CreateDirectory(outside);
            try
            {
                if (!broken)
                    foreach (string ext in extensions) File.WriteAllText(Path.Combine(outside, "Probe" + ext), "// sentinel");
                string relative = "Assets/" + id;
                if (directory)
                {
                    Link(Path.Combine(root, "Linked"), outside, true);
                    relative += "/Linked";
                }
                else
                    foreach (string ext in extensions) Link(Path.Combine(root, "Probe" + ext), Path.Combine(outside, "Probe" + ext), false);

                Exception denied = Assert.Catch<Exception>(() => AssetPathUtility.GetFullAssetPath(relative + "/Probe.cs"));
                Assert.That(denied, Is.InstanceOf<InvalidOperationException>().Or.InstanceOf<IOException>());
                Assert.IsFalse(AssetGenPaths.TryGetAssetsRelativePath(relative + "/Probe.png", out _));
                Assert.IsFalse(LocalImage.ResolveExisting(relative + "/Probe.png", out _, out _));
                Assert.Throws<UnauthorizedAccessException>(() => LocalImage.ToDataUri(relative + "/Probe.png"));
                foreach (string action in new[] { "read", "get_sha", "update", "delete", "apply_text_edits", "validate", "edit" })
                {
                    var result = JObject.FromObject(ManageScript.HandleCommand(new JObject
                    { ["action"] = action, ["path"] = relative, ["name"] = "Probe", ["contents"] = "// changed" }));
                    Assert.IsFalse(result.Value<bool>("success"), "script " + action);
                }
                foreach (string action in new[] { "read", "create", "update", "delete" })
                {
                    var result = JObject.FromObject(ManageUI.HandleCommand(new JObject
                    { ["action"] = action, ["path"] = relative + "/Probe.uss", ["contents"] = ".a { color: red; }" }));
                    Assert.IsFalse(result.Value<bool>("success"), "UI " + action);
                }
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManageTexture\] Action 'delete' failed:"));
                Assert.IsFalse(JObject.FromObject(ManageTexture.HandleCommand(new JObject
                { ["action"] = "delete", ["path"] = relative + "/Probe.png" })).Value<bool>("success"));
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManagePrefabs\] Action 'get_info' failed:"));
                Assert.IsFalse(JObject.FromObject(ManagePrefabs.HandleCommand(new JObject
                { ["action"] = "get_info", ["path"] = relative + "/Probe.prefab" })).Value<bool>("success"));
                if (!broken)
                    foreach (string ext in extensions) Assert.AreEqual("// sentinel", File.ReadAllText(Path.Combine(outside, "Probe" + ext)));
            }
            finally
            {
                // Delete links themselves without traversing the directory target.
                string directoryLink = Path.Combine(root, "Linked");
                if (directory)
                {
                    try { Directory.Delete(directoryLink); } catch (DirectoryNotFoundException) { }
                }
                else
                    foreach (string ext in extensions) File.Delete(Path.Combine(root, "Probe" + ext));
                foreach (string file in Directory.GetFiles(root)) File.Delete(file);
                Directory.Delete(root);
                File.Delete(root + ".meta");
                if (Directory.Exists(outside))
                {
                    foreach (string ext in extensions) File.Delete(Path.Combine(outside, "Probe" + ext));
                    Directory.Delete(outside);
                }
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
}
