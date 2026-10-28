using System;
using MCPForUnity.Editor.Services;
using MCPForUnityTests.EditMode.Tools;
using Newtonsoft.Json.Linq;

internal static class ConsentHarness
{
    private static int Main()
    {
        int failures = 0;
        foreach (string action in new[] { "deploy_package", "restore_package" })
        {
            var test = new ManageEditorDeploymentConsentTests();
            test.SetUp();
            try
            {
                test.MissingRevokedDisabledOrUnconfirmedConsentNeverEntersDeployment(action);
                Console.WriteLine("PASS " + action + " refuses before deployment service entry");
            }
            catch (Exception error)
            {
                failures++;
                Console.WriteLine("FAIL " + action + " refusal: " + error);
            }
            finally
            {
                test.TearDown();
            }
            test.SetUp();
            try
            {
                test.LiveGrantedConsentAllowsOneCallAndRevocationBlocksTheNext(
                    action,
                    action == "deploy_package" ? ToolConsentScope.Project : ToolConsentScope.EditorSession
                );
                Console.WriteLine("PASS " + action + " live grant then revoke");
            }
            catch (Exception error)
            {
                failures++;
                Console.WriteLine("FAIL " + action + " grant/revoke: " + error);
            }
            finally
            {
                test.TearDown();
            }
        }
        Console.WriteLine("Managed consent cases: " + (4 - failures) + "/4");
        return failures == 0 ? 0 : 1;
    }
}

// Mono resolves externs while JIT-compiling the whole dispatch method, even for
// unused branches. Throwing stubs make accidental entry into those branches fail.
namespace UnityEditor
{
    internal static class EditorApplication
    {
        internal static bool isPlaying
        {
            get => throw new InvalidOperationException("Unused native boundary");
            set => throw new InvalidOperationException("Unused native boundary");
        }
        internal static bool isPlayingOrWillChangePlaymode => throw new InvalidOperationException("Unused native boundary");
        internal static bool isPaused
        {
            get => throw new InvalidOperationException("Unused native boundary");
            set => throw new InvalidOperationException("Unused native boundary");
        }
    }

    internal static class Undo
    {
        internal static string GetCurrentGroupName() => throw new InvalidOperationException("Unused native boundary");

        internal static void PerformUndo() => throw new InvalidOperationException("Unused native boundary");

        internal static void PerformRedo() => throw new InvalidOperationException("Unused native boundary");
    }
}

// Only service/unused native boundaries are replaced. The dispatched ManageEditor,
// consent service/status, parameter parsing, response types and NUnit tests are real sources.
namespace MCPForUnity.Editor.Services
{
    public static class MCPServiceLocator
    {
        private static IToolConsentService _toolConsentService;
        private static IPackageDeploymentService _packageDeploymentService;
        public static IToolConsentService ToolConsent => _toolConsentService ?? throw new InvalidOperationException("Missing fixture consent service");
        public static IPackageDeploymentService Deployment =>
            _packageDeploymentService ?? throw new InvalidOperationException("Missing fixture deployment service");

        public static void Register<T>(T service)
            where T : class
        {
            if (service is IToolConsentService consent)
                _toolConsentService = consent;
            else if (service is IPackageDeploymentService deployment)
                _packageDeploymentService = deployment;
            else
                throw new InvalidOperationException("Unexpected service");
        }
    }

    internal static class EditorStateCache
    {
        internal static bool IsPlayModeTransition => throw new InvalidOperationException("Unused native boundary");
    }

    internal static class PlayModeReadiness
    {
        internal static object Start(JObject p) => throw new InvalidOperationException("Unused native boundary");

        internal static object Get(JObject p, bool cancel) => throw new InvalidOperationException("Unused native boundary");

        internal static void Cancel(string reason) => throw new InvalidOperationException("Unused native boundary");
    }

    internal static class GameViewSizeControl
    {
        internal static object Execute(string action, JObject p) => throw new InvalidOperationException("Unused native boundary");
    }
}

namespace MCPForUnity.Editor.Helpers
{
    [AttributeUsage(AttributeTargets.Class)]
    internal sealed class McpForUnityToolAttribute : Attribute
    {
        public bool AutoRegister { get; set; }

        public McpForUnityToolAttribute(string name) { }
    }

    internal static class ProjectIdentityUtility
    {
        internal static bool TryGetConsentProjectRoot(out string root) => throw new InvalidOperationException("Unused native boundary");
    }

    internal static class McpLog
    {
        internal static void Error(string message) => throw new InvalidOperationException("Unused native boundary");
    }
}

namespace MCPForUnity.Runtime.Helpers
{
    internal static class SafePathUtility
    {
        internal static string ResolveWithinRoot(string root, string path) => throw new InvalidOperationException("Unused native boundary");
    }
}
