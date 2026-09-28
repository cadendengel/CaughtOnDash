using System;
using System.IO;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Rules for which analyzer-reported files the worker will upload.
    /// </summary>
    /// <remarks>
    /// The analyzer names files by path, and the worker uploads whatever it is
    /// told to with the backend's token. Only files inside the job's own output
    /// directory are accepted, so a bug or a hostile clip cannot turn the worker
    /// into a way to send arbitrary files from this machine.
    /// </remarks>
    public static class ArtifactUploads
    {
        /// <summary>Larger than any evidence image; the backend enforces the same cap.</summary>
        public const long MaxBytes = 10 * 1024 * 1024;

        public static bool IsInsideDirectory(string directory, string path)
        {
            if (string.IsNullOrWhiteSpace(directory) || string.IsNullOrWhiteSpace(path))
            {
                return false;
            }

            var root = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                       + Path.DirectorySeparatorChar;
            var full = Path.GetFullPath(path);
            var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
            return full.StartsWith(root, comparison);
        }

        public static string ContentTypeFor(string path)
        {
            return Path.GetExtension(path).ToLowerInvariant() switch
            {
                ".png" => "image/png",
                _ => "image/jpeg",
            };
        }
    }
}
