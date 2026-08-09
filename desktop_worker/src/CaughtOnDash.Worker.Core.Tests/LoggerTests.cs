using System;
using System.IO;
using System.Linq;
using CaughtOnDash.Worker.Models;
using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// The log file, which exists so that "the backend is rejecting the
    /// heartbeats" is a claim someone can check.
    /// </summary>
    /// <remarks>
    /// Logger is static, so these tests share it and must not run beside each
    /// other. The collection attribute below is what enforces that; without it
    /// xUnit parallelises across classes and one test's DisableFileOutput lands
    /// in the middle of another's writes.
    /// </remarks>
    [Collection("logger")]
    public class LoggerTests : IDisposable
    {
        private readonly string _directory;

        public LoggerTests()
        {
            _directory = Path.Combine(Path.GetTempPath(), "cod-logger-" + Guid.NewGuid().ToString("N"));
        }

        public void Dispose()
        {
            Logger.DisableFileOutput();
            try
            {
                if (Directory.Exists(_directory))
                {
                    Directory.Delete(_directory, recursive: true);
                }
            }
            catch
            {
                // A leftover temp directory is not worth failing a test over.
            }
        }

        private string LogFile => Path.Combine(_directory, "worker.log");

        [Fact]
        public void It_creates_the_directory_and_writes_the_line()
        {
            Logger.EnableFileOutput(_directory);

            Logger.Log("hello from the worker");

            Assert.True(File.Exists(LogFile));
            Assert.Contains("hello from the worker", File.ReadAllText(LogFile));
        }

        [Fact]
        public void It_records_the_level_and_a_timestamp()
        {
            Logger.EnableFileOutput(_directory);

            Logger.Log("could not reach the backend", Logger.LogLevel.Error);

            var line = File.ReadAllLines(LogFile).Last();
            Assert.Contains("[ERROR]", line);
            Assert.Matches(@"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", line);
        }

        [Fact]
        public void It_exposes_the_path_so_the_file_can_be_found()
        {
            Logger.EnableFileOutput(_directory);

            Assert.Equal(LogFile, Logger.LogFilePath);
        }

        [Fact]
        public void It_rolls_when_the_file_passes_the_size_limit()
        {
            // A dashcam worker can run for days. Unbounded is its own incident.
            Logger.EnableFileOutput(_directory, maxBytes: 500, maxFiles: 3);

            for (var i = 0; i < 40; i++)
            {
                Logger.Log(new string('x', 100));
            }

            Assert.True(File.Exists(LogFile));
            Assert.True(File.Exists(Path.Combine(_directory, "worker.1.log")));
            Assert.True(new FileInfo(LogFile).Length <= 500);
        }

        [Fact]
        public void It_keeps_no_more_files_than_it_was_told_to()
        {
            Logger.EnableFileOutput(_directory, maxBytes: 300, maxFiles: 3);

            for (var i = 0; i < 100; i++)
            {
                Logger.Log(new string('y', 100));
            }

            var files = Directory.GetFiles(_directory, "worker*.log");
            Assert.Equal(3, files.Length);
        }

        [Fact]
        public void A_single_file_limit_still_bounds_the_size()
        {
            // Degenerate but reachable: with maxFiles at 1 there is nothing to
            // rename, so rolling has to truncate or the file grows forever.
            Logger.EnableFileOutput(_directory, maxBytes: 300, maxFiles: 1);

            for (var i = 0; i < 50; i++)
            {
                Logger.Log(new string('z', 100));
            }

            Assert.Single(Directory.GetFiles(_directory, "worker*.log"));
            Assert.True(new FileInfo(LogFile).Length <= 300);
        }

        [Fact]
        public void An_unwritable_location_does_not_throw()
        {
            // A worker that dies because it could not write a log is worse than
            // one with no logs at all.
            var unwritable = Path.Combine(Path.GetTempPath(), "cod-blocked-" + Guid.NewGuid().ToString("N"));
            File.WriteAllText(unwritable, "this is a file, not a directory");

            try
            {
                Logger.EnableFileOutput(unwritable);
                Logger.Log("the worker keeps going");

                Assert.Null(Logger.LogFilePath);
            }
            finally
            {
                File.Delete(unwritable);
            }
        }

        [Fact]
        public void A_write_failure_mid_run_does_not_throw_and_stops_retrying()
        {
            Logger.EnableFileOutput(_directory);
            Logger.Log("first line, while the directory exists");

            // Pull the directory out from under it, the way an unmounted volume
            // or a cleanup script would.
            Directory.Delete(_directory, recursive: true);

            for (var i = 0; i < 5; i++)
            {
                Logger.Log("still running");
            }

            Assert.Null(Logger.LogFilePath);
        }

        [Fact]
        public void The_in_memory_buffer_survives_the_addition_of_file_output()
        {
            Logger.EnableFileOutput(_directory);
            Logger.ClearLogs();

            Logger.Log("remembered");

            Assert.Contains(Logger.GetLogs(), line => line.Contains("remembered"));
        }

        [Fact]
        public void Startup_context_names_the_backend_and_never_the_token()
        {
            Logger.EnableFileOutput(_directory);
            var config = new WorkerConfig
            {
                WorkerId = "mac-1",
                WorkerName = "Mac",
                BackendUrl = "https://caughtondash.onrender.com",
                ApiToken = "super-secret-token",
                Analyzer = "python",
            };

            Logger.LogStartupContext(config);

            var text = File.ReadAllText(LogFile);
            Assert.Contains("https://caughtondash.onrender.com", text);
            Assert.Contains("mac-1", text);
            Assert.Contains("python", text);
            // The whole point: enough to tell a wrong token from a missing one,
            // without putting a credential in a file people paste into chats.
            Assert.DoesNotContain("super-secret-token", text);
            Assert.Contains("18 chars", text);
        }

        [Fact]
        public void Startup_context_distinguishes_a_missing_token_from_a_present_one()
        {
            Logger.EnableFileOutput(_directory);

            Logger.LogStartupContext(new WorkerConfig { BackendUrl = "", ApiToken = "" });

            var text = File.ReadAllText(LogFile);
            Assert.Contains("token not set", text);
            Assert.Contains("backend not set", text);
        }

        [Fact]
        public void The_default_location_is_the_one_for_this_platform()
        {
            var directory = Logger.DefaultLogDirectory();

            Assert.Contains("CaughtOnDash", directory);
            if (OperatingSystem.IsMacOS())
            {
                Assert.Contains(Path.Combine("Library", "Logs"), directory);
            }
            else if (OperatingSystem.IsWindows())
            {
                Assert.Contains("logs", directory);
            }
        }
    }
}
