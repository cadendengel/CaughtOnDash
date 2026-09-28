using System;
using System.IO;
using System.Runtime.CompilerServices;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// Keeps the suite out of the real worker's log.
    /// </summary>
    /// <remarks>
    /// Logger opens its file in a static constructor, on first use, so the
    /// directory has to be chosen before any test touches it. A module
    /// initializer runs when this assembly loads, ahead of every test. Running
    /// the tests used to append "Thumbnail too large: https://example.com/..."
    /// to %LOCALAPPDATA%\CaughtOnDash\logs\worker.log, which made reading the
    /// live worker's log misleading.
    /// </remarks>
    internal static class TestLogDirectory
    {
        [ModuleInitializer]
        internal static void Use()
        {
            var directory = Path.Combine(Path.GetTempPath(), "caught_on_dash_worker_tests", "logs");
            Environment.SetEnvironmentVariable(Logger.LogDirectoryVariable, directory);
        }
    }
}
