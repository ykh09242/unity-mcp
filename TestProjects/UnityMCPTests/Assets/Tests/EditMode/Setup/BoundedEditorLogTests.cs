using System;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class BoundedEditorLogTests
    {
        private static object Create()
        {
            var type = typeof(McpForUnitySkillInstaller).Assembly.GetType("MCPForUnity.Editor.Setup.BoundedEditorLog");
            Assert.IsNotNull(type, "Installer log storage must bound producers and retained output.");
            return Activator.CreateInstance(type, true);
        }

        private static object Call(object log, string method, params object[] args) =>
            log.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).Invoke(log, args);

        private static string Snapshot(object log) => (string)Call(log, "Snapshot");

        [Test]
        public void RepeatedOutputKeepsOnlyBoundedNewestTailAndReusesUnchangedText()
        {
            var log = Create();
            string line = new('x', 4096);
            for (int i = 0; i < 10000; i++)
                Call(log, "Append", line);
            Call(log, "Append", "newest-marker");
            string text = Snapshot(log);
            Assert.LessOrEqual(text.Length, 262300);
            StringAssert.Contains("newest-marker", text);
            StringAssert.Contains("omitted", text);
            Assert.AreSame(text, Snapshot(log));
            Call(log, "Clear");
            Assert.AreEqual(string.Empty, Snapshot(log));
        }

        [Test]
        public void HugeLinesAndParallelProducersStayBoundedWithoutTornSnapshots()
        {
            var log = Create();
            Call(log, "Append", new string('x', 1000000));
            Assert.Less(Snapshot(log).Length, 4200);
            Parallel.For(
                0,
                4,
                producer =>
                {
                    for (int i = 0; i < 500; i++)
                        Call(log, "Append", "producer-" + producer + "-line-" + i);
                }
            );
            string text = Snapshot(log);
            Assert.LessOrEqual(text.Length, 262300);
            Assert.LessOrEqual(text.Split('\n').Length, 1027);
            Call(log, "Clear");
            Call(log, "Append", "after-clear");
            text = Snapshot(log);
            StringAssert.Contains("after-clear", text);
            StringAssert.DoesNotContain("producer-", text);
            Assert.AreSame(text, Snapshot(log));
        }
    }
}
