# Optional script analysis host dependencies

The host restores a locked dependency graph only during explicit setup. No analyzer binaries are shipped in this source directory. See `packages.lock.json` for all exact dependency versions and NuGet content hashes.

- [Roslyn and C# CodeStyle 5.9.0](https://github.com/dotnet/roslyn): MIT, copyright .NET Foundation and contributors.
- [Microsoft.Unity.Analyzers 1.28.0](https://github.com/microsoft/Microsoft.Unity.Analyzers): MIT, copyright Microsoft Corporation.
- [Microsoft.Bcl.AsyncInterfaces 10.0.1](https://github.com/dotnet/runtime): MIT, copyright .NET Foundation and contributors.
- [Humanizer 2.14.1](https://github.com/Humanizr/Humanizer): MIT, copyright Mehdi Khalili and contributors.
- [System.Composition](https://github.com/dotnet/runtime), as pinned in the lock file: MIT, copyright .NET Foundation and contributors.

## MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
