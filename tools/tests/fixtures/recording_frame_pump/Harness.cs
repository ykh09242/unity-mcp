using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;

internal static class Harness
{
    private static int passed;
    private static int failed;
    private static int assertions;

    private static void Check(bool valid, string message)
    {
        assertions++;
        if (!valid)
            throw new InvalidOperationException(message);
    }

    private static void Run(string name, Action scenario)
    {
        try
        {
            scenario();
            passed++;
            Console.WriteLine("PASS " + name);
        }
        catch (Exception ex)
        {
            failed++;
            Console.WriteLine("FAIL " + name + ": " + ex.Message);
        }
        finally
        {
            Native.Reset();
        }
    }

    private static void SetupFailure(string stage)
    {
        var caller = new GameObject("caller-owned");
        int before = Native.Live.Count;
        int callbacks = 0;
        Native.Failure = stage;
        for (int i = 0; i < 100; i++)
        {
            Exception observed = null;
            try
            {
                RecordingFramePump.Begin(() => callbacks++, () => callbacks++);
            }
            catch (Exception ex)
            {
                observed = ex;
            }
            Check(observed != null, "factory failure must propagate");
            Check(stage == "component-null" ? observed is NullReferenceException : observed is SetupFailure, "original failure type");
        }
        int retained = Native.Live.Count - before;
        Console.WriteLine(stage + "_failed_100_ownerless_hosts=" + retained);
        Check(!caller.Destroyed, "caller-owned object preserved");
        Check(callbacks == 0, "failed factory must not call user callbacks");
        Check(retained == 0, "factory owns cleanup before returning: retained=" + retained);
        Check(Native.DestroyCalls == 100, "each failed factory host destroyed exactly once");
        Check(Native.DestroyedComponents == (stage == "component-attached-throw" ? 100 : 0), "partially attached components destroyed");
    }

    private static IEnumerator Start(RecordingFramePump pump) =>
        (IEnumerator)typeof(RecordingFramePump).GetMethod("Start", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(pump, null);

    public static int Main(string[] arguments)
    {
        Run("hide-flags setup failure", () => SetupFailure("hide-flags"));
        Run("persistence setup failure", () => SetupFailure("persist"));
        Run("component creation exception", () => SetupFailure("component-throw"));
        Run("component creation null", () => SetupFailure("component-null"));
        Run("exception after component attachment", () => SetupFailure("component-attached-throw"));
        Run(
            "edit-mode preflight does not allocate",
            () =>
            {
                Application.isPlaying = false;
                for (int i = 0; i < 100; i++)
                {
                    try
                    {
                        RecordingFramePump.Begin(null, null);
                        throw new Exception("missing preflight error");
                    }
                    catch (InvalidOperationException) { }
                }
                Check(Native.Created == 0 && Native.Live.Count == 0, "preflight allocation");
            }
        );
        Run(
            "successful factory transfers ownership and cancel is idempotent",
            () =>
            {
                int callbacks = 0;
                for (int i = 0; i < 100; i++)
                {
                    var pump = RecordingFramePump.Begin(() => callbacks++, () => callbacks++);
                    Check(Native.Live.Count == 1 && !pump.Destroyed, "successful transfer");
                    pump.Cancel();
                    pump.Cancel();
                    Check(Native.Live.Count == 0, "cancel releases host");
                }
                Check(Native.DestroyCalls == 100 && callbacks == 0, "cancel destruction and callback contract");
            }
        );
        Run(
            "external destruction notifies once and clears callback owners",
            () =>
            {
                int calls = 0;
                var pump = RecordingFramePump.Begin(() => calls += 100, () => calls++);
                UnityEngine.Object.DestroyImmediate(pump.gameObject);
                UnityEngine.Object.DestroyImmediate(pump.gameObject);
                Check(calls == 1 && Native.Live.Count == 0, "external destruction callback once");
                foreach (var name in new[] { "_onFrame", "_onDestroyed" })
                    Check(
                        typeof(RecordingFramePump).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(pump) == null,
                        "callback capture cleared"
                    );
            }
        );
        Run(
            "cancellation after end-of-frame yield does not invoke stale callbacks",
            () =>
            {
                int calls = 0;
                var pump = RecordingFramePump.Begin(() => calls++, () => calls++);
                var frames = Start(pump);
                Check(frames.MoveNext() && frames.Current is WaitForEndOfFrame, "initial frame yield");
                pump.Cancel();
                Check(!frames.MoveNext() && calls == 0, "cancelled coroutine cannot invoke old callback");
            }
        );
        Console.WriteLine("RESULT passed=" + passed + " failed=" + failed + " assertions=" + assertions);
        if (Array.IndexOf(arguments, "--baseline") >= 0)
        {
            Console.WriteLine("Baseline expects the five cleanup regressions to fail; this is reproduction, not a passing product verdict.");
            return passed == 4 && failed == 5 ? 0 : 1;
        }
        return failed == 0 ? 0 : 1;
    }
}

internal sealed class SetupFailure : Exception
{
    public SetupFailure(string stage)
        : base(stage) { }
}

internal static class Native
{
    internal static readonly List<GameObject> Live = new List<GameObject>();
    internal static string Failure;
    internal static int Created;
    internal static int DestroyCalls;
    internal static int DestroyedComponents;

    internal static void Reset()
    {
        Failure = null;
        foreach (var go in Live.ToArray())
            UnityEngine.Object.DestroyImmediate(go);
        Live.Clear();
        Created = 0;
        DestroyCalls = 0;
        DestroyedComponents = 0;
        Application.isPlaying = true;
    }
}

namespace UnityEngine
{
    public enum HideFlags
    {
        HideAndDontSave,
    }

    public static class Application
    {
        public static bool isPlaying = true;
    }

    public class Object
    {
        public bool Destroyed;
        private HideFlags flags;
        public HideFlags hideFlags
        {
            get => flags;
            set
            {
                if (Native.Failure == "hide-flags")
                    throw new SetupFailure("hide-flags");
                flags = value;
            }
        }

        public static void DontDestroyOnLoad(Object value)
        {
            if (Native.Failure == "persist")
                throw new SetupFailure("persist");
        }

        public static void DestroyImmediate(Object value)
        {
            if (ReferenceEquals(value, null) || value.Destroyed)
                return;
            value.Destroyed = true;
            var go = value as GameObject;
            if (ReferenceEquals(go, null))
                return;
            Native.Live.Remove(go);
            Native.DestroyCalls++;
            if (go.Component != null)
            {
                go.Component.Destroyed = true;
                Native.DestroyedComponents++;
                go.Component.GetType().GetMethod("OnDestroy", BindingFlags.Instance | BindingFlags.NonPublic)?.Invoke(go.Component, null);
            }
        }

        public static bool operator ==(Object left, Object right)
        {
            bool leftNull = ReferenceEquals(left, null) || left.Destroyed;
            bool rightNull = ReferenceEquals(right, null) || right.Destroyed;
            return leftNull && rightNull || !leftNull && !rightNull && ReferenceEquals(left, right);
        }

        public static bool operator !=(Object left, Object right) => !(left == right);

        public override bool Equals(object value) => ReferenceEquals(this, value);

        public override int GetHashCode() => base.GetHashCode();
    }

    public class GameObject : Object
    {
        internal MonoBehaviour Component;

        public GameObject(string name)
        {
            Native.Live.Add(this);
            Native.Created++;
        }

        public T AddComponent<T>()
            where T : MonoBehaviour
        {
            if (Native.Failure == "component-throw")
                throw new SetupFailure("component-throw");
            if (Native.Failure == "component-null")
                return null;
            var component = (T)Activator.CreateInstance(typeof(T), true);
            component.gameObject = this;
            Component = component;
            if (Native.Failure == "component-attached-throw")
                throw new SetupFailure("component-attached-throw");
            return component;
        }
    }

    public class MonoBehaviour : Object
    {
        public GameObject gameObject;

        public void StopAllCoroutines() { }
    }

    public class WaitForEndOfFrame { }
}
