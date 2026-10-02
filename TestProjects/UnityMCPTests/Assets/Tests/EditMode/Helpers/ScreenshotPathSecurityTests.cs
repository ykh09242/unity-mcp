using System;
using System.IO;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ScreenshotPathSecurityTests
    {
        [TestCase("../escape.png")]
        [TestCase("..\\escape.png")]
        [TestCase("/tmp/escape.png")]
        [TestCase("C:\\escape.png")]
        [TestCase("file:stream.png")]
        [TestCase("//host/share/escape.png")]
        public void ScreenshotRejectsPathInFilename(string name)
        {
            Assert.Throws<InvalidOperationException>(() =>
                ScreenshotUtility.PrepareCaptureResult(name, 1, true, "Captures", false));
        }

        [Test]
        public void ScreenshotAcceptsSimpleNameInConfiguredFolder()
        {
            var result = ScreenshotUtility.PrepareCaptureResult("security-test", 1, true, "Captures", false);
            Assert.AreEqual("security-test.png", Path.GetFileName(result.FullPath));
            Assert.AreEqual("Captures/security-test.png", result.ProjectRelativePath);
        }
    }
}
