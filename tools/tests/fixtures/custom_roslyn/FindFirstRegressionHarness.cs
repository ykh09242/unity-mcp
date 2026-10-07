using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
using Object = UnityEngine.Object;

internal static class FindFirstRegressionHarness
{
    private static int checks;
    private static int failures;

    private static int Main()
    {
        Check("unrelated lookups work before ordered lookup", CheckUnordered);
#if MISSING_ORDERED_API
        Check(
            "missing exact ordered overload fails loudly on each call",
            () =>
            {
                for (int i = 0; i < 2; i++)
                {
                    try
                    {
                        UnityFindObjectsCompat.FindFirst<Sample>(true);
                        throw new Exception("Missing API returned normally");
                    }
                    catch (MissingMethodException) { }
                }
            }
        );
#else
        Check("no match retains null", () => Require(UnityFindObjectsCompat.FindFirst<Other>() == null, "No-match lookup changed"));
        Check(
            "multiple active objects retain Unity's exact ordered identity",
            () =>
            {
                Require(UnityFindObjectsCompat.FindFirst<Sample>() == Object.Scene[2], "Used enumeration or any-object order");
                Require(Object.LastType == typeof(Sample) && !Object.LastInactive, "Wrong Type or default inactive flag");
            }
        );
        Check(
            "including inactive retains ordered inactive identity",
            () =>
            {
                Require(UnityFindObjectsCompat.FindFirst<Sample>(true) == Object.Scene[1], "Inactive first object was excluded");
                Require(Object.LastType == typeof(Sample) && Object.LastInactive, "Inactive flag was not forwarded");
            }
        );
        Check(
            "inactive flag roundtrip does not stick in cached lookup",
            () =>
            {
                UnityFindObjectsCompat.FindFirst<Sample>(true);
                Require(UnityFindObjectsCompat.FindFirst<Sample>(false) == Object.Scene[2] && !Object.LastInactive, "Cached flag leaked");
            }
        );
        Check(
            "requested runtime Type changes across cached calls",
            () =>
            {
                Require(UnityFindObjectsCompat.FindFirst<Derived>() == Object.Scene[0] && Object.LastType == typeof(Derived), "Cached Type leaked");
                Require(UnityFindObjectsCompat.FindFirst<Other>() == null && Object.LastType == typeof(Other), "No-match Type changed");
            }
        );
        Check(
            "ordered invocation failure is observable",
            () =>
            {
                Object.ThrowOnOrderedLookup = true;
                try
                {
                    UnityFindObjectsCompat.FindFirst<Sample>();
                    throw new Exception("Underlying failure returned normally");
                }
                catch (InvalidOperationException ex)
                {
                    Require(ex.Message == "ordered-probe", "Wrong direct failure");
                }
                catch (TargetInvocationException ex)
                {
                    Require(ex.InnerException is InvalidOperationException && ex.InnerException.Message == "ordered-probe", "Underlying failure lost");
                }
                finally
                {
                    Object.ThrowOnOrderedLookup = false;
                }
            }
        );
#endif
#if UNITY_6000_5_OR_NEWER || !UNITY_2022_3_OR_NEWER
        Check(
            "reflection resolves exact non-generic signature once",
            () =>
            {
                var field = typeof(UnityFindObjectsCompat).GetField("_findFirst", BindingFlags.NonPublic | BindingFlags.Static);
                var method = (MethodInfo)field.GetValue(null);
#if MISSING_ORDERED_API
                Require(method == null, "Accepted distractor overload");
#else
                Require(method != null && !method.IsGenericMethod && method.GetParameters()[0].ParameterType == typeof(Type), "Wrong ordered overload");
#if UNITY_6000_5_OR_NEWER
                Require(
                    method.Name == "FindFirstObjectByType" && method.GetParameters()[1].ParameterType == typeof(FindObjectsInactive),
                    "Wrong modern signature"
                );
#else
                Require(method.Name == "FindObjectOfType" && method.GetParameters()[1].ParameterType == typeof(bool), "Wrong legacy signature");
#endif
                UnityFindObjectsCompat.FindFirst<Sample>();
                Require(ReferenceEquals(method, field.GetValue(null)), "Reflection result was not cached");
#endif
                Require(
                    (bool)typeof(UnityFindObjectsCompat).GetField("_findFirstProbed", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null),
                    "Probe was not cached"
                );
            }
        );
#endif
        Check("unrelated lookups work after ordered lookup or failure", CheckUnordered);
        Console.WriteLine("RESULT: " + (checks - failures) + "/" + checks + " passed; failures=" + failures);
        return failures == 0 ? 0 : 1;
    }

    private static void CheckUnordered()
    {
        Require(UnityFindObjectsCompat.FindAll<Sample>().Length == 2, "FindAll changed");
        Require(UnityFindObjectsCompat.FindAll(typeof(Sample), true).Length == 3, "FindAll inactive changed");
        Require(UnityFindObjectsCompat.FindAny(typeof(Sample)) == Object.Scene[0], "FindAny changed");
    }

    private static void Check(string name, Action check)
    {
        checks++;
        Object.Scene.Clear();
        Object.Scene.Add(new Derived { Order = (1L << 34) + 2, Active = true });
        Object.Scene.Add(new Sample { Order = 1, Active = false });
        Object.Scene.Add(new Sample { Order = (1L << 34) + 1, Active = true });
        try
        {
            check();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception ex)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + ex);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }

    public class Sample : Object { }

    public sealed class Derived : Sample { }

    public sealed class Other : Object { }
}

// Engine API seam only. The ordered behavior under test comes from the production shim.
namespace UnityEngine
{
    public enum FindObjectsInactive
    {
        Exclude,
        Include,
    }

    public enum FindObjectsSortMode
    {
        None,
    }

    public class Object
    {
        public long Order;
        public bool Active;
        public static readonly List<Object> Scene = new List<Object>();
        public static Type LastType;
        public static bool LastInactive;
        public static bool ThrowOnOrderedLookup;

        private static Object[] Select(Type type, bool inactive) => Scene.Where(value => type.IsInstanceOfType(value) && (value.Active || inactive)).ToArray();

        private static Object Ordered(Type type, bool inactive)
        {
            LastType = type;
            LastInactive = inactive;
            if (ThrowOnOrderedLookup)
                throw new InvalidOperationException("ordered-probe");
            return Select(type, inactive).OrderBy(value => value.Order).FirstOrDefault();
        }
#if UNITY_2022_3_OR_NEWER
#if !MISSING_ORDERED_API
#if UNITY_6000_5_OR_NEWER
        [Obsolete("Ordered API is deprecated", true)]
#endif
        public static Object FindFirstObjectByType(Type type, FindObjectsInactive inactive) => Ordered(type, inactive == FindObjectsInactive.Include);
#endif

        public static Object FindFirstObjectByType(Type type) => throw new Exception("Wrong overload");

        public static Object FindAnyObjectByType(Type type) => Select(type, false).FirstOrDefault();

        public static T[] FindObjectsByType<T>()
            where T : Object => Select(typeof(T), false).Cast<T>().ToArray();

        public static T[] FindObjectsByType<T>(FindObjectsSortMode mode)
            where T : Object => FindObjectsByType<T>();

        public static Object[] FindObjectsByType(Type type, FindObjectsSortMode mode) => Select(type, false);

        public static Object[] FindObjectsByType(Type type, FindObjectsInactive inactive) => Select(type, inactive == FindObjectsInactive.Include);

        public static Object[] FindObjectsByType(Type type, FindObjectsInactive inactive, FindObjectsSortMode mode) =>
            Select(type, inactive == FindObjectsInactive.Include);
#else
        [Obsolete("Legacy API must use reflection", true)]
        public static Object[] FindObjectsOfType(Type type) => Select(type, false);

        [Obsolete("Legacy API must use reflection", true)]
        public static Object[] FindObjectsOfType(Type type, bool inactive) => Select(type, inactive);

        [Obsolete("Legacy API must use reflection", true)]
        public static Object FindObjectOfType(Type type) => Select(type, false).FirstOrDefault();

#if !MISSING_ORDERED_API
        [Obsolete("Legacy API must use reflection", true)]
        public static Object FindObjectOfType(Type type, bool inactive) => Ordered(type, inactive);
#endif
#endif
    }
}
