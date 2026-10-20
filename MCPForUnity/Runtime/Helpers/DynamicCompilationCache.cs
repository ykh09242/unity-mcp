using System;
using System.Collections.Generic;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>
    /// Owns reuse and admission for assemblies that cannot be unloaded individually on Mono.
    /// A domain reload, not clearing execution history or refreshing references, resets the shared budget.
    /// </summary>
    public sealed class DynamicCompilationCache
    {
        public static readonly DynamicCompilationCache Shared = new DynamicCompilationCache(256);
        private readonly object gate = new object();
        private readonly Dictionary<string, Assembly> assemblies = new Dictionary<string, Assembly>(StringComparer.Ordinal);
        private int compiling;
        private int loaded;

        public DynamicCompilationCache(int limit)
        {
            if (limit < 1)
                throw new ArgumentOutOfRangeException(nameof(limit));
            Limit = limit;
        }

        public int Limit { get; }
        public int Count
        {
            get
            {
                lock (gate)
                    return loaded;
            }
        }

        /// <summary>
        /// False means admission was refused before invoking the compiler. A null assembly
        /// from the compiler is a compilation failure and consumes no budget.
        /// </summary>
        public bool TryGetOrCompile(string sourceIdentity, Func<Assembly> compile, out Assembly assembly)
        {
            if (sourceIdentity == null)
                throw new ArgumentNullException(nameof(sourceIdentity));
            if (compile == null)
                throw new ArgumentNullException(nameof(compile));
            string key;
            using (var hash = SHA256.Create())
                key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(sourceIdentity)));
            lock (gate)
            {
                if (assemblies.TryGetValue(key, out assembly))
                    return true;
                if (loaded + compiling >= Limit)
                    return false;
                compiling++;
                try
                {
                    assembly = compile();
                    if (assembly != null)
                    {
                        loaded++;
                        assemblies[key] = assembly;
                    }
                    return true;
                }
                finally
                {
                    compiling--;
                }
            }
        }
    }
}
