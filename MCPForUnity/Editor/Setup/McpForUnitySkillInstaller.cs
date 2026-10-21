using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Setup
{
    public class McpForUnitySkillInstaller : EditorWindow
    {
        private const string RepoUrlKey = "UnityMcpSkillSync.RepoUrl.ykh09242";
        private const string BranchKey = "UnityMcpSkillSync.Branch";
        private const string CliKey = "UnityMcpSkillSync.Cli";
        private const string InstallDirKey = "UnityMcpSkillSync.InstallDir";
        private const string CodexCli = "codex";
        private const string ClaudeCli = "claude";
        private static readonly string[] BranchOptions = { "beta", "main" };
        private static readonly string[] SourceOptions = { "Installed package", "Latest from branch" };
        private static readonly string[] CliOptions = { CodexCli, ClaudeCli };

        private string _repoUrl;
        private string _targetBranch;
        private string _cliType;
        private string _installDir;
        private bool _latest;
        private SkillSyncSource _packageSource;
        private string _packageError;
        private Vector2 _scroll;
        private volatile bool _isRunning;
        private SkillSyncService.SyncPreview _preview;
        private readonly BoundedEditorLog _log = new();
        private long _displayedLogRevision;
        private double _nextRepaintAt;

        [MenuItem(ProductInfo.MenuRoot + "/Skill Installer", priority = 4)]
        public static void OpenWindow()
        {
            GetWindow<McpForUnitySkillInstaller>("Unity MCP Skill Install(Sync)");
        }

        private void OnEnable()
        {
            var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            _repoUrl = EditorPrefs.GetString(RepoUrlKey, "https://github.com/ykh09242/unity-mcp");
            _targetBranch = EditorPrefs.GetString(BranchKey, "beta");
            if (!BranchOptions.Contains(_targetBranch))
            {
                _targetBranch = "beta";
            }
            _cliType = EditorPrefs.GetString(CliKey, CodexCli);
            if (!CliOptions.Contains(_cliType))
            {
                _cliType = CodexCli;
            }
            _installDir = EditorPrefs.GetString(InstallDirKey, GetDefaultInstallDir(userHome, _cliType));
            _latest = false;
            RefreshPackageSource();
            EditorApplication.update += OnEditorUpdate;
        }

        private void OnDisable()
        {
            EditorApplication.update -= OnEditorUpdate;
            EditorPrefs.SetString(RepoUrlKey, _repoUrl);
            EditorPrefs.SetString(BranchKey, _targetBranch);
            EditorPrefs.SetString(CliKey, _cliType);
            EditorPrefs.SetString(InstallDirKey, _installDir);
        }

        private void OnGUI()
        {
            FlushPendingLogs();
            EditorGUILayout.HelpBox("Preview upstream changes, then apply safe changes while preserving local edits and additions.", MessageType.Info);
            EditorGUILayout.Space(4f);

            EditorGUILayout.LabelField("Config", EditorStyles.boldLabel);
            EditorGUI.BeginChangeCheck();
            using (new EditorGUI.DisabledScope(_isRunning))
            {
                _latest = EditorGUILayout.Popup("Skill source", _latest ? 1 : 0, SourceOptions) == 1;
                if (!_latest)
                {
                    EditorGUILayout.HelpBox(_packageSource?.Description ?? _packageError, _packageSource == null ? MessageType.Error : MessageType.Info);
                    EditorGUILayout.LabelField(
                        "Local changes are preserved; installed files may differ from the selected source.",
                        EditorStyles.wordWrappedLabel
                    );
                }
                if (_latest)
                {
                    _repoUrl = EditorGUILayout.TextField("Repo URL", _repoUrl);
                    var branchIndex = Array.IndexOf(BranchOptions, _targetBranch);
                    if (branchIndex < 0)
                    {
                        branchIndex = 0;
                    }

                    var selectedBranchIndex = EditorGUILayout.Popup("Branch", branchIndex, BranchOptions);
                    _targetBranch = BranchOptions[selectedBranchIndex];
                }
                else
                    EditorGUILayout.LabelField("Repository", SkillSyncSource.ForkRepository);
                if (_latest)
                    EditorGUILayout.HelpBox(
                        "Latest resolves the selected branch at preview time. Compatibility with this package is not verified.",
                        MessageType.Info
                    );

                var cliIndex = Array.IndexOf(CliOptions, _cliType);
                if (cliIndex < 0)
                {
                    cliIndex = 0;
                }

                var selectedCliIndex = EditorGUILayout.Popup("CLI", cliIndex, CliOptions);
                if (selectedCliIndex != cliIndex)
                {
                    var previousCli = _cliType;
                    _cliType = CliOptions[selectedCliIndex];
                    TryApplyCliDefaultInstallPath(previousCli, _cliType);
                }

                _installDir = EditorGUILayout.TextField("Install Dir", _installDir);
            }
            if (EditorGUI.EndChangeCheck())
                _preview = null;

            EditorGUILayout.Space(8f);
            EditorGUILayout.BeginHorizontal();
            using (new EditorGUI.DisabledScope(_isRunning))
            {
                if (GUILayout.Button(_latest ? $"Preview latest ({_targetBranch})" : "Preview package skills", GUILayout.Height(32f)))
                    RunPreview();
            }

            using (new EditorGUI.DisabledScope(_isRunning || _preview == null))
                if (GUILayout.Button("Apply safe changes", GUILayout.Height(32f)))
                    RunApply();

            if (GUILayout.Button("Clear Log", GUILayout.Width(100f), GUILayout.Height(32f)))
            {
                _log.Clear();
            }
            EditorGUILayout.EndHorizontal();
            if (_preview != null)
                EditorGUILayout.HelpBox(
                    $"Preview: +{_preview.Added.Count} ~{_preview.Updated.Count} -{_preview.Deleted.Count}. "
                        + $"Local preserved: {_preview.Preserved.Count}. Conflicts retained: {_preview.Conflicts.Count}.\n"
                        + _preview.SourceDescription
                        + "\nResolved commit: "
                        + _preview.CommitSha,
                    _preview.Conflicts.Count > 0 ? MessageType.Warning : MessageType.Info
                );

            EditorGUILayout.Space(8f);
            EditorGUILayout.LabelField("Output", EditorStyles.boldLabel);
            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            EditorGUILayout.TextArea(_log.Snapshot(), GUILayout.ExpandHeight(true));
            EditorGUILayout.EndScrollView();
        }

        private void OnEditorUpdate()
        {
            var changed = FlushPendingLogs();
            if (changed || (_isRunning && EditorApplication.timeSinceStartup >= _nextRepaintAt))
            {
                _nextRepaintAt = EditorApplication.timeSinceStartup + 0.25;
                Repaint();
            }
        }

        private void RunPreview()
        {
            if (_isRunning)
                return;
            SkillSyncSource source;
            try
            {
                source = _latest ? SkillSyncSource.Latest(_repoUrl, _targetBranch) : SkillSyncSource.CaptureInstalled();
                if (!_latest)
                    _packageSource = source;
            }
            catch (Exception error)
            {
                _preview = null;
                _packageError = error.Message;
                AppendLineImmediate("[ERROR] " + error.Message);
                return;
            }
            _isRunning = true;
            _preview = null;
            SkillSyncService.PreviewSourceAsync(
                source,
                _installDir,
                _log.Append,
                preview =>
                {
                    if (this == null)
                        return;
                    _isRunning = false;
                    _preview = preview.Success ? preview : null;
                    if (!preview.Success)
                        AppendLineImmediate("[ERROR] " + preview.Error);
                    Repaint();
                }
            );
        }

        private void RefreshPackageSource()
        {
            try
            {
                _packageSource = SkillSyncSource.CaptureInstalled();
                _packageError = null;
            }
            catch (Exception error)
            {
                _packageSource = null;
                _packageError = error.Message;
            }
        }

        private void RunApply()
        {
            if (_isRunning || _preview == null)
                return;
            var preview = _preview;
            _preview = null;
            _isRunning = true;
            SkillSyncService.ApplyAsync(
                preview,
                _log.Append,
                result =>
                {
                    if (this == null)
                        return;
                    _isRunning = false;
                    AppendLineImmediate(SkillSyncService.DescribeResult(result));
                    Repaint();
                }
            );
        }

        private void TryApplyCliDefaultInstallPath(string previousCli, string currentCli)
        {
            var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            var previousDefaultInstall = GetDefaultInstallDir(userHome, previousCli);
            var currentDefaultInstall = GetDefaultInstallDir(userHome, currentCli);

            if (string.IsNullOrWhiteSpace(_installDir) || PathsEqual(_installDir, previousDefaultInstall))
            {
                _installDir = currentDefaultInstall;
            }
        }

        private static string GetDefaultInstallDir(string userHome, string cliType)
        {
            var baseDir = IsClaudeCli(cliType) ? ".claude" : ".codex";
            return Path.Combine(userHome, baseDir, "skills/unity-mcp-skill");
        }

        private static bool IsClaudeCli(string cliType)
        {
            return string.Equals(cliType, ClaudeCli, StringComparison.Ordinal);
        }

        private static bool PathsEqual(string left, string right)
        {
            if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
            {
                return false;
            }

            try
            {
                return string.Equals(SkillSyncService.ExpandPath(left), SkillSyncService.ExpandPath(right), StringComparison.Ordinal);
            }
            catch
            {
                return false;
            }
        }

        private void AppendLineImmediate(string line)
        {
            _log.Append(line);
            _scroll.y = float.MaxValue;
            Repaint();
        }

        private bool FlushPendingLogs()
        {
            long revision = _log.Revision;
            if (revision == _displayedLogRevision)
                return false;
            _displayedLogRevision = revision;
            _scroll.y = float.MaxValue;
            return true;
        }
    }
}
