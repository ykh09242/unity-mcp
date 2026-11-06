using System;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class ServerLaunchLogTests
    {
        private string _root;
        private string _path;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(Path.GetTempPath(), "mcp-launch-tail-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_root);
            _path = Path.Combine(_root, "launch.log");
        }

        [TearDown]
        public void TearDown()
        {
            // This fixture exclusively owns the newly allocated temporary directory.
            Directory.Delete(_root, true);
        }

        [TestCase("")]
        [TestCase("\n")]
        [TestCase("\r\n")]
        public void TailFile_GiantLineHasABoundedReadAndResult(string ending)
        {
            File.WriteAllText(_path, new string('x', 512 * 1024) + ending, new UTF8Encoding(false));
            string tail = ServerManagementService.TailFile(_path, 40);
            Assert.AreEqual(64 * 1024 - ending.Length, tail.Length);
            Assert.IsTrue(tail.All(character => character == 'x'));
        }

        [Test]
        public void TailFile_RetainsLastLinesAfterLargeUtf8Prefix()
        {
            string[] lines = Enumerable.Range(1, 80).Select(index => $"line {index} 한글").ToArray();
            File.WriteAllText(_path, new string('한', 256 * 1024) + "\n" + string.Join("\r\n", lines) + "\r\n", new UTF8Encoding(false));
            Assert.AreEqual(string.Join(Environment.NewLine, lines.Skip(40)), ServerManagementService.TailFile(_path, 40));
        }

        [Test]
        public void TailFile_ReadsAStillOpenLaunchLog()
        {
            using var stream = new FileStream(_path, FileMode.Create, FileAccess.Write, FileShare.ReadWrite);
            using var writer = new StreamWriter(stream, new UTF8Encoding(false));
            writer.Write("first\nlast diagnostic 한글\n");
            writer.Flush();
            Assert.AreEqual("last diagnostic 한글", ServerManagementService.TailFile(_path, 1));
        }

        [TestCase(0)]
        [TestCase(-1)]
        public void TailFile_NonPositiveLineLimitIsEmpty(int limit)
        {
            File.WriteAllText(_path, "diagnostic");
            Assert.IsEmpty(ServerManagementService.TailFile(_path, limit));
        }

        [Test]
        public void TailFile_MissingLogIsEmpty()
        {
            Assert.IsEmpty(ServerManagementService.TailFile(_path, 40));
        }
    }
}
