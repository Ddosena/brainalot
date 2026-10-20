// Windows Core Audio per-session volume. No endpoint/global volume interface.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace Brainalot {
    public sealed class SpotifyAudioResult {
        public bool Available;
        public bool Success;
        public double? Volume;
        public string Error;
        public int Sessions;
    }
    // Injectable policy boundary: tests supply fake sessions, never OS writes.
    public interface ISpotifyAudioSession {
        string ProcessPath { get; }
        bool SharedProcess { get; }
        float Level { get; }
        void SetLevel(float value);
    }
    public static class SpotifyAudio {
        public static bool IsSpotifyPath(string path) {
            if (String.IsNullOrEmpty(path)) return false;
            try {
                path = Path.GetFullPath(path);
                if (!String.Equals(Path.GetFileName(path), "Spotify.exe", StringComparison.OrdinalIgnoreCase)) return false;
                foreach (string root in new string[] {
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86) }) {
                    if (!String.IsNullOrEmpty(root) && String.Equals(path, Path.Combine(root, "Spotify", "Spotify.exe"), StringComparison.OrdinalIgnoreCase)) return true;
                }
                string packages = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "WindowsApps") + Path.DirectorySeparatorChar;
                if (!path.StartsWith(packages, StringComparison.OrdinalIgnoreCase)) return false;
                string relative = path.Substring(packages.Length);
                string[] parts = relative.Split(Path.DirectorySeparatorChar);
                return parts.Length == 2 && parts[0].StartsWith("SpotifyAB.SpotifyMusic_", StringComparison.OrdinalIgnoreCase)
                    && parts[0].EndsWith("__zpdnekdrzrea0", StringComparison.OrdinalIgnoreCase);
            } catch { return false; }
        }

        public static SpotifyAudioResult Apply(IList<ISpotifyAudioSession> sessions, double? requested) {
            var result = new SpotifyAudioResult();
            if (requested.HasValue && (Double.IsNaN(requested.Value) || Double.IsInfinity(requested.Value) || requested < 0 || requested > 1)) {
                result.Error = "Spotify volume must be between 0 and 1"; return result;
            }
            try {
                var targets = sessions.Where(s => IsSpotifyPath(s.ProcessPath)).ToList();
                if (targets.Count == 0) { result.Error = "Spotify audio session is not active"; return result; }
                if (targets.Any(s => s.SharedProcess) || targets.Select(s => s.ProcessPath).Distinct(StringComparer.OrdinalIgnoreCase).Count() != 1) {
                    result.Error = "Spotify audio sessions are ambiguous"; return result;
                }
                // Validate every target again before any write; a PID/device may disappear.
                string expectedPath = targets[0].ProcessPath;
                if (targets.Any(s => !String.Equals(s.ProcessPath, expectedPath, StringComparison.OrdinalIgnoreCase))) {
                    result.Error = "Spotify audio sessions changed"; return result;
                }
                result.Available = true;
                result.Sessions = targets.Count;
                if (requested.HasValue) foreach (var target in targets) target.SetLevel((float)requested.Value);
                float[] levels = targets.Select(s => s.Level).ToArray();
                if (levels.Any(v => Single.IsNaN(v) || v < 0 || v > 1)) throw new InvalidOperationException();
                // Different legitimate Spotify session levels are represented as unknown.
                result.Volume = levels.All(v => Math.Abs(v - levels[0]) < 0.002) ? (double?)levels[0] : null;
                result.Success = !requested.HasValue || levels.All(v => Math.Abs(v - requested.Value) < 0.01);
                if (!result.Success) result.Error = "Spotify volume was not confirmed";
                return result;
            } catch {
                result.Available = false; result.Success = false; result.Volume = null;
                result.Error = requested.HasValue ? "Spotify volume change failed; outcome may be partial" : "Spotify volume is unavailable";
                return result;
            }
        }

        public static SpotifyAudioResult Read() { return Access(null); }
        public static SpotifyAudioResult Set(double value) { return Access(value); }
        private static void Release(object value) { if (value != null && Marshal.IsComObject(value)) Marshal.ReleaseComObject(value); }
        private static void Check(int hr) { Marshal.ThrowExceptionForHR(hr); }
        private static SpotifyAudioResult Access(double? requested) {
            var sessions = new List<ISpotifyAudioSession>();
            object enumerator = null; IMMDeviceCollection devices = null;
            try {
                enumerator = new MMDeviceEnumerator();
                Check(((IMMDeviceEnumerator)enumerator).EnumAudioEndpoints(0, 1, out devices)); // render, active only
                uint count; Check(devices.GetCount(out count));
                if (count > 64) throw new InvalidOperationException();
                for (uint i = 0; i < count; i++) {
                    IMMDevice device = null; object manager = null; IAudioSessionEnumerator collection = null;
                    try {
                        Check(devices.Item(i, out device));
                        Guid iid = typeof(IAudioSessionManager2).GUID;
                        Check(device.Activate(ref iid, 23, IntPtr.Zero, out manager));
                        Check(((IAudioSessionManager2)manager).GetSessionEnumerator(out collection));
                        int length; Check(collection.GetCount(out length));
                        if (length > 512) throw new InvalidOperationException();
                        for (int n = 0; n < length; n++) {
                            object control = null;
                            try {
                                Check(collection.GetSession(n, out control));
                                var session = (IAudioSessionControl2)control;
                                int state; Check(session.GetState(out state));
                                uint pid; int hr = session.GetProcessId(out pid);
                                if (state == 2 || hr < 0 || pid == 0) continue; // expired / system sounds
                                var candidate = new AudioSession(control, pid, hr != 0);
                                if (!IsSpotifyPath(candidate.ProcessPath)) continue;
                                sessions.Add(candidate); control = null; // transfer ownership
                            } finally { Release(control); }
                        }
                    } finally { Release(collection); Release(manager); Release(device); }
                }
                return Apply(sessions, requested);
            } catch {
                return new SpotifyAudioResult { Error = "Spotify volume is unavailable" };
            } finally {
                foreach (var session in sessions) ((IDisposable)session).Dispose();
                Release(devices); Release(enumerator);
            }
        }
        private sealed class AudioSession : ISpotifyAudioSession, IDisposable {
            private object control; private uint pid; private string original;
            public bool SharedProcess { get; private set; }
            public AudioSession(object value, uint processId, bool shared) { control = value; pid = processId; SharedProcess = shared; original = ProcessPath; }
            public string ProcessPath { get { return GetProcessPath(pid); } }
            public float Level { get { float value; Check(((ISimpleAudioVolume)control).GetMasterVolume(out value)); return value; } }
            public void SetLevel(float value) {
                uint currentPid;
                if (((IAudioSessionControl2)control).GetProcessId(out currentPid) != 0 || currentPid != pid || !IsSpotifyPath(ProcessPath) ||
                    !String.Equals(original, ProcessPath, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException();
                Guid context = new Guid("468656b1-5afb-4889-9fb5-0bf071567aab");
                Check(((ISimpleAudioVolume)control).SetMasterVolume(value, ref context));
            }
            public void Dispose() { Release(control); control = null; }
        }
        private static string GetProcessPath(uint pid) {
            IntPtr handle = OpenProcess(0x1000, false, pid);
            if (handle == IntPtr.Zero) return null;
            try { var buffer = new StringBuilder(32768); int size = buffer.Capacity; return QueryFullProcessImageName(handle, 0, buffer, ref size) ? buffer.ToString() : null; }
            finally { CloseHandle(handle); }
        }
        [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool QueryFullProcessImageName(IntPtr handle, uint flags, StringBuilder path, ref int size);
        [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] internal class MMDeviceEnumerator { }
    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IMMDeviceEnumerator {
        [PreserveSig] int EnumAudioEndpoints(int flow, uint mask, out IMMDeviceCollection devices);
    }
    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IMMDeviceCollection {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int Item(uint index, out IMMDevice device);
    }
    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid, uint context, IntPtr parameters, [MarshalAs(UnmanagedType.IUnknown)] out object result);
    }
    [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IAudioSessionManager2 {
        [PreserveSig] int GetAudioSessionControl(IntPtr session, uint flags, out IntPtr control);
        [PreserveSig] int GetSimpleAudioVolume(IntPtr session, uint flags, out IntPtr volume);
        [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessions);
    }
    [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IAudioSessionEnumerator {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetSession(int index, [MarshalAs(UnmanagedType.IUnknown)] out object session);
    }
    [ComImport, Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface IAudioSessionControl2 {
        [PreserveSig] int GetState(out int state);
        [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr context);
        [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string path);
        [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string path, IntPtr context);
        [PreserveSig] int GetGroupingParam(out Guid grouping);
        [PreserveSig] int SetGroupingParam(ref Guid grouping, IntPtr context);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr notification);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr notification);
        [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetProcessId(out uint pid);
    }
    [ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] internal interface ISimpleAudioVolume {
        [PreserveSig] int SetMasterVolume(float level, ref Guid context);
        [PreserveSig] int GetMasterVolume(out float level);
    }
}
