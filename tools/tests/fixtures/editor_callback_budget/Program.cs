using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using UnityEditor;

internal static class Program
{
    private static int failures;
    private static int cases;

    private static int Main()
    {
        foreach (bool native in new[] { false, true })
        {
            Check("burst yields and preserves FIFO, native=" + native, () =>
            {
                var seen = new List<int>();
                var handles = Enumerable.Range(0, 200).Select(index => EditorMainThreadWakeup.Schedule(() => seen.Add(index))).ToArray();
                Tick(native);
                Require(seen.Count >= 1 && seen.Count <= 64, "A tick drained " + seen.Count + " callbacks.");
                handles[150].Dispose();
                for (int i = 0; i < 250 && seen.Count != 199; i++)
                    Tick(native);
                Require(seen.SequenceEqual(Enumerable.Range(0, 200).Where(index => index != 150)), "FIFO or cancellation was lost across ticks.");
            });
            Check("slow callback consumes elapsed budget, native=" + native, () =>
            {
                int seen = 0;
                EditorMainThreadWakeup.Schedule(() => Thread.Sleep(15));
                EditorMainThreadWakeup.Schedule(() => seen++);
                Tick(native);
                Require(seen == 0, "A slow callback did not yield before later work.");
                Tick(native);
                Require(seen == 1, "Remaining work was not reawakened.");
            });
        }
        Check("new and reentrant callbacks wait for next drain", () =>
        {
            int seen = 0;
            EditorMainThreadWakeup.Schedule(() =>
            {
                EditorMainThreadWakeup.Schedule(() => seen++);
                EditorApplication.Update();
            });
            EditorApplication.Update();
            Require(seen == 0, "Reentrant drain bypassed the current slice.");
            EditorApplication.Update();
            Require(seen == 1, "Next drain did not run new work.");
        });
        Check("throw and cancellation cannot starve later work", () =>
        {
            int seen = 0;
            int canceled = 0;
            IDisposable handle = null;
            EditorMainThreadWakeup.Schedule(() =>
            {
                handle.Dispose();
                throw new InvalidOperationException("expected");
            });
            handle = EditorMainThreadWakeup.Schedule(() => canceled++);
            for (int i = 0; i < 200; i++)
                EditorMainThreadWakeup.Schedule(() => seen++);
            for (int i = 0; i < 250 && seen != 200; i++)
                EditorApplication.NativeTick();
            Require(seen == 200 && canceled == 0 && UnityEngine.Debug.Exceptions == 1, "Throw lost later work or ran canceled callback.");
        });
        Check("shutdown cancels remaining slices and late ticks", () =>
        {
            int seen = 0;
            EditorMainThreadWakeup.Schedule(EditorApplication.Quit);
            for (int i = 0; i < 200; i++)
                EditorMainThreadWakeup.Schedule(() => seen++);
            for (int i = 0; i < 5; i++)
            {
                EditorApplication.NativeTick();
                EditorApplication.Update();
            }
            Require(seen == 0, "Shutdown revived deferred callbacks.");
            try
            {
                EditorMainThreadWakeup.Schedule(() => seen++);
                throw new Exception("Shutdown accepted new work.");
            }
            catch (InvalidOperationException) { }
        });
        Console.WriteLine($"RESULT: {cases - failures}/{cases} passed; failures={failures}");
        return failures == 0 ? 0 : 1;
    }

    private static void Tick(bool native)
    {
        if (native)
            EditorApplication.NativeTick();
        else
            EditorApplication.Update();
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }

    private static void Check(string name, Action run)
    {
        cases++;
        // Drain any remainder from an intentionally failing baseline scenario.
        for (int i = 0; i < 250; i++)
            EditorApplication.Update();
        UnityEngine.Debug.Exceptions = 0;
        try
        {
            run();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception error)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + error.Message);
        }
    }
}

namespace UnityEditor
{
    internal sealed class InitializeOnLoadAttribute : Attribute { }

    internal static class EditorApplication
    {
        public delegate void CallbackFunction();
        public static event CallbackFunction update;
        public static event Action quitting;
        private static event CallbackFunction tick;
        private static bool signaled;

        private static void SignalTick() => signaled = true;

        public static void QueuePlayerLoopUpdate() { }

        public static void Update() => update?.Invoke();

        public static void NativeTick()
        {
            if (signaled)
            {
                signaled = false;
                tick?.Invoke();
            }
        }

        public static void Quit() => quitting?.Invoke();
    }

    internal static class AssemblyReloadEvents
    {
        public static event Action beforeAssemblyReload;
    }
}

namespace UnityEngine
{
    internal static class Debug
    {
        internal static int Exceptions;

        public static void LogException(Exception error) => Exceptions++;
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        public static void Warn(string message) { }
    }
}
