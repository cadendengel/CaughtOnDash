using System;
using System.Collections.Generic;

namespace CaughtOnDash.Worker.Services
{
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

        static Logger()
        {
            Log("Logger initialized", LogLevel.Debug);
        }

        public static void Log(string message, LogLevel level = LogLevel.Info)
        {
            var timestamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            var levelStr = level.ToString().ToUpper();
            var formattedMessage = $"[{timestamp}] [{levelStr}] {message}";

            _logs.Add(formattedMessage);

            // Keep log size manageable
            if (_logs.Count > MaxLogs)
            {
                _logs.RemoveRange(0, _logs.Count - MaxLogs);
            }

            Console.WriteLine(formattedMessage);
        }

        public static List<string> GetLogs(int count = 100)
        {
            var startIndex = Math.Max(0, _logs.Count - count);
            return _logs.GetRange(startIndex, _logs.Count - startIndex);
        }

        public static void ClearLogs()
        {
            _logs.Clear();
        }
    }
}
