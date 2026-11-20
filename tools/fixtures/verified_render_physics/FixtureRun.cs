using System;
using System.IO;
using UnityEngine;

namespace VerifiedWorkflows
{
    internal static class FixtureRun
    {
        internal static string OpenOutput(string fixture)
        {
            string project = Path.GetDirectoryName(Application.dataPath);
            if (!File.Exists(Path.Combine(project, "ProjectSettings", "VerifiedRenderPhysics-" + fixture)))
                throw new InvalidOperationException("Use an owned disposable " + fixture + " verification project.");
            string output = Path.GetFullPath(Argument("--fixture-output"));
            if (Directory.Exists(output) || File.Exists(output))
                throw new InvalidOperationException("The evidence output must not already exist: " + output);
            Directory.CreateDirectory(output);
            return output;
        }

        internal static string Argument(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, name);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
                throw new ArgumentException("Provide exactly one " + name + ".");
            return args[index + 1];
        }
    }
}
