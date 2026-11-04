using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Dependencies
{
    /// <summary>
    /// Runs the official uv installer for the current platform. UI-agnostic: build the command,
    /// run it (off the main thread), and report the outcome. The caller owns confirmation and
    /// re-checking dependencies afterwards.
    /// Installer reference: https://docs.astral.sh/uv/getting-started/installation/
    /// </summary>
    public static class UvInstaller
    {
        // Process boundary only: tests replace this with inert results, never an installer.
        private static Func<string, string, int, UvInstallResult> runCommand = RunCommand;

        public readonly struct UvInstallResult
        {
            public readonly bool Success;
            public readonly string Output;

            public UvInstallResult(bool success, string output)
            {
                Success = success;
                Output = output ?? string.Empty;
            }
        }

        /// <summary>One-click install is available on Windows, macOS and Linux.</summary>
        public static bool IsSupported =>
            RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            || RuntimeInformation.IsOSPlatform(OSPlatform.OSX)
            || RuntimeInformation.IsOSPlatform(OSPlatform.Linux);

        /// <summary>
        /// Build the download stage for the current platform. The default destination is
        /// a preview placeholder; Run supplies its owned temporary installer path.
        /// </summary>
        public static (string file, string arguments) BuildInstallCommand(string destination = "<temporary installer>")
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                string path = destination.Replace("'", "''");
                return (
                    "powershell",
                    "-NoProfile -NonInteractive -Command \"try { Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile '"
                        + path
                        + "' -ErrorAction Stop; exit 0 } catch { Write-Error $_; exit 1 }\""
                );
            }

            return ("curl", "-LsSf --output " + QuotePath(destination) + " https://astral.sh/uv/install.sh");
        }

        /// <summary>Human-readable one-line description of the command that will run.</summary>
        public static string DescribeCommand()
        {
            var (file, arguments) = BuildInstallCommand();
            return $"Download: {file} {arguments}\nThen execute the downloaded installer with "
                + (RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? "PowerShell -File" : "/bin/sh")
                + ". Stop on a failed download or execution, then remove the temporary installer.";
        }

        /// <summary>
        /// Run the installer and return the outcome. Blocks until the installer exits or the
        /// timeout elapses, so call it off the main thread (e.g. via Task.Run).
        /// </summary>
        public static UvInstallResult Run(int timeoutMs = 180000)
        {
            string directory = null;
            try
            {
                if (timeoutMs <= 0)
                    return new UvInstallResult(false, "Installer timeout must be positive.");
                var clock = Stopwatch.StartNew();
                directory = Path.Combine(Path.GetTempPath(), "UnityMCPUvInstaller_" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(directory);
                bool windows = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
                string installer = Path.Combine(directory, windows ? "install.ps1" : "install.sh");
                var (file, arguments) = BuildInstallCommand(installer);
                var download = runCommand(file, arguments, timeoutMs);
                if (!download.Success)
                    return new UvInstallResult(false, "Installer download failed.\n" + download.Output);
                if (!File.Exists(installer) || new FileInfo(installer).Length == 0)
                    return new UvInstallResult(false, "Installer download did not produce a nonempty file.");
                int remaining = (int)Math.Max(0, timeoutMs - clock.ElapsedMilliseconds);
                if (remaining == 0)
                    return new UvInstallResult(false, "Installer timed out after download; execution was not started.");
                var execution = runCommand(
                    windows ? "powershell" : "/bin/sh",
                    (windows ? "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " : "") + QuotePath(installer),
                    remaining
                );
                return new UvInstallResult(execution.Success, execution.Success ? execution.Output : "Installer execution failed.\n" + execution.Output);
            }
            catch (Exception ex)
            {
                return new UvInstallResult(false, ex.Message);
            }
            finally
            {
                if (directory != null)
                {
                    try
                    {
                        Directory.Delete(directory, true);
                    }
                    catch (IOException) { /* Report the installer result even if temporary cleanup is unavailable. */ }
                    catch (UnauthorizedAccessException) { /* Report the installer result even if temporary cleanup is unavailable. */ }
                }
            }
        }

        private static string QuotePath(string path)
        {
            string value = RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? path : path.Replace("\\", "\\\\").Replace("\"", "\\\"");
            return "\"" + value + "\"";
        }

        private static UvInstallResult RunCommand(string file, string arguments, int timeoutMs)
        {
            bool success = ExecPath.TryRun(file, arguments, null, out string stdout, out string stderr, timeoutMs);
            return new UvInstallResult(success, Combine(stdout, stderr));
        }

        private static string Combine(string stdout, string stderr)
        {
            string outText = (stdout ?? string.Empty).Trim();
            string errText = (stderr ?? string.Empty).Trim();
            string combined =
                string.IsNullOrEmpty(errText) ? outText
                : string.IsNullOrEmpty(outText) ? errText
                : outText + "\n" + errText;

            // Keep dialogs readable — echo only the tail of long installer output.
            const int max = 1500;
            if (combined.Length > max)
            {
                combined = "…" + combined.Substring(combined.Length - max);
            }
            return combined;
        }
    }
}
