using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    // One transaction per project. The OS lease excludes live writers; SessionState also
    // distinguishes an interrupted invocation in this Editor from a subsequent project open.
    internal sealed class PlayScenarioBuildJournal : IDisposable
    {
        internal const string ActiveKey = "MCPForUnity.ScenarioPlayerBuild.Active";
        internal const string DirectoryName = "Library/MCPForUnity/ScenarioPlayerBuild";
        internal const string Resource = "Resources/" + PlayScenarioPlayerBundle.ResourceName + ".json";
        private static readonly string[] Assets = { "", "Resources", Resource, "link.xml", "Bootstrap.unity" };
        private readonly string project;
        private readonly string root;
        private readonly ExclusiveFileLease lease;
        private readonly Func<ScriptingImplementation> backend;
        private readonly Action<ScriptingImplementation> setBackend;
        private JObject record;
        private string activeId;

        internal PlayScenarioBuildJournal(string project, Func<ScriptingImplementation> backend, Action<ScriptingImplementation> setBackend)
        {
            this.project = Path.GetFullPath(project);
            this.backend = backend;
            this.setBackend = setBackend;
            root = SafePathUtility.ResolveWithinRoot(this.project, DirectoryName);
            Directory.CreateDirectory(root);
            lease = ExclusiveFileLease.Acquire(Checked("owner.lock"));
        }

        private string Checked(string name) => SafePathUtility.ResolveWithinRoot(root, name);

        internal static bool Pending(string project) => File.Exists(SafePathUtility.ResolveWithinRoot(project, DirectoryName + "/state.json"));

        internal string AssetFolder => "Assets/__MCPScenarioBuild_" + (string)record["id"];

        private string Absolute(string relative) => SafePathUtility.ResolveWithinRoot(project, AssetPath(relative));

        private string AssetPath(string relative) => relative.Length == 0 ? AssetFolder : AssetFolder + "/" + relative;

        internal void Begin(ScriptingImplementation selected)
        {
            if (Pending(project))
                throw new IOException("A previous scenario build journal requires recovery before creating another build.");
            var id = Guid.NewGuid().ToString("N");
            record = new JObject
            {
                ["version"] = 1,
                ["project"] = project,
                ["id"] = id,
                ["before"] = (int)backend(),
                ["selected"] = (int)selected,
                ["backend_settled"] = true,
                ["assets"] = new JObject(),
            };
            RequireAbsent("");
            Save();
            activeId = id;
            SessionState.SetString(ActiveKey, id);
        }

        internal void ApplyBackend()
        {
            var previous = (ScriptingImplementation)(int)record["before"];
            var selected = (ScriptingImplementation)(int)record["selected"];
            if (backend() != previous)
                throw new IOException("The standalone scripting backend changed before scenario build preparation.");
            if (selected == previous)
                return;
            record["backend_settled"] = false;
            Save();
            setBackend(selected);
        }

        internal void RequireAbsent(string relative)
        {
            if (relative.Length > 0)
                VerifyAsset("");
            if (relative == Resource)
                VerifyAsset("Resources");
            string path = Absolute(relative);
            string meta = SafePathUtility.ResolveWithinRoot(project, path + ".meta");
            if (File.Exists(path) || Directory.Exists(path) || File.Exists(meta) || Directory.Exists(meta))
                throw Conflict(relative);
        }

        internal void Capture(string relative, string expectedContent = null)
        {
            if (!Assets.Contains(relative))
                throw new ArgumentException("Unknown temporary build asset.");
            string path = Absolute(relative);
            string meta = SafePathUtility.ResolveWithinRoot(project, path + ".meta");
            string content = IsFolder(relative) ? null : AtomicAssetFile.DigestFile(path);
            if (expectedContent != null && AtomicAssetFile.Digest(Encoding.UTF8.GetBytes(expectedContent)) != content)
                throw new IOException("Temporary build asset changed during import: " + AssetPath(relative));
            ((JObject)record["assets"]).Add(relative, new JObject { ["meta"] = AtomicAssetFile.DigestFile(meta), ["content"] = content });
            Save();
        }

        internal void Recover()
        {
            if (!Pending(project))
                return;
            record = Load();
            if ((string)record["id"] == SessionState.GetString(ActiveKey, ""))
                throw new IOException("An interrupted scenario build still belongs to this Editor session. Inspect the native build, then reopen the project.");
            Finish();
        }

        internal void Finish()
        {
            // Retry only persisted comparisons. A failed capture must never promote its
            // uncommitted in-memory proof to permission to delete an asset.
            string id = (string)record["id"];
            record = Load();
            if ((string)record["id"] != id)
                throw new IOException("Scenario build recovery ownership changed.");
            if (!(bool)record["backend_settled"])
            {
                var previous = (ScriptingImplementation)(int)record["before"];
                var selected = (ScriptingImplementation)(int)record["selected"];
                if (selected != previous && backend() == selected)
                    setBackend(previous);
                record["backend_settled"] = true;
                Save();
            }

            // Inspect only fixed, known names. Refuse changed bytes, changed folder identities,
            // unknown children (including hidden files), and assets created before a receipt was durable.
            foreach (string relative in Assets)
                VerifyAsset(relative);
            for (int index = Assets.Length - 1; index >= 0; index--)
                RemoveAsset(Assets[index]);
            RequireAbsent("");
            File.Delete(Checked("state.json"));
        }

        private static bool IsFolder(string relative) => relative.Length == 0 || relative == "Resources";

        private void VerifyAsset(string relative)
        {
            string path = Absolute(relative);
            string meta = SafePathUtility.ResolveWithinRoot(project, path + ".meta");
            bool file = File.Exists(path),
                folder = Directory.Exists(path),
                hasMeta = File.Exists(meta);
            if (!file && !folder && !hasMeta && !Directory.Exists(meta))
                return; // Already removed, or preparation never reached this asset.
            var proof = record["assets"][relative];
            if (
                proof == null
                || !hasMeta
                || Directory.Exists(meta)
                || AtomicAssetFile.DigestFile(meta) != (string)proof["meta"]
                || file && (IsFolder(relative) || AtomicAssetFile.DigestFile(path) != (string)proof["content"])
                || folder && !IsFolder(relative)
            )
                throw Conflict(relative);
            if (folder)
            {
                foreach (string entry in Directory.EnumerateFileSystemEntries(path))
                {
                    string name = Path.GetFileName(entry);
                    if (name.EndsWith(".meta", StringComparison.Ordinal))
                        name = name.Substring(0, name.Length - 5);
                    string child = relative.Length == 0 ? name : relative + "/" + name;
                    if (name.Length == 0 || !Assets.Contains(child))
                        throw Conflict(relative);
                }
            }
        }

        private IOException Conflict(string relative) =>
            new IOException(
                "Scenario build recovery cannot verify ownership of " + AssetPath(relative) + ". Inspect the retained assets before another build."
            );

        private void RemoveAsset(string relative)
        {
            VerifyAsset(relative);
            string path = Absolute(relative);
            if (File.Exists(path) || Directory.Exists(path))
            {
                if (Directory.Exists(path) && Directory.EnumerateFileSystemEntries(path).Any())
                    throw Conflict(relative);
                if (!AssetDatabase.DeleteAsset(AssetPath(relative)))
                    throw new IOException("Could not remove the verified scenario build asset: " + AssetPath(relative));
            }
            else if (File.Exists(SafePathUtility.ResolveWithinRoot(project, path + ".meta")))
                File.Delete(path + ".meta");
        }

        private JObject Load()
        {
            lease.Verify();
            string json = PlayScenarioPlayerFiles.Read(Checked("state.json"), 16384);
            var envelope = JObject.Parse(json);
            var value = envelope["record"] as JObject;
            if (
                value == null
                || (string)envelope["sha256"] != Digest(value)
                || (int?)value["version"] != 1
                || (string)value["project"] != project
                || !Guid.TryParseExact((string)value["id"], "N", out _)
                || value["before"]?.Type != JTokenType.Integer
                || !Enum.IsDefined(typeof(ScriptingImplementation), (int)value["before"])
                || value["selected"]?.Type != JTokenType.Integer
                || (int)value["selected"] != (int)ScriptingImplementation.Mono2x && (int)value["selected"] != (int)ScriptingImplementation.IL2CPP
                || value["backend_settled"]?.Type != JTokenType.Boolean
                || value["assets"] is not JObject proofs
                || proofs
                    .Properties()
                    .Any(p =>
                        !Assets.Contains(p.Name)
                        || p.Value is not JObject proof
                        || proof["meta"]?.Type != JTokenType.String
                        || proof["content"]?.Type != (IsFolder(p.Name) ? JTokenType.Null : JTokenType.String)
                    )
            )
                throw new IOException("Invalid scenario build journal identity or ownership evidence.");
            return value;
        }

        private static string Digest(JObject value) => AtomicAssetFile.Digest(Encoding.UTF8.GetBytes(value.ToString(Formatting.None)));

        private void Save()
        {
            lease.Verify();
            string path = Checked("state.json"),
                stage = Checked("state.stage");
            File.Delete(stage); // Only the lease owner can publish this journal's fixed staging file.
            var envelope = new JObject { ["record"] = record, ["sha256"] = Digest(record) };
            byte[] bytes = Encoding.UTF8.GetBytes(envelope.ToString(Formatting.None));
            AtomicFilePublication.StageNew(stage, stream => stream.Write(bytes, 0, bytes.Length));
            if (!File.Exists(path))
                AtomicFilePublication.Create(stage, path);
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                File.Replace(stage, path, null);
            else
            {
                AtomicFilePublication.Exchange(stage, path);
                File.Delete(stage);
            }
        }

        public void Dispose()
        {
            if (activeId != null && SessionState.GetString(ActiveKey, "") == activeId)
                SessionState.EraseString(ActiveKey);
            lease.Dispose();
        }
    }
}
