using MCPForUnity.Editor.Services.Server;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services.Server
{
    public class ProcessLivenessTests
    {
        [Test]
        public void CurrentEditorProcessRemainsAlive()
        {
            using var editor = System.Diagnostics.Process.GetCurrentProcess();
            Assert.IsTrue(new ProcessDetector().ProcessExists(editor.Id));
            Assert.IsFalse(editor.HasExited);
        }

        [TestCase(-1)]
        [TestCase(0)]
        [TestCase(int.MaxValue)]
        public void MissingOrInvalidProcessIsNotAlive(int pid)
        {
            Assert.IsFalse(new ProcessDetector().ProcessExists(pid));
        }
    }
}
