using System;
using System.Runtime.InteropServices;
using Newtonsoft.Json;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Bounded launch evidence. Unavailable values remain null rather than guessed or truncated.</summary>
    public sealed class PlayScenarioEnvironment
    {
        [JsonProperty("schema_version")]
        public int SchemaVersion { get; private set; }

        [JsonProperty("operating_system", NullValueHandling = NullValueHandling.Include)]
        public string OperatingSystem { get; private set; }

        [JsonProperty("platform", NullValueHandling = NullValueHandling.Include)]
        public string Platform { get; private set; }

        [JsonProperty("architecture", NullValueHandling = NullValueHandling.Include)]
        public string Architecture { get; private set; }

        [JsonProperty("scripting_backend", NullValueHandling = NullValueHandling.Include)]
        public string ScriptingBackend { get; private set; }

        [JsonProperty("build_mode", NullValueHandling = NullValueHandling.Include)]
        public string BuildMode { get; private set; }

        [JsonProperty("runtime_version", NullValueHandling = NullValueHandling.Include)]
        public string RuntimeVersion { get; private set; }

        public static PlayScenarioEnvironment FromValues(
            string operatingSystem,
            string platform,
            string architecture,
            string scriptingBackend,
            string buildMode,
            string runtimeVersion
        )
        {
            return new PlayScenarioEnvironment
            {
                SchemaVersion = 1,
                OperatingSystem = Value(operatingSystem, 256),
                Platform = Value(platform, 64),
                Architecture = Value(architecture, 32),
                ScriptingBackend = scriptingBackend == "mono" || scriptingBackend == "il2cpp" ? scriptingBackend : null,
                BuildMode = buildMode == "editor" || buildMode == "development" || buildMode == "release" ? buildMode : null,
                RuntimeVersion = Value(runtimeVersion, 64),
            };
        }

        private static string Value(string value, int limit)
        {
            if (string.IsNullOrWhiteSpace(value) || value.Length > limit)
                return null;
            for (int index = 0; index < value.Length; index++)
            {
                if (char.IsControl(value[index]) || char.IsLowSurrogate(value[index]))
                    return null;
                if (char.IsHighSurrogate(value[index]))
                {
                    if (index + 1 >= value.Length || !char.IsLowSurrogate(value[index + 1]))
                        return null;
                    index++;
                }
            }
            return value;
        }
    }

    /// <summary>Explicit main-thread launch capture; creating or reading reports never invokes native getters.</summary>
    public sealed class PlayScenarioEnvironmentCapture
    {
        private readonly Func<PlayScenarioEnvironment> read;
        private PlayScenarioEnvironment snapshot;

        public PlayScenarioEnvironmentCapture(Func<PlayScenarioEnvironment> read = null)
        {
            this.read = read ?? ReadCurrent;
        }

        public PlayScenarioEnvironment CaptureOnce()
        {
            if (snapshot == null)
            {
                try
                {
                    snapshot = read();
                }
                catch (Exception)
                {
                    // Metadata availability cannot replace a scenario outcome or reveal exception paths.
                }
                snapshot = snapshot ?? PlayScenarioEnvironment.FromValues(null, null, null, null, null, null);
            }
            return snapshot;
        }

        private static PlayScenarioEnvironment ReadCurrent() =>
            PlayScenarioEnvironment.FromValues(
                Read(() => SystemInfo.operatingSystem),
                Read(() => Application.platform.ToString()),
                Read(() => RuntimeInformation.ProcessArchitecture.ToString().ToLowerInvariant()),
                Backend(),
                Read(BuildMode),
                Read(() => System.Environment.Version.ToString())
            );

        private static string Read(Func<string> getter)
        {
            try
            {
                return getter();
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static string Backend()
        {
#if ENABLE_MONO && !ENABLE_IL2CPP
            return "mono";
#elif ENABLE_IL2CPP && !ENABLE_MONO
            return "il2cpp";
#else
            return null;
#endif
        }

        private static string BuildMode()
        {
#if UNITY_EDITOR
            return "editor";
#else
            return Debug.isDebugBuild ? "development" : "release";
#endif
        }
    }
}
