using System;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Services.ScriptQuality;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class SerializedScriptImpactTests
    {
        private const string GuidValue = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        private static JArray Members =>
            JArray.Parse(
                "[{\"name\":\"count\",\"kind\":\"field\",\"container\":\"global::C\"},{\"name\":\"Click\",\"kind\":\"method\",\"container\":\"global::C\"}]"
            );
        private const string Text =
            "%YAML 1.1\n--- !u!114 &42\nMonoBehaviour:\n  m_Script: {fileID: 11500000, guid: "
            + GuidValue
            + ", type: 3}\n  count: 2\n  clicked:\n    m_PersistentCalls:\n      m_Calls:\n      - m_Target: {fileID: 42}\n        m_MethodName: Click\n";

        [Test]
        public void TextEvidenceConfirmsFieldAndLocallyBoundPersistentCall()
        {
            var result = SerializedScriptImpact.InspectText(Text, GuidValue, "C", Members);
            Assert.That(((JArray)result["members"]).Count, Is.EqualTo(2));
            Assert.That(result["members"].All(m => (string)m["certainty"] == "confirmed_reference"), Is.True);
        }

        [Test]
        public void CrossFileOrMissingTargetsNeverConfirmAMethod()
        {
            foreach (string target in new[] { "{fileID: 99}", "{fileID: 42, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}" })
            {
                var result = SerializedScriptImpact.InspectText(Text.Replace("{fileID: 42}", target), GuidValue, "C", Members);
                Assert.That(result["members"].Any(m => (string)m["kind"] == "method" && (string)m["certainty"] == "confirmed_reference"), Is.False);
            }
        }

        [Test]
        public void DuplicateFileIdsCannotBindPersistentTargets()
        {
            var result = SerializedScriptImpact.InspectText(Text + "--- !u!1 &42\nGameObject:\n  m_Name: Duplicate\n", GuidValue, "C", Members);
            Assert.That(result["members"].Any(), Is.False);
            Assert.That(result["coverage_gaps"].Values<string>(), Does.Contain("ambiguous_file_id"));
        }

        [Test]
        public void BinaryMissingScriptAndAmbiguousYamlRetainCoverageGaps()
        {
            Assert.That(
                SerializedScriptImpact.InspectText("binary\0payload", GuidValue, "C", Members)["coverage_gaps"].Values<string>(),
                Does.Contain("binary_or_unsupported_serialization")
            );
            Assert.That(
                SerializedScriptImpact
                    .InspectText(Text.Replace("{fileID: 11500000, guid: " + GuidValue + ", type: 3}", "{fileID: 0}"), GuidValue, "C", Members)["coverage_gaps"]
                    .Values<string>(),
                Does.Contain("missing_script_reference")
            );
            var ambiguous = SerializedScriptImpact.InspectText(Text + "  text: |\n    m_MethodName: Click\n", GuidValue, "C", Members);
            Assert.That(ambiguous["members"].Any(), Is.False);
        }

        [Test]
        public void ImpactReadPreservesSceneStageAssetAndMetadataAndRefusesSourceDrift()
        {
            string stem = "MCPImpact_" + Guid.NewGuid().ToString("N");
            string script = "Assets/" + stem + ".cs",
                asset = "Assets/" + stem + ".prefab";
            string scriptDisk = Path.Combine(Application.dataPath, stem + ".cs"),
                assetDisk = Path.Combine(Application.dataPath, stem + ".prefab");
            string meta = "fileFormatVersion: 2\nguid: " + GuidValue + "\n";
            var scenes = EditorSceneManager.GetSceneManagerSetup();
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            try
            {
                File.WriteAllText(scriptDisk, "class " + stem + " {}", new UTF8Encoding(false));
                File.WriteAllText(scriptDisk + ".meta", meta);
                File.WriteAllText(assetDisk, Text);
                File.WriteAllText(assetDisk + ".meta", meta.Replace(GuidValue, new string('d', 32)));
                byte[] bytes = File.ReadAllBytes(assetDisk),
                    metadata = File.ReadAllBytes(assetDisk + ".meta");
                var selection = ScriptSelection.Resolve(new JObject { ["paths"] = new JArray(script) });
                var request = new JObject
                {
                    ["action"] = "impact",
                    ["script_path"] = script,
                    ["script_guid"] = GuidValue,
                    ["source_hash"] = selection.Files[0].Hash,
                    ["assets"] = new JArray(asset),
                    ["members"] = Members,
                };
                Assert.That((bool)JObject.FromObject(ManageScriptQuality.HandleCommand(request))["success"], Is.True);
                Assert.That(File.ReadAllBytes(assetDisk), Is.EqualTo(bytes));
                Assert.That(File.ReadAllBytes(assetDisk + ".meta"), Is.EqualTo(metadata));
                Assert.That(PrefabStageUtility.GetCurrentPrefabStage(), Is.SameAs(stage));
                Assert.That(EditorSceneManager.GetSceneManagerSetup().Select(s => s.path), Is.EqualTo(scenes.Select(s => s.path)));
                File.AppendAllText(scriptDisk, "\n// changed");
                Assert.That((bool)JObject.FromObject(ManageScriptQuality.HandleCommand(request))["success"], Is.False);
            }
            finally
            {
                foreach (string path in new[] { scriptDisk, scriptDisk + ".meta", assetDisk, assetDisk + ".meta" })
                    File.Delete(path);
            }
        }
    }
}
