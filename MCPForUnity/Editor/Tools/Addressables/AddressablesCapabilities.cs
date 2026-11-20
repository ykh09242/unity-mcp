using System;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Addressables
{
    internal static class AddressablesCapabilities
    {
        private static readonly string[] Actions =
        {
            "inspect",
            "preflight",
            "analyze_layout",
            "set_address",
            "move_entry",
            "set_profile_value",
            "preview_prefab",
            "create_prefab",
            "register_prefab",
            "prefab_status",
            "cancel_prefab",
            "reconcile_prefab",
            "replan_prefab",
        };

        internal static object Report(JObject request)
        {
            if (request.Count != 1)
                AddressablesState.Fail("invalid_addressables_request", "capabilities takes only the action.");
            Type settings = null;
            string version = null;
            AddressablesException unavailable = null;
            try
            {
                AddressablesState.RequirePackage(out settings, out version);
            }
            catch (AddressablesException error)
            {
                unavailable = error;
            }
            var operations = new JObject { ["capabilities"] = new JObject { ["available"] = true } };
            foreach (string action in Actions)
            {
                try
                {
                    // Cancelling a pending checkpoint needs only the Editor session store.
                    if (action != "cancel_prefab")
                    {
                        if (unavailable != null)
                            throw unavailable;
                        Require(settings, action);
                    }
                    operations[action] = new JObject { ["available"] = true };
                }
                catch (AddressablesException error)
                {
                    operations[action] = new JObject
                    {
                        ["available"] = false,
                        ["code"] = error.Code,
                        ["reason"] = error.Message,
                    };
                }
                catch (AmbiguousMatchException)
                {
                    operations[action] = new JObject
                    {
                        ["available"] = false,
                        ["code"] = "addressables_api_unsupported",
                        ["reason"] = "Ambiguous provider member for " + action + ".",
                    };
                }
            }
            return new SuccessResponse(
                "Observed Addressables API capabilities",
                new
                {
                    package_version = version,
                    scope = "provider_api",
                    operations,
                }
            );
        }

        internal static void Require(Type settings, string action)
        {
            if (action == "analyze_layout")
            {
                Type layout = settings.Assembly.GetType("UnityEditor.AddressableAssets.Build.Layout.BuildLayout");
                AddressablesLayout.RequireSchema(layout);
                AddressablesLayout.UsesContentDirectories(layout);
                return;
            }
            AddressablesCompatibility.RequireSnapshot(settings, action);
            const string ns = "UnityEditor.AddressableAssets.Settings.";
            Writer(
                action,
                settings,
                settings.Assembly.GetType(ns + "AddressableAssetEntry"),
                settings.Assembly.GetType(ns + "AddressableAssetGroup"),
                settings.Assembly.GetType(ns + "AddressableAssetProfileSettings")
            );
            if (action == "move_entry" || action == "set_address" || action == "set_profile_value")
                AddressablesLookupCache.BindReset(settings, action);
        }

        internal static MethodInfo Writer(string action, Type settings, Type entry, Type group, Type profiles)
        {
            if (action == "register_prefab" || action == "move_entry")
                return AddressablesCompatibility.RequireMethod(settings, action, "CreateOrMoveEntry", entry, typeof(string), group, typeof(bool), typeof(bool));
            if (action == "set_address")
                return AddressablesCompatibility.RequireMethod(entry, action, "SetAddress", typeof(void), typeof(string), typeof(bool));
            if (action == "set_profile_value")
                return AddressablesCompatibility.RequireMethod(profiles, action, "SetValue", typeof(void), typeof(string), typeof(string), typeof(string));
            return null;
        }
    }
}
