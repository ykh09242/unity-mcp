using System;
using System.Reflection;
using UnityEngine;
using UnityEngine.Scripting;

[assembly: AlwaysLinkAssembly]

namespace ManagedStrippingFixture
{
    public static class ReflectionProbe
    {
        [Serializable]
        public sealed class Observation
        {
            public string nonce;
            public string unityVersion;
            public string platform;
            public string backend;
            public string buildGuid;
            public string typeName;
            public string methodName;
            public string state;
            public string value;
        }

        public static string Argument(string flag)
        {
            string[] arguments = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(arguments, flag);
            if (index < 0 || index + 1 == arguments.Length)
                throw new ArgumentException("Missing fixture argument " + flag);
            return arguments[index + 1];
        }

        public static Observation Observe()
        {
#if UNITY_EDITOR
            const string backend = "Editor";
#elif ENABLE_IL2CPP
            const string backend = "IL2CPP";
#else
            const string backend = "Other";
#endif
            var observation = new Observation
            {
                nonce = Argument("--strip-nonce"),
                unityVersion = Application.unityVersion,
                platform = Application.platform.ToString(),
                backend = backend,
                buildGuid = Application.buildGUID,
                typeName = Argument("--strip-type"),
                methodName = Argument("--strip-method"),
                state = "missing_type",
                value = "",
            };
            // Both lookup names come from the launching process, beyond what the linker can see.
            Type type = Assembly.GetExecutingAssembly().GetType(observation.typeName, throwOnError: false);
            if (type == null)
                return observation;
            MethodInfo method = type.GetMethod(observation.methodName, BindingFlags.Public | BindingFlags.Static, null, Type.EmptyTypes, null);
            if (method == null)
            {
                observation.state = "missing_method";
                return observation;
            }
            observation.value = (string)method.Invoke(null, null);
            observation.state = "invoked";
            return observation;
        }

        public static void Log(Observation observation) => Debug.Log("MCP_MANAGED_STRIPPING " + JsonUtility.ToJson(observation));

#if !UNITY_EDITOR
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void RunPlayer()
        {
            try
            {
                Observation observation = Observe();
                Log(observation);
                Application.Quit(
                    observation.state switch
                    {
                        "invoked" => 0,
                        "missing_type" => 42,
                        _ => 43,
                    }
                );
            }
            catch (Exception error)
            {
                Debug.LogException(error);
                Application.Quit(44);
            }
        }
#endif
    }
}
