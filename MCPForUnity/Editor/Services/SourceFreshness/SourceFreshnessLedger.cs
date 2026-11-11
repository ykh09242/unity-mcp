using System;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.SourceFreshness
{
    internal sealed class SourceFreshnessLedger
    {
        internal readonly string ProjectId;
        internal readonly string SessionId;
        internal FreshnessObservation Observation { get; private set; } = new();
        internal string CompileId { get; private set; }
        internal string RequestDomain { get; private set; }
        private SourceSnapshot _before;
        private string _verifiedToken;
        private long _verifiedGeneration;

        internal SourceFreshnessLedger(string projectId, string sessionId, string checkpoint = null)
        {
            ProjectId = projectId;
            SessionId = sessionId;
            if (string.IsNullOrEmpty(checkpoint))
                return;
            try
            {
                if (checkpoint.Length > 32768)
                    throw new FormatException();
                var root = JObject.Parse(checkpoint, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if ((int?)root["schema"] != 1 || (string)root["project"] != projectId || (string)root["session"] != sessionId)
                    throw new FormatException();
                var data = root["data"] as JObject;
                if (data == null || (string)root["checksum"] != SourceSnapshot.Hash(data.ToString(Formatting.None)))
                    throw new FormatException();
                _before = data["before"]?.ToObject<SourceSnapshot>();
                CompileId = (string)data["compile_id"];
                RequestDomain = (string)data["domain"];
                _verifiedToken = (string)data["verified_token"];
                _verifiedGeneration = (long?)data["verified_generation"] ?? 0;
                if ((_before != null && !Owns(_before)) || _verifiedGeneration < 0 || (CompileId != null && !CompilationLedger.ValidId(CompileId)))
                    throw new FormatException();
                Observation.Reason = "reload_requires_observation";
            }
            catch (Exception error) when (error is JsonException || error is FormatException || error is InvalidCastException || error is ArgumentException)
            {
                _before = null;
                CompileId = RequestDomain = _verifiedToken = null;
                _verifiedGeneration = 0;
                Observation.Reason = "checkpoint_unavailable";
            }
        }

        internal string Encode()
        {
            var data = new JObject
            {
                ["before"] = _before == null ? null : JObject.FromObject(_before),
                ["compile_id"] = CompileId,
                ["domain"] = RequestDomain,
                ["verified_token"] = _verifiedToken,
                ["verified_generation"] = _verifiedGeneration,
            };
            return new JObject
            {
                ["schema"] = 1,
                ["project"] = ProjectId,
                ["session"] = SessionId,
                ["data"] = data,
                ["checksum"] = SourceSnapshot.Hash(data.ToString(Formatting.None)),
            }.ToString(Formatting.None);
        }

        private bool Owns(SourceSnapshot snapshot) => snapshot != null && snapshot.ProjectId == ProjectId && snapshot.SessionId == SessionId;

        private FreshnessObservation Set(string status, string reason, SourceSnapshot snapshot, CompileObservation compilation = null)
        {
            Observation = new FreshnessObservation
            {
                Status = status,
                Reason = reason,
                Snapshot = snapshot,
                Compilation = compilation,
            };
            return Observation;
        }

        internal FreshnessObservation ObserveSnapshot(SourceSnapshot snapshot)
        {
            if (!Owns(snapshot))
                return Set("unavailable", "identity_mismatch", null);
            if (!snapshot.Complete)
                return Set("incomplete", "input_coverage_incomplete", snapshot);
            var prior = Observation.Snapshot;
            if (prior != null && prior.Token != snapshot.Token)
                return Set("changed", "inputs_changed", snapshot);
            // A raw snapshot may update the age of existing evidence; it cannot create it.
            if (Observation.Status == "observed_current" && _verifiedToken == snapshot.Token)
                return Set("observed_current", "", snapshot, Observation.Compilation);
            return Set(Observation.Status == "pending" ? "pending" : "incomplete", "compilation_evidence_required", snapshot);
        }

        internal void BeginCompilation(string compileId, SourceSnapshot snapshot, string domain)
        {
            if (!CompilationLedger.ValidId(compileId) || !Owns(snapshot))
                throw new ArgumentException("Invalid compilation observation identity.");
            CompileId = compileId;
            RequestDomain = domain;
            _before = snapshot;
            Set("pending", "compilation_requested", snapshot);
        }

        internal FreshnessObservation ObserveCompilation(SourceSnapshot snapshot, CompileObservation compilation)
        {
            if (!Owns(snapshot) || _before == null || compilation.CompileId != CompileId)
                return Set("incomplete", "request_snapshot_unavailable", snapshot);
            if (!_before.Complete || !snapshot.Complete)
                return Set("incomplete", "input_coverage_incomplete", snapshot, compilation);
            if (_before.Token != snapshot.Token)
                return Set("changed", "inputs_changed_since_request", snapshot, compilation);
            if (compilation.Outcome == "failed")
                return Set("compile_failed", "compiler_errors", snapshot, compilation);
            if (!compilation.Finished)
                return Set("pending", "compilation_pending", snapshot, compilation);
            if (!compilation.Complete || !compilation.Attributed || compilation.Generation < 1)
                return Set("incomplete", compilation.Reason ?? "compilation_evidence_incomplete", snapshot, compilation);
            bool reused =
                compilation.AssemblyCount == 0
                && compilation.Reason == "no_assembly_compilation"
                && _verifiedToken == snapshot.Token
                && _verifiedGeneration > 0
                && compilation.Generation >= _verifiedGeneration;
            if (!reused && (compilation.Outcome != "success" || compilation.AssemblyCount < 1))
                return Set("incomplete", "successful_compilation_required", snapshot, compilation);
            if (!reused && !compilation.ReloadObserved)
                return Set("pending", "reload_not_observed", snapshot, compilation);
            if (compilation.Assemblies.Length == 0 || compilation.Assemblies.Length > 512 || compilation.Assemblies.Any(a => !a.Matches))
                return Set("incomplete", "loaded_output_identity_mismatch_or_unavailable", snapshot, compilation);
            _verifiedToken = snapshot.Token;
            _verifiedGeneration = compilation.Generation;
            return Set("observed_current", "", snapshot, compilation);
        }

        internal void Invalidate(string reason) => Set("incomplete", reason, Observation.Snapshot);

        internal bool CheckAdmission(string sourceToken) =>
            Observation.Status == "observed_current" && !string.IsNullOrEmpty(sourceToken) && sourceToken == Observation.Snapshot?.Token;
    }
}
