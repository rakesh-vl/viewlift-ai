#include "ViewliftCrashSignals.h"

#include <TargetConditionals.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/vm_map.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <sys/ucontext.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#if __has_include(<ptrauth.h>)
#include <ptrauth.h>
#endif

#define VLC_MAX_IMAGES 768
#define VLC_MAX_FRAMES 128
#define VLC_PATH_BYTES 256
#define VLC_ALT_STACK_BYTES (64 * 1024)

typedef struct {
    uintptr_t base;
    uintptr_t end;
    uint8_t uuid[16];
    char path[VLC_PATH_BYTES];
} VLCImage;

static VLCImage g_images[VLC_MAX_IMAGES];
static atomic_int g_image_count;
static atomic_int g_installed;
static atomic_int g_handling;
static atomic_int g_suppressed;
static int g_fd = -1;
static struct sigaction g_previous[32];
static const int g_signals[] = {SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGTRAP, SIGFPE, SIGSYS};
static const int g_signal_count = (int)(sizeof(g_signals) / sizeof(g_signals[0]));

/* The record is written through this buffer so the handler never allocates. */
static char g_buffer[4096];
static size_t g_buffered;

static void flush_buffer(void) {
    if (g_buffered > 0 && g_fd >= 0) {
        ssize_t ignored = write(g_fd, g_buffer, g_buffered);
        (void)ignored;
    }
    g_buffered = 0;
}

static void emit_bytes(const char *text, size_t length) {
    for (size_t index = 0; index < length; index++) {
        if (g_buffered == sizeof(g_buffer)) {
            flush_buffer();
        }
        g_buffer[g_buffered++] = text[index];
    }
}

static void emit(const char *text) {
    emit_bytes(text, strlen(text));
}

static void emit_unsigned(uint64_t value) {
    char digits[24];
    size_t count = 0;
    do {
        digits[count++] = (char)('0' + (value % 10));
        value /= 10;
    } while (value > 0 && count < sizeof(digits));
    while (count > 0) {
        emit_bytes(&digits[--count], 1);
    }
}

static void emit_hex(uint64_t value) {
    static const char alphabet[] = "0123456789abcdef";
    char digits[18];
    size_t count = 0;
    do {
        digits[count++] = alphabet[value & 0xF];
        value >>= 4;
    } while (value > 0 && count < sizeof(digits));
    emit("0x");
    while (count > 0) {
        emit_bytes(&digits[--count], 1);
    }
}

static void emit_uuid(const uint8_t *uuid) {
    static const char alphabet[] = "0123456789abcdef";
    for (int index = 0; index < 16; index++) {
        char pair[2] = {alphabet[uuid[index] >> 4], alphabet[uuid[index] & 0xF]};
        emit_bytes(pair, 2);
    }
}

static uintptr_t strip_pointer(uintptr_t value) {
#if __has_feature(ptrauth_calls)
    return (uintptr_t)ptrauth_strip((void *)value, ptrauth_key_return_address);
#else
    return value;
#endif
}

/* vm_read_overwrite fails cleanly on a bad pointer, where a plain load would fault inside the handler. */
static bool read_word(uintptr_t address, uintptr_t *out) {
    vm_size_t received = 0;
    kern_return_t status = vm_read_overwrite(
        mach_task_self(), (vm_address_t)address, sizeof(uintptr_t), (vm_address_t)out, &received);
    return status == KERN_SUCCESS && received == sizeof(uintptr_t);
}

static int collect_frames(const ucontext_t *context, uintptr_t *frames, int capacity) {
    if (context == NULL || context->uc_mcontext == NULL) {
        return 0;
    }
    uintptr_t pc = 0, fp = 0, lr = 0;
#if defined(__arm64__)
    pc = (uintptr_t)arm_thread_state64_get_pc(context->uc_mcontext->__ss);
    fp = (uintptr_t)arm_thread_state64_get_fp(context->uc_mcontext->__ss);
    lr = (uintptr_t)arm_thread_state64_get_lr(context->uc_mcontext->__ss);
#elif defined(__x86_64__)
    pc = (uintptr_t)context->uc_mcontext->__ss.__rip;
    fp = (uintptr_t)context->uc_mcontext->__ss.__rbp;
#endif
    int count = 0;
    if (pc != 0) {
        frames[count++] = pc;
    }
    if (lr != 0 && count < capacity) {
        frames[count++] = lr;
    }
    bool first = true;
    while (count < capacity && fp != 0 && (fp & 0x7) == 0) {
        uintptr_t next = 0, returned = 0;
        if (!read_word(fp, &next) || !read_word(fp + sizeof(uintptr_t), &returned)) {
            break;
        }
        returned = strip_pointer(returned);
        if (returned == 0) {
            break;
        }
        /* A function that already pushed its frame repeats lr as its first return address. */
        if (!(first && lr != 0 && returned == lr)) {
            frames[count++] = returned;
        }
        first = false;
        if (next <= fp) {
            break;
        }
        fp = next;
    }
    return count;
}

static const char *arch_name(void) {
#if defined(__arm64__)
    return "arm64";
#elif defined(__x86_64__)
    return "x86_64";
#else
    return "unknown";
#endif
}

static bool frames_touch_image(const VLCImage *image, const uintptr_t *frames, int count) {
    for (int index = 0; index < count; index++) {
        if (frames[index] >= image->base && frames[index] < image->end) {
            return true;
        }
    }
    return false;
}

static void write_record(int signal_number, const siginfo_t *info, const ucontext_t *context) {
    uintptr_t frames[VLC_MAX_FRAMES];
    int frame_count = collect_frames(context, frames, VLC_MAX_FRAMES);
    uint64_t thread_id = 0;
    pthread_threadid_np(NULL, &thread_id);

    emit("VLC1\n");
    emit("sig ");
    emit_unsigned((uint64_t)signal_number);
    emit(" ");
    emit_unsigned((uint64_t)(info != NULL ? info->si_code : 0));
    emit(" ");
    emit_hex((uint64_t)(info != NULL ? (uintptr_t)info->si_addr : 0));
    emit("\ntime ");
    emit_unsigned((uint64_t)time(NULL));
    emit("\npid ");
    emit_unsigned((uint64_t)getpid());
    emit("\narch ");
    emit(arch_name());
    emit("\nthread ");
    emit_unsigned(thread_id);
    emit(pthread_main_np() ? " 1\n" : " 0\n");
    for (int index = 0; index < frame_count; index++) {
        emit("frame ");
        emit_hex((uint64_t)frames[index]);
        emit("\n");
    }
    int images = atomic_load(&g_image_count);
    for (int index = 0; index < images; index++) {
        const VLCImage *image = &g_images[index];
        if (!frames_touch_image(image, frames, frame_count)) {
            continue;
        }
        emit("image ");
        emit_hex((uint64_t)image->base);
        emit(" ");
        emit_hex((uint64_t)image->end);
        emit(" ");
        emit_uuid(image->uuid);
        emit(" ");
        emit(image->path[0] != '\0' ? image->path : "?");
        emit("\n");
    }
    emit("end\n");
    flush_buffer();
}

static void restore_previous_handlers(void) {
    for (int index = 0; index < g_signal_count; index++) {
        int number = g_signals[index];
        sigaction(number, &g_previous[number], NULL);
    }
}

static void handle_signal(int signal_number, siginfo_t *info, void *raw_context) {
    if (atomic_exchange(&g_handling, 1) == 0 && atomic_load(&g_suppressed) == 0) {
        write_record(signal_number, info, (const ucontext_t *)raw_context);
    }
    /* Returning re-runs the faulting instruction (or lets abort() finish), now with the original
       handler in place, so the process dies exactly as it would have and the OS still files its report. */
    restore_previous_handlers();
}

static void add_image(const struct mach_header *header, intptr_t slide) {
    if (header == NULL || header->magic != MH_MAGIC_64) {
        return;
    }
    int slot = atomic_load(&g_image_count);
    if (slot >= VLC_MAX_IMAGES) {
        return;
    }
    const struct mach_header_64 *header64 = (const struct mach_header_64 *)header;
    VLCImage *image = &g_images[slot];
    memset(image, 0, sizeof(*image));
    image->base = (uintptr_t)header;
    image->end = image->base;
    const uint8_t *cursor = (const uint8_t *)(header64 + 1);
    for (uint32_t index = 0; index < header64->ncmds; index++) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmd == LC_UUID) {
            memcpy(image->uuid, ((const struct uuid_command *)command)->uuid, sizeof(image->uuid));
        } else if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const struct segment_command_64 *)command;
            if (strncmp(segment->segname, "__PAGEZERO", sizeof(segment->segname)) != 0) {
                uintptr_t end = (uintptr_t)(segment->vmaddr + (uint64_t)slide + segment->vmsize);
                if (end > image->end) {
                    image->end = end;
                }
            }
        }
        cursor += command->cmdsize;
    }
    atomic_store(&g_image_count, slot + 1);
}

void VLCCrashSignalsRefreshImagePaths(void) {
    uint32_t total = _dyld_image_count();
    int known = atomic_load(&g_image_count);
    for (uint32_t index = 0; index < total; index++) {
        const struct mach_header *header = _dyld_get_image_header(index);
        const char *name = _dyld_get_image_name(index);
        if (header == NULL || name == NULL) {
            continue;
        }
        for (int slot = 0; slot < known; slot++) {
            if (g_images[slot].base == (uintptr_t)header && g_images[slot].path[0] == '\0') {
                strlcpy(g_images[slot].path, name, VLC_PATH_BYTES);
            }
        }
    }
}

/// tvOS does not offer sigaltstack. Handlers there run on the faulting stack, so a stack overflow
/// on Apple TV is left to the system crash report.
static void install_alternate_stack(void) {
#if !TARGET_OS_TV
    stack_t stack;
    stack.ss_sp = malloc(VLC_ALT_STACK_BYTES);
    stack.ss_size = VLC_ALT_STACK_BYTES;
    stack.ss_flags = 0;
    if (stack.ss_sp != NULL) {
        sigaltstack(&stack, NULL);
    }
#endif
}

bool VLCCrashSignalsInstall(const char *path) {
    if (path == NULL || atomic_exchange(&g_installed, 1) != 0) {
        return false;
    }
    g_fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (g_fd < 0) {
        return false;
    }
    _dyld_register_func_for_add_image(add_image);
    VLCCrashSignalsRefreshImagePaths();
    install_alternate_stack();
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = handle_signal;
    action.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigemptyset(&action.sa_mask);
    for (int index = 0; index < g_signal_count; index++) {
        int number = g_signals[index];
        sigaction(number, &action, &g_previous[number]);
    }
    return true;
}

void VLCCrashSignalsSuppress(void) {
    atomic_store(&g_suppressed, 1);
}

bool VLCCrashSignalsDebuggerAttached(void) {
    struct kinfo_proc info;
    memset(&info, 0, sizeof(info));
    int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()};
    size_t size = sizeof(info);
    if (sysctl(mib, 4, &info, &size, NULL, 0) != 0) {
        return false;
    }
    return (info.kp_proc.p_flag & P_TRACED) != 0;
}
