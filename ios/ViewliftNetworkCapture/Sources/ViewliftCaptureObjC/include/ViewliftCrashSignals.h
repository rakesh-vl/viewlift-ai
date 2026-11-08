#ifndef ViewliftCrashSignals_h
#define ViewliftCrashSignals_h

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/// Installs fatal signal handlers (SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGFPE, SIGSYS).
///
/// When one fires, the handler writes a plain text crash record to `path` using only
/// async-signal-safe calls, restores the previous handler, and returns so the process dies
/// the way it would have without the SDK. `path` is truncated on install, so the caller must
/// recover any earlier record first. Returns false when the file cannot be opened.
bool VLCCrashSignalsInstall(const char *path);

/// Fills in image paths for libraries loaded since the last call.
/// Runs outside the handler because resolving a path takes dyld locks.
void VLCCrashSignalsRefreshImagePaths(void);

/// Stops the signal handler from writing a record. Used once an uncaught exception has
/// already been recorded, so its follow-up SIGABRT does not produce a second report.
void VLCCrashSignalsSuppress(void);

/// True when a debugger is attached. A debugger holds a fatal trap instead of letting the
/// process die, so no handler or crash report fires until the debugger lets go.
bool VLCCrashSignalsDebuggerAttached(void);

#ifdef __cplusplus
}
#endif

#endif /* ViewliftCrashSignals_h */
