using System;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    // Pure owned strings and mode integers only. Never invokes LogEntries, reads the
    // user's console, emits logs, or modifies console filters.
    public class ConsoleEntryIntegrityTests
    {
        [OneTimeSetUp]
        public void RequireReflectionMetadataBeforeToolInitialization()
        {
            // ReadConsole's initializer reflects these members. Check their presence
            // without invoking them so a missing layout skips before its error logger.
            const BindingFlags methods = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
            const BindingFlags fields = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
            Assembly editor = typeof(EditorApplication).Assembly;
            Type entries = editor.GetType("UnityEditor.LogEntries");
            Type entry = editor.GetType("UnityEditor.LogEntry");
            if (entries == null || entry == null)
                Assert.Ignore("Internal console metadata unavailable.");
            foreach (string name in new[] { "StartGettingEntries", "EndGettingEntries", "Clear", "GetCount", "GetEntryInternal" })
                if (entries.GetMethod(name, methods) == null)
                    Assert.Ignore("Required console method metadata unavailable.");
            foreach (string name in new[] { "mode", "message", "file", "line" })
                if (entry.GetField(name, fields) == null)
                    Assert.Ignore("Required console field metadata unavailable.");
        }

        private static (string body, string stackTrace) Split(string text, int? offset)
        {
            MethodInfo method = typeof(ReadConsole).GetMethod("SplitMessageAndStackTrace", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            Assert.AreEqual(2, method.GetParameters().Length, "Native-boundary splitter must accept the supplied UTF-16 offset.");
            return ((string body, string stackTrace))method.Invoke(null, new object[] { text, offset });
        }

        [TestCase("LogError documentation")]
        [TestCase("Exception documentation")]
        [TestCase("Summary\nVersion.2 is ready\nfinish")]
        [TestCase("Summary\nat noon we meet\nfinish")]
        [TestCase("Summary\nUnityEngine.Debug example\nfinish")]
        public void NativeNoStackBoundaryPreservesBody(string text)
        {
            var result = Split(text, 0);
            Assert.AreEqual(text, result.body);
            Assert.IsNull(result.stackTrace);
        }

        [Test]
        public void Utf16OffsetPrecedesUnicodeAndCrLfNormalization()
        {
            const string body = "😀 summary\r\nUpper.Case body\r\n";
            const string stack = "Fixture`1[T]:Method (T) (at Fixture.cs:7)";
            var result = Split(body + stack, body.Length);
            Assert.AreEqual("😀 summary\nUpper.Case body", result.body);
            Assert.AreEqual(stack, result.stackTrace);
        }

        [Test]
        public void NativeBoundaryPreservesInternalBlankLine()
        {
            const string body = "summary\n\n";
            var result = Split(body + "Fixture:Method ()", body.Length);
            Assert.AreEqual("summary\n", result.body);
            Assert.AreEqual("Fixture:Method ()", result.stackTrace);
        }

        [Test]
        public void EndBoundaryPreservesWholeBody()
        {
            const string text = "Summary\nVersion.2 is ready";
            var result = Split(text, text.Length);
            Assert.AreEqual(text, result.body);
            Assert.IsNull(result.stackTrace);
        }

        [TestCase(null)]
        [TestCase(-1)]
        [TestCase(999)]
        public void MissingOrInvalidOffsetUsesExistingFrameFallback(int? offset)
        {
            var result = Split("body\nFixture.Method () (at Fixture.cs:7)", offset);
            Assert.AreEqual("body", result.body);
            Assert.AreEqual("Fixture.Method () (at Fixture.cs:7)", result.stackTrace);
        }

        [TestCase("Header\nPlayer moved.\nBody continues")]
        [TestCase("Header\nNote. (continued below)\nBody continues")]
        [TestCase("Header\nat noon (bring lunch)\nBody continues")]
        [TestCase("Header\nat noon we meet\nBody continues")]
        [TestCase("Header\nUnityEngine.Debug example\nBody continues")]
        [TestCase("Header\nCall Foo.Bar() to continue.\nBody continues")]
        [TestCase("Header\nDebug.Log(\"example\");\nBody continues")]
        [TestCase("Header\n0x12345678 is a value\nBody continues")]
        [TestCase("Header\nSource (at Assets/Example.cs:7) is quoted\nBody continues")]
        public void FallbackPreservesMultilineProseAndSourceExcerpts(string text)
        {
            foreach (int? offset in new int?[] { null, -1, text.Length + 1 })
            {
                var result = Split(text, offset);
                Assert.AreEqual(text, result.body);
                Assert.IsNull(result.stackTrace);
            }
        }

        [TestCase("0x00007ffd387f224e (Unity) StackWalker::ShowCallstack")]
        [TestCase("0x12345678 (Module) Symbol")]
        [TestCase("Fixture`1[T]:Method (T) (at Assets/Fixture.cs:7)")]
        [TestCase("Namespace.Fixture.Method()")]
        [TestCase("at Namespace.Fixture.Method() in Fixture.cs:line 7")]
        public void FallbackRecognizesRealManagedAndNativeFrames(string frame)
        {
            var result = Split("Header\nPlayer moved.\n" + frame, null);
            Assert.AreEqual("Header\nPlayer moved.", result.body);
            Assert.AreEqual(frame, result.stackTrace);
        }

        [TestCase("UnityEngine.Debug:Log (object)")]
        [TestCase("Method () (at Fixture.cs:7)")]
        [TestCase("   at Fixture.Method() in Fixture.cs:line 7")]
        public void LegacyFrameFormatsRemainRecognized(string stack)
        {
            var result = Split("body\n" + stack, null);
            Assert.AreEqual("body", result.body);
            Assert.AreEqual(stack, result.stackTrace);
        }
    }
}
