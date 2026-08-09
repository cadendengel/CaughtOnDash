using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Application log: an in-memory ring for the UI, and a rolling file on disk.
    /// </summary>
    /// <remarks>
    /// The file exists because of an evening spent on "it looks like the backend
    /// is rejecting the heartbeats". It could not be checked: the log lived only
    /// in a List and a Console that a GUI process discards. The answer turned out
    /// to be that no backend was running at all, and the first line of a log file
    /// would have said "Connection refused" straight away.
    ///
    /// Three rules follow from that, and they are the reason this class is more
    /// than a StreamWriter:
    ///
    /// Writing must never throw into the caller. A worker that dies because it
    /// could not write a log is strictly worse than one with no logs, so every
    /// failure here is swallowed. After a few consecutive failures file output
    /// switches itself off rather than attempting a write per line forever.
    ///
    /// The file must be bounded. A dashcam worker can be left running for days;
    /// an unbounded log is its own incident.
    ///
    /// The path must be discoverable. It is logged as the first line, so finding
    /// the file never depends on remembering platform conventions.
    /// </remarks>
    public class Logger
    {
        public enum LogLevel
        {
            Info,
            Warning,
            Error,
            Debug
        }

        private static readonly List<string> _logs = new();
        private const int MaxLogs = 1000;

        // Guards both the in-memory list and the file. The list was already
        // shared across the UI thread, the worker loop and the heartbeat timer
        // without one, which is a race that had simply not been noticed yet.
        private static readonly object _gate = new();

        private const long DefaultMaxBytes = 2 * 1024 * 1024;
        private const int DefaultMaxFiles = 5;
        private const int FailuresBeforeGivingUp = 3;

        private static string? _logFilePath;
        private static long _maxBytes = DefaultMaxBytes;
        private static int _maxFiles = DefaultMaxFiles;
        private static long _currentBytes;
        private static int _consecutiveFailures;
        private static bool _fileOutputEnabled;

        /// <summary>Full path of the active log file, or null if file output is off.</summary>
        public static string? LogFilePath
        {
            get { lock (_gate) { return _fileOutputEnabled ? _logFilePath : null; } }
        }

        static Logger()
        {
            EnableFileOutput(DefaultLogDirectory(), DefaultMaxBytes, DefaultMaxFiles);
            Log("Logger initialized", LogLevel.Debug);

            // First line in the file names the file, so nobody has to know the
            // platform convention to find it.
            var path = LogFilePath;
            Log(path == null ? "Logging to memory and console only" : $"Log file: {path}");
        }

        /// <summary>
        /// Where logs go when nothing overrides it: the location a user of that
        /// platform would look first, and that a support request can name.
        /// </summary>
        public static string DefaultLogDirectory()
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                return Path.Combine(localAppData, "CaughtOnDash", "logs");
            }

            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
            {
                return Path.Combine(home, "Library", "Logs", "CaughtOnDash");
            }

            return Path.Combine(home, ".local", "state", "CaughtOnDash", "logs");
        }

        /// <summary>
        /// Point file output at a directory. Called once at startup; tests use it
        /// to redirect writes into a temporary directory.
        /// </summary>
        public static void EnableFileOutput(string directory, long maxBytes = DefaultMaxBytes, int maxFiles = DefaultMaxFiles)
        {
            lock (_gate)
            {
                _maxBytes = maxBytes > 0 ? maxBytes : DefaultMaxBytes;
                _maxFiles = maxFiles > 0 ? maxFiles : DefaultMaxFiles;
                _consecutiveFailures = 0;

                try
                {
                    Directory.CreateDirectory(directory);
                    _logFilePath = Path.Combine(directory, "worker.log");
                    _currentBytes = File.Exists(_logFilePath) ? new FileInfo(_logFilePath).Length : 0;
                    _fileOutputEnabled = true;
                }
                catch (Exception ex)
                {
                    // An unwritable directory is a reason to run without a log
                    // file, not a reason to fail to start.
                    _fileOutputEnabled = false;
                    _logFilePath = null;
                    Console.WriteLine($"Log file disabled ({directory}): {ex.Message}");
                }
            }
        }

        /// <summary>Stop writing to disk. Chiefly so tests can release their files.</summary>
        public static void DisableFileOutput()
        {
            lock (_gate)
            {
                _fileOutputEnabled = false;
                _logFilePath = null;
            }
        }

        public static void Log(string message, LogLevel level = LogLevel.Info)
        {
            var timestamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            var levelStr = level.ToString().ToUpper();
            var formattedMessage = $"[{timestamp}] [{levelStr}] {message}";

            lock (_gate)
            {
                _logs.Add(formattedMessage);

                // Keep log size manageable
                if (_logs.Count > MaxLogs)
                {
                    _logs.RemoveRange(0, _logs.Count - MaxLogs);
                }

                WriteToFile(formattedMessage);
            }

            Console.WriteLine(formattedMessage);
        }

        /// <summary>
        /// Record which backend this worker is actually talking to.
        /// </summary>
        /// <remarks>
        /// Most of the confusion during the Mac worker session was about exactly
        /// this: whether it was pointed at localhost or production. It is one line
        /// and it removes the question. The token is reduced to a length, which is
        /// enough to tell "unset" from "set" and from "set to the wrong thing"
        /// without putting a credential in a file people paste into chats.
        /// </remarks>
        public static void LogStartupContext(WorkerConfig config)
        {
            if (config == null)
            {
                return;
            }

            var token = string.IsNullOrWhiteSpace(config.ApiToken)
                ? "not set"
                : $"set ({config.ApiToken.Length} chars)";
            var backend = string.IsNullOrWhiteSpace(config.BackendUrl) ? "not set" : config.BackendUrl;

            Log($"Worker {config.WorkerId} ({config.WorkerName}) -> backend {backend}, token {token}, analyzer {config.Analyzer}");
        }

        /// <summary>Caller already holds the lock.</summary>
        private static void WriteToFile(string line)
        {
            if (!_fileOutputEnabled || _logFilePath == null)
            {
                return;
            }

            try
            {
                var bytes = System.Text.Encoding.UTF8.GetByteCount(line) + Environment.NewLine.Length;

                if (_currentBytes + bytes > _maxBytes)
                {
                    Roll();
                }

                File.AppendAllText(_logFilePath, line + Environment.NewLine);
                _currentBytes += bytes;
                _consecutiveFailures = 0;
            }
            catch (Exception ex)
            {
                // Never propagate: logging is not worth a crash.
                _consecutiveFailures++;
                if (_consecutiveFailures >= FailuresBeforeGivingUp)
                {
                    _fileOutputEnabled = false;
                    Console.WriteLine($"Log file disabled after {_consecutiveFailures} failures: {ex.Message}");
                }
            }
        }

        /// <summary>
        /// worker.log -> worker.1.log -> worker.2.log ... oldest dropped.
        /// Caller already holds the lock.
        /// </summary>
        private static void Roll()
        {
            if (_logFilePath == null)
            {
                return;
            }

            var directory = Path.GetDirectoryName(_logFilePath) ?? ".";

            // Keeping one file means keeping no history: truncate, or the loop
            // below does nothing and the file grows without bound.
            if (_maxFiles <= 1)
            {
                if (File.Exists(_logFilePath))
                {
                    File.Delete(_logFilePath);
                }

                _currentBytes = 0;
                return;
            }

            // Walk down so each move lands on a free name.
            for (var index = _maxFiles - 1; index >= 1; index--)
            {
                var older = Path.Combine(directory, $"worker.{index}.log");
                var newer = index == 1 ? _logFilePath : Path.Combine(directory, $"worker.{index - 1}.log");

                if (!File.Exists(newer))
                {
                    continue;
                }

                if (File.Exists(older))
                {
                    File.Delete(older);
                }

                File.Move(newer, older);
            }

            _currentBytes = 0;
        }

        public static List<string> GetLogs(int count = 100)
        {
            lock (_gate)
            {
                var startIndex = Math.Max(0, _logs.Count - count);
                return _logs.GetRange(startIndex, _logs.Count - startIndex);
            }
        }

        public static void ClearLogs()
        {
            lock (_gate)
            {
                _logs.Clear();
            }
        }
    }
}
