using System;
using System.Reflection;
using System.Runtime.ExceptionServices;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Build
{
    // Bind only to the Editor assembly that owns BuildPipeline. User scripts can
    // declare types with the same names as an optional Unity API.
    internal sealed class BuildProfileApi
    {
        private static readonly Assembly EditorAssembly = typeof(BuildPipeline).Assembly;
        internal static BuildProfileApi Installed { get; set; } =
            new BuildProfileApi(
                EditorAssembly.GetType("UnityEditor.Build.Profile.BuildProfile"),
                EditorAssembly.GetType("UnityEditor.BuildPlayerWithProfileOptions")
            );

        private const BindingFlags PublicStatic = BindingFlags.Public | BindingFlags.Static;
        private readonly Type profileType;
        private readonly Type optionsType;
        private readonly MethodInfo activeGetter;
        private readonly MethodInfo activeSetter;
        private readonly PropertyInfo scenes;
        private readonly PropertyInfo overrides;
        private readonly PropertyInfo globalScenes;
        private readonly PropertyInfo buildProfile;
        private readonly PropertyInfo location;
        private readonly PropertyInfo manifest;
        private readonly PropertyInfo buildOptions;
        private readonly MethodInfo buildPlayer;

        internal BuildProfileApi(Type profile, Type options)
        {
            if (profile == null || !typeof(ScriptableObject).IsAssignableFrom(profile))
                return;
            profileType = profile;
            activeGetter = profile.GetMethod("GetActiveBuildProfile", PublicStatic, null, Type.EmptyTypes, null);
            activeSetter = profile.GetMethod("SetActiveBuildProfile", PublicStatic, null, new[] { profile }, null);
            scenes = Property(profile, "scenes", typeof(EditorBuildSettingsScene[]));
            overrides = Property(profile, "overrideGlobalScenes", typeof(bool));
            globalScenes = typeof(EditorBuildSettings).GetProperty(
                "globalScenes",
                PublicStatic,
                null,
                typeof(EditorBuildSettingsScene[]),
                Type.EmptyTypes,
                null
            );
            if (options == null || !options.IsValueType)
                return;
            optionsType = options;
            buildProfile = Property(options, "buildProfile", profile);
            location = Property(options, "locationPathName", typeof(string));
            manifest = Property(options, "assetBundleManifestPath", typeof(string));
            buildOptions = Property(options, "options", typeof(BuildOptions));
            buildPlayer = typeof(BuildPipeline).GetMethod("BuildPlayer", PublicStatic, null, new[] { options }, null);
        }

        private static PropertyInfo Property(Type type, string name, Type result) =>
            type.GetProperty(name, BindingFlags.Public | BindingFlags.Instance, null, result, Type.EmptyTypes, null);

        private static void Require(bool available, string capability)
        {
            if (!available)
                throw new InvalidOperationException($"Build Profile API unavailable: {capability} in Unity {Application.unityVersion}.");
        }

        internal Object Load(string path)
        {
            Require(profileType != null, "BuildProfile");
            return AssetDatabase.LoadAssetAtPath(path, profileType);
        }

        internal Object GetActive()
        {
            Require(profileType != null && activeGetter?.ReturnType == profileType, "BuildProfile.GetActiveBuildProfile");
            return (Object)Invoke(activeGetter, null);
        }

        internal void SetActive(Object profile)
        {
            Require(activeSetter?.ReturnType == typeof(void), "BuildProfile.SetActiveBuildProfile");
            Invoke(activeSetter, new object[] { profile });
        }

        internal EditorBuildSettingsScene[] Scenes(Object profile)
        {
            Require(scenes?.GetGetMethod() != null, "BuildProfile.scenes");
            return (EditorBuildSettingsScene[])scenes.GetValue(profile);
        }

        internal void RequireBuild(string manifestPath = null)
        {
            Require(
                buildPlayer?.ReturnType == typeof(BuildReport)
                    && buildProfile?.GetSetMethod() != null
                    && location?.GetSetMethod() != null
                    && buildOptions?.GetSetMethod() != null
                    && overrides?.GetGetMethod() != null,
                "BuildPipeline.BuildPlayer(BuildPlayerWithProfileOptions) and profile scene settings"
            );
            if (manifestPath != null)
                Require(manifest?.GetSetMethod() != null, "BuildPlayerWithProfileOptions.assetBundleManifestPath");
        }

        internal EditorBuildSettingsScene[] EffectiveScenes(Object profile, out bool overridesGlobal)
        {
            overridesGlobal = (bool)overrides.GetValue(profile);
            if (overridesGlobal)
                return Scenes(profile);
            Require(globalScenes?.GetGetMethod() != null, "EditorBuildSettings.globalScenes");
            return (EditorBuildSettingsScene[])globalScenes.GetValue(null);
        }

        internal object CreateOptions(Object profile, BuildPlayerOptions options)
        {
            RequireBuild(options.assetBundleManifestPath);
            object boxed = Activator.CreateInstance(optionsType);
            buildProfile.SetValue(boxed, profile);
            location.SetValue(boxed, options.locationPathName);
            if (options.assetBundleManifestPath != null)
                manifest.SetValue(boxed, options.assetBundleManifestPath);
            buildOptions.SetValue(boxed, options.options);
            return boxed;
        }

        internal BuildReport Build(object options) => (BuildReport)Invoke(buildPlayer, new[] { options });

        private static object Invoke(MethodInfo method, object[] args)
        {
            try
            {
                return method.Invoke(null, args);
            }
            catch (TargetInvocationException ex) when (ex.InnerException != null)
            {
                ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
                throw;
            }
        }
    }
}
