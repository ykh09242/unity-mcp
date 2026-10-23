using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.Server;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services.Server
{
    /// <summary>Retained public API and refusal guards, without launching a process killer.</summary>
    [TestFixture]
    public class ProcessTerminatorTests
    {
#pragma warning disable CS0618 // Intentional compatibility coverage of the deprecated public class.
        [Test]
        public void Constructor_NullDetector_ThrowsArgumentNullException()
        {
            Assert.Throws<ArgumentNullException>(() => new ProcessTerminator(null));
        }

        [TestCase(-1)]
        [TestCase(0)]
        [TestCase(1)]
        [TestCase(42)]
        public void LegacyInterface_RefusesUnsafeOrSelfPid(int pid)
        {
            IProcessTerminator terminator = new ProcessTerminator(new GuardDetector());
            Assert.That(terminator.Terminate(pid), Is.False);
        }
#pragma warning restore CS0618

        private sealed class GuardDetector : IProcessDetector
        {
            public int GetCurrentProcessId() => 42;

            public bool LooksLikeMcpServerProcess(int pid) => throw new InvalidOperationException("Unexpected inspection");

            public bool TryGetProcessCommandLine(int pid, out string argsLower) => throw new InvalidOperationException("Unexpected inspection");

            public List<int> GetListeningProcessIdsForPort(int port) => throw new InvalidOperationException("Unexpected inspection");

            public bool ProcessExists(int pid) => throw new InvalidOperationException("Unexpected inspection");

            public string NormalizeForMatch(string input) => throw new InvalidOperationException("Unexpected inspection");
        }
    }
}
