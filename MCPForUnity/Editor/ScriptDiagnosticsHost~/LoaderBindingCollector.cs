using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace UnityMcp.ScriptDiagnostics;

internal sealed class LoaderBindingCollector
{
    internal List<LoaderBinding> Rows { get; } = [];
    internal HashSet<string> Gaps { get; } = [];
    private int bytes;

    internal void Capture(Compilation compilation, string root, HashSet<string> selected, CancellationToken cancellation)
    {
        if (Gaps.Contains("loader_binding_limit"))
            return;
        foreach (var tree in compilation.SyntaxTrees.Where(tree => selected.Contains(tree.FilePath)))
        {
            var model = compilation.GetSemanticModel(tree);
            string path = Path.GetRelativePath(root, tree.FilePath).Replace('\\', '/');
            foreach (var call in tree.GetRoot(cancellation).DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                cancellation.ThrowIfCancellationRequested();
                var name = call.Expression switch
                {
                    SimpleNameSyntax simple => simple,
                    MemberAccessExpressionSyntax member => member.Name,
                    MemberBindingExpressionSyntax conditional => conditional.Name,
                    _ => null,
                };
                if (name?.Identifier.ValueText is not ("Load" or "LoadAll" or "LoadAsync" or "LoadAssetAsync" or "LoadAssetsAsync" or "LoadSceneAsync"))
                    continue;
                var binding = model.GetSymbolInfo(call, cancellation);
                var method = binding.Symbol as IMethodSymbol;
                var declared = method?.ReducedFrom ?? method;
                var position = name.GetLocation().GetLineSpan().StartLinePosition;
                var row = new LoaderBinding(
                    path,
                    position.Line + 1,
                    position.Character + 1,
                    compilation.Assembly.Identity.Name,
                    declared?.ContainingAssembly.Identity.Name,
                    declared?.ToDisplayString(SymbolDisplayFormat.CSharpErrorMessageFormat),
                    declared == null ? binding.CandidateReason.ToString() : null
                );
                bytes += JsonSerializer.SerializeToUtf8Bytes(row, WireProtocol.Json).Length;
                if (Rows.Count >= 128 || bytes > 64 * 1024)
                {
                    Gaps.Add("loader_binding_limit");
                    return;
                }
                Rows.Add(row);
            }
        }
    }
}
