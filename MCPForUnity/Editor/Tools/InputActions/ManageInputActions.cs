using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.InputActions
{
    [McpForUnityTool("manage_input_actions", AutoRegister = false, Group = "core")]
    public static class ManageInputActions
    {
        internal static Type AssetType => Type.GetType("UnityEngine.InputSystem.InputActionAsset, Unity.InputSystem");

        public static object HandleCommand(JObject p)
        {
            try
            {
                if (p == null || Encoding.UTF8.GetByteCount(p.ToString(Newtonsoft.Json.Formatting.None)) > 8192)
                    throw new ArgumentException("InputAction request must be a bounded object (8 KiB).");
                string action = Text(p, "action", true, 32);
                if (action == "capabilities")
                {
                    if (p.Properties().Any(property => property.Name != "action"))
                        throw new ArgumentException("capabilities accepts only action.");
                    return new SuccessResponse("InputAction authoring capabilities.", Capabilities());
                }
                if (!Available(out _))
                    return new ErrorResponse("input_system_unavailable", new { required_package = "com.unity.inputsystem", minimum_version = "1.7.0" });
                string path = AssetPathUtility.GetContainedAssetPath(Text(p, "path", true, 2048));
                if (!path.EndsWith(".inputactions", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("path must be an Assets .inputactions source file.");
                if (action == "read")
                {
                    Keys(p, "page_size", "cursor", "expected_revision");
                    return new SuccessResponse("InputAction source and exact identities.", Read(path, p));
                }
                if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                    return new ErrorResponse("editor_busy", new { detail = "Author InputAction assets in idle Edit Mode." });
                if (action == "create_asset")
                {
                    Keys(p);
                    return Create(path);
                }
                if (action == "edit")
                {
                    Keys(p, "expected_revision", "commands");
                    return EditCommands(path, p);
                }
                CommandKeys(p, action, true);
                byte[] before = InputActionDocument.ReadBytes(path);
                if (Text(p, "expected_revision", true, 64) != InputActionDocument.Digest(before))
                    return new ErrorResponse("stale_revision");
                JObject document = InputActionDocument.Parse(before);
                InputActionDocument.Validate(document);
                string created = InputActionDocument.Edit(document, action, p);
                byte[] after = InputActionDocument.Encode(document, before);
                InputActionDocument.ValidateNative(after);
                return Save(path, before, after, new JArray(), false, created);
            }
            catch (TargetInvocationException ex)
            {
                return new ErrorResponse("input_action_api_error", new { detail = ex.InnerException?.Message ?? ex.Message });
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse("invalid_input_action_request", new { detail = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse("input_action_conflict", new { detail = ex.Message });
            }
            catch (IOException ex)
            {
                return new ErrorResponse("input_action_io_error", new { detail = ex.Message, source_state = "inspect_before_retry" });
            }
            catch (UnauthorizedAccessException ex)
            {
                return new ErrorResponse("input_action_io_error", new { detail = ex.Message, source_state = "inspect_before_retry" });
            }
            catch (Newtonsoft.Json.JsonException ex)
            {
                return new ErrorResponse("invalid_input_action_json", new { detail = ex.Message });
            }
        }

        private static object Create(string path)
        {
            EnsureCreationPath(path);
            JObject document = EmptyDocument(path);
            byte[] bytes = InputActionDocument.Encode(document, Array.Empty<byte>());
            InputActionDocument.ValidateNative(bytes);
            return Save(path, Array.Empty<byte>(), bytes, new JArray(), true);
        }

        private static JObject EmptyDocument(string path) =>
            new JObject
            {
                ["name"] = Path.GetFileNameWithoutExtension(path),
                ["maps"] = new JArray(),
                ["controlSchemes"] = new JArray(),
            };

        private static void EnsureCreationPath(string path)
        {
            string full = AssetPathUtility.GetFullAssetPath(path);
            if (!Directory.Exists(Path.GetDirectoryName(full)) || File.Exists(full) || File.Exists(full + ".meta"))
                throw new InvalidOperationException("Creation requires an existing parent and an unoccupied source/meta path.");
        }

        private static object EditCommands(string path, JObject p)
        {
            if (!(p["commands"] is JArray commands) || commands.Count < 1 || commands.Count > 64)
                throw new ArgumentException("commands must contain 1..64 ordered objects.");
            bool creation = commands[0] is JObject first && first["action"]?.Type == JTokenType.String && (string)first["action"] == "create_asset";
            byte[] before;
            JObject document;
            if (creation)
            {
                if (p["expected_revision"] != null)
                    throw new ArgumentException("New assets cannot specify expected_revision.");
                EnsureCreationPath(path);
                before = Array.Empty<byte>();
                document = EmptyDocument(path);
            }
            else
            {
                before = InputActionDocument.ReadBytes(path);
                if (Text(p, "expected_revision", true, 64) != InputActionDocument.Digest(before))
                    return new ErrorResponse("stale_revision");
                document = InputActionDocument.Parse(before);
                InputActionDocument.Validate(document);
            }
            JArray created = InputActionDocument.EditCommands(document, commands);
            byte[] after = InputActionDocument.Encode(document, before);
            InputActionDocument.ValidateNative(after);
            return Save(path, before, after, created, creation);
        }

        private static object Save(string path, byte[] before, byte[] after, JArray created, bool creation, string createdId = null)
        {
            string guid = creation ? null : AssetDatabase.AssetPathToGUID(path);
            if (!creation && (string.IsNullOrEmpty(guid) || AssetDatabase.LoadAssetAtPath(path, AssetType) == null))
                throw new InvalidOperationException("The source must already be imported as an InputActionAsset.");
            bool changed = creation || !before.SequenceEqual(after);
            bool saved = !changed;
            string importError = null;
            try
            {
                if (creation)
                {
                    EnsureCreationPath(path);
                    using (var stream = File.Open(AssetPathUtility.GetFullAssetPath(path), FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        stream.Write(after, 0, after.Length);
                        stream.Flush(true);
                    }
                    saved = true;
                    AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    guid = AssetDatabase.AssetPathToGUID(path);
                    if (string.IsNullOrEmpty(guid) || AssetDatabase.LoadAssetAtPath(path, AssetType) == null)
                        throw new InvalidOperationException("Native import did not produce an InputActionAsset.");
                }
                else if (changed)
                    InputActionFileUndo.ApplyObserved(path, guid, before, after, afterWrite: () => saved = true);
            }
            catch (Exception ex) when (saved)
            {
                importError = ex.GetBaseException().Message;
            }
            string savedRevision = InputActionDocument.Digest(after);
            string revision = null;
            string observationError = null;
            bool identityMatches = false;
            try
            {
                revision = InputActionDocument.Digest(InputActionDocument.ReadBytes(path));
                identityMatches = !string.IsNullOrEmpty(guid) && AssetDatabase.AssetPathToGUID(path) == guid;
            }
            catch (Exception ex)
            {
                observationError = ex.GetBaseException().Message;
            }
            bool stale = revision != null && (revision != savedRevision || (!identityMatches && importError == null));
            var findings = new JArray();
            if (importError != null)
                findings.Add(importError.Substring(0, Math.Min(importError.Length, 1024)));
            if (observationError != null)
                findings.Add(observationError.Substring(0, Math.Min(observationError.Length, 1024)));
            if (stale)
                findings.Add("Source or asset identity changed after saving; created identities belong to the saved candidate.");
            JObject verification = new JObject
            {
                ["status"] = findings.Count > 0 ? "issues_found" : "passed",
                ["scope"] = "final_candidate_native_validation_and_exact_source_import_readback",
                ["observed_values"] = new JObject
                {
                    ["revision"] = revision,
                    ["asset_guid"] = guid,
                    ["identity_matches"] = identityMatches,
                },
                ["findings"] = findings,
                ["complete"] = importError == null && observationError == null && !stale,
                ["further_fixes_required"] = findings.Count > 0,
            };
            if (stale)
                verification["status"] = "stale";
            else if (importError != null || observationError != null)
            {
                verification["status"] = "unavailable";
                verification["further_fixes_required"] = JValue.CreateNull();
            }
            var data = new JObject
            {
                ["path"] = path,
                ["asset_guid"] = guid,
                ["revision"] = revision,
                ["saved_revision"] = savedRevision,
                ["created_id"] = createdId,
                ["created_identities"] = created,
                ["changed"] = changed,
                ["saved"] = saved,
                ["import_status"] = importError == null ? (changed ? "completed" : "not_required") : "unavailable",
                ["undo_scope"] = creation ? "subsequent_edits_in_current_editor_domain_only" : "current_editor_domain_conditional_file_restore",
                ["creation_undo"] = false,
                ["import_may_generate_configured_wrapper_code"] = true,
                ["compilation_observation"] = new JObject
                {
                    ["is_compiling"] = EditorApplication.isCompiling,
                    ["is_updating"] = EditorApplication.isUpdating,
                    ["complete"] = false,
                    ["scope"] = "immediate_editor_flags_only; compilation diagnostics are not observed",
                },
                ["verification"] = verification,
            };
            return new SuccessResponse(
                importError == null
                    ? "InputAction source saved and imported."
                    : "InputAction source saved; import observation failed. Inspect before retrying.",
                data
            );
        }

        internal static void CommandKeys(JObject p, string action, bool revision = false)
        {
            string[] fields;
            switch (action)
            {
                case "create_asset":
                    fields = Array.Empty<string>();
                    break;
                case "add_map":
                    fields = new[] { "name" };
                    break;
                case "add_action":
                    fields = new[] { "map_id", "name", "action_type", "expected_control_type", "interactions", "processors" };
                    break;
                case "add_binding":
                    fields = new[] { "map_id", "action_id", "binding_path", "groups", "interactions", "processors" };
                    break;
                case "set_binding":
                    fields = new[] { "map_id", "binding_id", "binding_path", "groups", "interactions", "processors" };
                    break;
                case "remove_binding":
                    fields = new[] { "map_id", "binding_id" };
                    break;
                default:
                    throw new ArgumentException("Unknown InputAction authoring operation.");
            }
            if (revision)
                Keys(p, fields.Concat(new[] { "expected_revision" }).ToArray());
            else
            {
                var allowed = new HashSet<string>(fields, StringComparer.Ordinal) { "action" };
                if (p.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw new ArgumentException("Unknown or action-inapplicable command field.");
            }
        }

        private static object Read(string path, JObject p)
        {
            byte[] bytes = InputActionDocument.ReadBytes(path);
            string revision = InputActionDocument.Digest(bytes);
            JObject document = InputActionDocument.Parse(bytes);
            InputActionDocument.Validate(document);
            int size = Integer(p, "page_size", 100, 1, 100),
                cursor = Integer(p, "cursor", 0, 0, 2048);
            string expected = Text(p, "expected_revision", false, 64);
            if ((cursor > 0 && expected == null) || (expected != null && expected != revision))
                throw new InvalidOperationException("Paged continuation requires the unchanged source revision.");
            var rows = InputActionDocument.Rows(document);
            if (cursor > rows.Count)
                throw new ArgumentException("cursor exceeds the available row count.");
            return new
            {
                path,
                asset_guid = AssetDatabase.AssetPathToGUID(path),
                revision,
                rows = rows.Skip(cursor).Take(size).ToArray(),
                total = rows.Count,
                next_cursor = cursor + size < rows.Count ? (int?)(cursor + size) : null,
                undo_status = InputActionFileUndo.Status(path),
                creation_undo = false,
                undo_scope = "current_editor_domain_conditional_file_restore",
                limitations = "Simple bindings only. Existing control schemes/composites are preserved; no simulation, pairing or enable/disable changes. Configured importer wrapper generation may compile scripts. Undo does not survive assembly/domain reload.",
            };
        }

        private static object Capabilities()
        {
            bool available = Available(out string version);
            return new
            {
                available,
                package = "com.unity.inputsystem",
                package_version = version,
                minimum_version = "1.7.0",
                actions = new[] { "capabilities", "read", "create_asset", "edit", "add_map", "add_action", "add_binding", "set_binding", "remove_binding" },
                max_edit_commands = 64,
                local_selector_syntax = "$ref (earlier same-request creation of the matching kind)",
                max_source_bytes = InputActionDocument.MaxBytes,
                max_maps = 32,
                max_actions = 256,
                max_bindings = 1024,
                max_page_size = 100,
                max_domain_undo_edits = 32,
                simple_binding_authoring = true,
                creation_undo = false,
                undo_survives_domain_reload = false,
                installs_packages = false,
                preserves_existing_player_input_links = true,
                enables_input_actions = false,
            };
        }

        private static bool Available(out string version)
        {
            version = AssetType == null ? null : UnityEditor.PackageManager.PackageInfo.FindForAssembly(AssetType.Assembly)?.version;
            return AssetType != null && Version.TryParse(version?.Split('-')[0], out Version parsed) && parsed >= new Version(1, 7, 0);
        }

        internal static string Text(JObject p, string key, bool required = false, int max = 512)
        {
            JToken value = p[key];
            if (value == null)
            {
                if (required)
                    throw new ArgumentException(key + " is required.");
                return null;
            }
            if (
                value.Type != JTokenType.String
                || value.ToString().Length > max
                || (required && string.IsNullOrWhiteSpace(value.ToString()))
                || value.ToString().Contains("\0")
            )
                throw new ArgumentException(key + " must be a bounded string without NUL.");
            return value.ToString();
        }

        private static int Integer(JObject p, string key, int fallback, int min, int max)
        {
            if (p[key] == null)
                return fallback;
            if (p[key].Type != JTokenType.Integer || !int.TryParse(p[key].ToString(), out int value) || value < min || value > max)
                throw new ArgumentException(key + " is outside its integer bounds.");
            return value;
        }

        private static void Keys(JObject p, params string[] keys)
        {
            var allowed = new HashSet<string>(keys, StringComparer.Ordinal) { "action", "path" };
            if (p.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unknown or action-inapplicable InputAction field.");
        }
    }
}
