using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Dependencies.PlatformDetectors;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Dependencies
{
    /// <summary>
    /// Main orchestrator for dependency validation and management
    /// </summary>
    public static class DependencyManager
    {
        private static readonly List<IPlatformDetector> _detectors = new List<IPlatformDetector>
        {
            new WindowsPlatformDetector(),
            new MacOSPlatformDetector(),
            new LinuxPlatformDetector(),
        };

        private static IPlatformDetector _currentDetector;

        /// <summary>
        /// Get the platform detector for the current operating system
        /// </summary>
        public static IPlatformDetector GetCurrentPlatformDetector()
        {
            if (_currentDetector == null)
            {
                _currentDetector = _detectors.FirstOrDefault(d => d.CanDetect);
                if (_currentDetector == null)
                {
                    throw new PlatformNotSupportedException($"No detector available for current platform: {RuntimeInformation.OSDescription}");
                }
            }
            return _currentDetector;
        }

        /// <summary>
        /// Perform a comprehensive dependency check
        /// </summary>
        private static readonly DependencyCheckCache checks = new(result => string.Join("|", result.Dependencies.Select(dep => FileIdentity(dep.Path))));

        /// <summary>Called on the Editor thread; subprocess work runs on a worker.</summary>
        public static Task<DependencyCheckResult> CheckAllDependenciesAsync(bool force = false)
        {
            var detector = ((PlatformDetectorBase)GetCurrentPlatformDetector()).CaptureForBackground();
            string key = Environment.GetEnvironmentVariable("PATH") + "|" + detector.UvxInputIdentity + "|" + FileIdentity(detector.UvxInputPath);
            return checks.Request(key, token => CheckDependencies(detector, token), force);
        }

        private static string FileIdentity(string path)
        {
            try
            {
                var file = new FileInfo(path);
                return path + ":" + (file.Exists ? file.Length + ":" + file.LastWriteTimeUtc.Ticks : "missing");
            }
            catch
            {
                return path ?? "";
            }
        }

        public static DependencyCheckResult CheckAllDependencies() => CheckDependencies(GetCurrentPlatformDetector(), CancellationToken.None);

        private static DependencyCheckResult CheckDependencies(IPlatformDetector detector, CancellationToken token)
        {
            var result = new DependencyCheckResult();

            try
            {
                token.ThrowIfCancellationRequested();

                // Check Python
                var pythonStatus = detector.DetectPython();
                result.Dependencies.Add(pythonStatus);

                // Check uv
                token.ThrowIfCancellationRequested();
                var uvStatus = detector.DetectUv();
                result.Dependencies.Add(uvStatus);

                // Check git (optional: Package Manager Git-URL installs only)
                token.ThrowIfCancellationRequested();
                result.Dependencies.Add(detector.DetectGit());

                // Generate summary and recommendations
                result.GenerateSummary();
                GenerateRecommendations(result, detector);

                token.ThrowIfCancellationRequested();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex)
            {
                result.Summary = $"Dependency check failed: {ex.Message}";
                result.IsSystemReady = false;
            }

            return result;
        }

        /// <summary>
        /// Get installation recommendations for the current platform
        /// </summary>
        public static string GetInstallationRecommendations()
        {
            try
            {
                var detector = GetCurrentPlatformDetector();
                return detector.GetInstallationRecommendations();
            }
            catch (Exception ex)
            {
                return $"Error getting installation recommendations: {ex.Message}";
            }
        }

        /// <summary>
        /// Get platform-specific installation URLs
        /// </summary>
        public static (string pythonUrl, string uvUrl) GetInstallationUrls()
        {
            try
            {
                var detector = GetCurrentPlatformDetector();
                return (detector.GetPythonInstallUrl(), detector.GetUvInstallUrl());
            }
            catch
            {
                return ("https://python.org/downloads/", "https://docs.astral.sh/uv/getting-started/installation/");
            }
        }

        private static void GenerateRecommendations(DependencyCheckResult result, IPlatformDetector detector)
        {
            var missing = result.GetMissingDependencies();

            if (missing.Count == 0)
            {
                result.RecommendedActions.Add("All dependencies are available. You can start using MCP for Unity.");
                return;
            }

            foreach (var dep in missing)
            {
                if (dep.Name == "Python")
                {
                    result.RecommendedActions.Add($"Install Python 3.11+ from: {detector.GetPythonInstallUrl()}");
                }
                else if (dep.Name == "uv Package Manager")
                {
                    result.RecommendedActions.Add($"Install uv package manager from: {detector.GetUvInstallUrl()}");
                }
                else if (dep.Name == "MCP Server")
                {
                    result.RecommendedActions.Add("MCP Server will be installed automatically when needed.");
                }
            }

            if (result.GetMissingRequired().Count > 0)
            {
                result.RecommendedActions.Add("Use the Setup Window (Window > MCP for Unity > Local Setup Window) for guided installation.");
            }
        }
    }

    /// <summary>One probe at a time; a changed input cancels the old snapshot before its successor runs.</summary>
    internal sealed class DependencyCheckCache
    {
        private readonly object gate = new();
        private string currentKey;
        private Task<DependencyCheckResult> current;
        private CancellationTokenSource cancellation;
        private DateTime requested;
        private readonly Func<DependencyCheckResult, string> resultIdentity;
        private string completedIdentity;

        public DependencyCheckCache()
            : this(null) { }

        internal DependencyCheckCache(Func<DependencyCheckResult, string> resultIdentity)
        {
            this.resultIdentity = resultIdentity;
        }

        public Task<DependencyCheckResult> Request(string key, Func<CancellationToken, DependencyCheckResult> probe, bool force = false)
        {
            lock (gate)
            {
                if (
                    key == currentKey
                    && current != null
                    && (
                        !current.IsCompleted
                        || (
                            !force
                            && current.Status == TaskStatus.RanToCompletion
                            && DateTime.UtcNow - requested < TimeSpan.FromSeconds(30)
                            && (resultIdentity == null || resultIdentity(current.Result) == completedIdentity)
                        )
                    )
                )
                    return current;
                var previous = current;
                var previousCancellation = cancellation;
                if (previous != null && !previous.IsCompleted)
                    previousCancellation?.Cancel();
                var source = new CancellationTokenSource();
                cancellation = source;
                var token = source.Token;
                currentKey = key;
                requested = DateTime.UtcNow;
                current = Task.Run(async () =>
                {
                    if (previous != null)
                    {
                        try
                        {
                            await previous.ConfigureAwait(false);
                        }
                        catch (Exception) { /* The caller of the previous task still observes its failure. */ }
                    }
                    previousCancellation?.Dispose();
                    token.ThrowIfCancellationRequested();
                    var result = probe(token);
                    token.ThrowIfCancellationRequested();
                    lock (gate)
                    {
                        if (ReferenceEquals(cancellation, source))
                            completedIdentity = resultIdentity?.Invoke(result);
                    }
                    return result;
                });
                return current;
            }
        }
    }
}
