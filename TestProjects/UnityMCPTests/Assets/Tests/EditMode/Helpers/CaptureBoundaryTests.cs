using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Blender;
using MCPForUnity.Editor.Windows.Components.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    [Parallelizable(ParallelScope.None)]
    public class CaptureBoundaryTests
    {
        private string root;
        private const BindingFlags StaticPrivate = BindingFlags.Static | BindingFlags.NonPublic;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "mcp-capture-boundary-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(Path.GetFileName(root).StartsWith("mcp-capture-boundary-", StringComparison.Ordinal));
            Directory.Delete(root, true);
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void PlayerOutputUnderAssetsIsRejectedBeforeSceneOrManifestAccess(bool alternateSlash, bool upperCase)
        {
            string output = Path.Combine(Application.dataPath, "mcp-invalid-player-" + Guid.NewGuid().ToString("N"));
            if (alternateSlash)
                output = output.Replace('\\', '/');
            if (upperCase && Path.DirectorySeparatorChar == '\\')
                output = output.ToUpperInvariant();
            output += Path.DirectorySeparatorChar;
            var error = Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBuild.BuildFrozen(null, null, output));
            StringAssert.Contains("outside Assets", error.Message);
            Assert.IsFalse(Directory.Exists(output));
        }

        [Test]
        public void EightKSquareAndSubstantialCaptureBatchAreAdmitted()
        {
            Assert.DoesNotThrow(() => ScreenshotUtility.ValidateFrameDimensions(8192, 8192));
            Assert.DoesNotThrow(() => ScreenshotUtility.ValidateBatchCapture(7680, 4320, 36, 320));
            var budget = new ScreenshotUtility.CaptureBatchBudget();
            for (int index = 0; index < 36; index++)
                Assert.DoesNotThrow(() => budget.AdmitFrame(7680, 4320, 320, 180));
            Type type = typeof(MCPForUnity.Editor.Tools.ManageEditor).Assembly.GetType("MCPForUnity.Editor.Helpers.GameViewSizeControl", true);
            type.GetMethod("ValidateFixedSize", StaticPrivate).Invoke(null, new object[] { 7680, 4320 });
        }

        [Test]
        public void PlayerContainmentUsesCanonicalDirectoryBoundaries()
        {
            string child = Path.Combine(root, "child");
            Assert.IsTrue(PlayScenarioPlayerFiles.IsWithin(root + Path.DirectorySeparatorChar, child));
            Assert.IsTrue(PlayScenarioPlayerFiles.IsWithin(root, root + Path.DirectorySeparatorChar));
            Assert.IsFalse(PlayScenarioPlayerFiles.IsWithin(root, root + "-other"));
        }

        [Test]
        public void MultiviewSavesWithTheSameNamePreserveBothCaptures()
        {
            var method = typeof(McpToolsSection).GetMethod("SaveMultiviewCapture", StaticPrivate);
            Assert.IsNotNull(method, "The UI must use the shared guarded capture writer");
            var first = (ScreenshotCaptureResult)method.Invoke(null, new object[] { Convert.ToBase64String(new byte[] { 1, 2 }), root, "Multiview_fixed" });
            var second = (ScreenshotCaptureResult)method.Invoke(null, new object[] { Convert.ToBase64String(new byte[] { 3, 4 }), root, "Multiview_fixed" });
            Assert.AreNotEqual(first.FullPath, second.FullPath);
            CollectionAssert.AreEqual(new byte[] { 1, 2 }, File.ReadAllBytes(first.FullPath));
            CollectionAssert.AreEqual(new byte[] { 3, 4 }, File.ReadAllBytes(second.FullPath));
        }

        [Test]
        public void MultiviewRejectsLinkedFinalFileBeforeChangingItsDestination()
        {
            string destination = Path.Combine(root, "destination");
            string link = Path.Combine(root, "Multiview_fixed.png");
            File.WriteAllText(destination, "owned-original");
            Link(link, destination, false);
            try
            {
                var method = typeof(McpToolsSection).GetMethod("SaveMultiviewCapture", StaticPrivate);
                Assert.IsNotNull(method);
                var error = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { "AQI=", root, "Multiview_fixed" }));
                Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
                Assert.AreEqual("owned-original", File.ReadAllText(destination));
                Assert.AreEqual(2, Directory.GetFileSystemEntries(root).Length);
            }
            finally
            {
                File.Delete(link);
            }
        }

        [TestCase("ScreenshotAsync", 8193)]
        [TestCase("ScreenshotAsync", int.MaxValue)]
        [TestCase("CompareScreenshotAsync", 8193)]
        [TestCase("CompareScreenshotAsync", int.MaxValue)]
        public async Task OversizedBlenderCaptureIsRejectedBeforeResolvingAnEndpoint(string operation, int size)
        {
            var previous = BlenderBridgeTool.EndpointOverride;
            int endpoints = 0;
            var target = new GameObject("OwnedCaptureTarget_" + Guid.NewGuid().ToString("N"));
            try
            {
                BlenderBridgeTool.EndpointOverride = () =>
                {
                    endpoints++;
                    throw new InvalidOperationException("A remote capture must not be admitted");
                };
                var method = typeof(BlenderBridgeTool).GetMethod(operation, StaticPrivate);
                var p = new ToolParams(new JObject { ["max_size"] = size, ["game_object"] = target.name });
                try
                {
                    await (Task<object>)method.Invoke(null, new object[] { p, 5, CancellationToken.None });
                    Assert.Fail("An over-budget capture must be refused");
                }
                catch (ArgumentException) { }
                Assert.AreEqual(0, endpoints);
            }
            finally
            {
                BlenderBridgeTool.EndpointOverride = previous;
                UnityEngine.Object.DestroyImmediate(target);
            }
        }

        [Test]
        public void BlenderStagingRefusesLinkedLibraryBeforeCreatingAnOutput()
        {
            string project = Path.Combine(root, "project");
            string external = Path.Combine(root, "external");
            Directory.CreateDirectory(project);
            Directory.CreateDirectory(external);
            string link = Path.Combine(project, "Library");
            Link(link, external, true);
            try
            {
                var type = CaptureScopeType();
                var error = Assert.Throws<TargetInvocationException>(() => Activator.CreateInstance(type, new object[] { project }));
                Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
                Assert.IsEmpty(Directory.GetFileSystemEntries(external));
            }
            finally
            {
                Directory.Delete(link);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CaptureScopeRemovesOnlyItsUnreturnedIntermediate(bool retainFinal)
        {
            string sibling = Path.Combine(root, "unrelated.txt");
            File.WriteAllText(sibling, "keep");
            var type = CaptureScopeType();
            string temporary,
                final;
            using (var scope = (IDisposable)Activator.CreateInstance(type, new object[] { root }))
            {
                temporary = (string)type.GetMethod("PathFor").Invoke(scope, new object[] { "unity.png" });
                final = (string)type.GetMethod("PathFor").Invoke(scope, new object[] { "comparison.png" });
                File.WriteAllText(temporary, "temporary");
                File.WriteAllText(final, "retained");
                if (retainFinal)
                    type.GetMethod("Retain").Invoke(scope, new object[] { final });
            }
            Assert.IsFalse(File.Exists(temporary));
            Assert.AreEqual(retainFinal, File.Exists(final));
            Assert.AreEqual("keep", File.ReadAllText(sibling));
        }

        [TestCase(8193, 1)]
        [TestCase(8192, 8193)]
        [TestCase(int.MaxValue, int.MaxValue)]
        public void PngHeaderBudgetIsCheckedBeforeTextureAllocation(int width, int height)
        {
            byte[] bytes = new byte[33];
            new byte[] { 137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82 }.CopyTo(bytes, 0);
            for (int i = 0; i < 4; i++)
            {
                bytes[16 + i] = (byte)((uint)width >> (24 - 8 * i));
                bytes[20 + i] = (byte)((uint)height >> (24 - 8 * i));
            }
            string path = Path.Combine(root, "oversized.png");
            File.WriteAllBytes(path, bytes);
            var before = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray();
            var method = typeof(BlenderBridgeTool).GetMethod("ReadCapturePng", StaticPrivate);
            Assert.IsNotNull(method, "External PNG headers must be bounded before decoding");
            var error = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { path }));
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            CollectionAssert.AreEquivalent(before, UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray());
        }

        private static Type CaptureScopeType()
        {
            var type = typeof(BlenderBridgeTool).GetNestedType("CaptureFiles", BindingFlags.NonPublic);
            Assert.IsNotNull(type, "Capture operations must own their staging files");
            return type;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        private static void Link(string link, string target, bool directory)
        {
            bool created =
                Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, (directory ? 1 : 0) | 2) : symlink(target, link) == 0;
            Assert.IsTrue(created, "Owned link fixture is required: " + Marshal.GetLastWin32Error());
        }
    }
}
