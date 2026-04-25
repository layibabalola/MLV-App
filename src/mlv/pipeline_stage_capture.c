/*!
 * \file pipeline_stage_capture.c
 * \brief Read-only diagnostic harness implementation. See header for the
 *        contract and the configuration env vars.
 */

#include "pipeline_stage_capture.h"

#include <ctype.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Sentinel value indicating "capture every frame". Stored in
 * g_capture_frames[0] when active. */
#define CAPTURE_FRAME_ALL  ((uint64_t)0xFFFFFFFFFFFFFFFFULL)
#define CAPTURE_FRAMES_MAX 64

static pthread_once_t g_capture_init_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t g_capture_write_mutex = PTHREAD_MUTEX_INITIALIZER;

static int      g_capture_enabled = 0;
static char     g_capture_dir[1024];
static char     g_capture_label[64];
static uint64_t g_capture_frames[CAPTURE_FRAMES_MAX];
static int      g_capture_frame_count = 0;
static int      g_capture_frames_all = 0;

/* Thread-local "current frame" index for hooks that don't otherwise have
 * one in scope (e.g. inside applyLLRawProcObject). Each thread carries
 * its own value; the default is 0. We use the C11 _Thread_local keyword
 * directly because pthread.h isn't required for this and we already use
 * MLV_STAGE_THREAD_LOCAL elsewhere in the codebase (which expands to the
 * same on supported toolchains). */
#if defined(__GNUC__) || defined(__clang__)
#  define MLV_PIPELINE_TLS __thread
#elif defined(_MSC_VER)
#  define MLV_PIPELINE_TLS __declspec(thread)
#else
#  define MLV_PIPELINE_TLS
#endif

static MLV_PIPELINE_TLS uint64_t g_current_frame_tls = 0;

/* ----------------------------------------------------------------------
 * Configuration parsing
 * --------------------------------------------------------------------*/

static void copy_env_or_default(const char * env_name,
                                const char * fallback,
                                char * out,
                                size_t out_size)
{
    const char * v = getenv(env_name);
    if (!v || !*v) v = fallback;
    if (!out || out_size == 0) return;
    size_t n = strlen(v);
    if (n >= out_size) n = out_size - 1;
    memcpy(out, v, n);
    out[n] = '\0';
}

/* Replace any character that's not [A-Za-z0-9_-] with '_' so the value is
 * filename-safe. Operates in place. */
static void sanitise_label(char * s)
{
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (!(isalnum(c) || c == '_' || c == '-')) {
            *s = '_';
        }
    }
}

/* Parse comma-separated frame indices. The literal "all" sets the
 * capture-all flag. Whitespace around commas is tolerated. Silently
 * truncates if the list exceeds CAPTURE_FRAMES_MAX. */
static void parse_frame_list(const char * spec)
{
    g_capture_frame_count = 0;
    g_capture_frames_all = 0;

    if (!spec || !*spec) {
        /* default: only frame 0 */
        g_capture_frames[0] = 0;
        g_capture_frame_count = 1;
        return;
    }

    /* skip leading whitespace */
    while (*spec && isspace((unsigned char)*spec)) ++spec;

    if (strncmp(spec, "all", 3) == 0) {
        const char * tail = spec + 3;
        while (*tail && isspace((unsigned char)*tail)) ++tail;
        if (*tail == '\0') {
            g_capture_frames_all = 1;
            return;
        }
        /* fall through: "all" was the start of a longer token, treat as a
         * regular numeric list */
    }

    const char * p = spec;
    while (*p && g_capture_frame_count < CAPTURE_FRAMES_MAX) {
        while (*p && (isspace((unsigned char)*p) || *p == ',')) ++p;
        if (!*p) break;

        char * end = NULL;
        unsigned long long v = strtoull(p, &end, 10);
        if (end == p) {
            /* not a number; skip to next comma */
            while (*p && *p != ',') ++p;
            continue;
        }
        g_capture_frames[g_capture_frame_count++] = (uint64_t)v;
        p = end;
    }

    if (g_capture_frame_count == 0) {
        /* nothing parsed; fall back to default */
        g_capture_frames[0] = 0;
        g_capture_frame_count = 1;
    }
}

/* Best-effort directory-existence probe. We don't try to mkdir; the user
 * is responsible for creating the directory before running. */
static int dir_exists(const char * path)
{
    if (!path || !*path) return 0;
    /* try to open a short list of candidate "always exists" sub-paths so
     * we don't need stat() (which has portability quirks on MinGW). */
    char probe[1280];
    int wrote = snprintf(probe, sizeof probe, "%s/.mlv_pipeline_capture_probe.tmp", path);
    if (wrote <= 0 || (size_t)wrote >= sizeof probe) return 0;
    FILE * f = fopen(probe, "wb");
    if (!f) return 0;
    fclose(f);
    remove(probe);
    return 1;
}

static void capture_init_once_locked(void)
{
    const char * dir = getenv("MLVAPP_PIPELINE_CAPTURE_DIR");
    if (!dir || !*dir) {
        g_capture_enabled = 0;
        return;
    }

    /* trim trailing slash (forward or back) so we can append our own */
    size_t dn = strlen(dir);
    while (dn > 0 && (dir[dn - 1] == '/' || dir[dn - 1] == '\\')) --dn;
    if (dn >= sizeof g_capture_dir) dn = sizeof g_capture_dir - 1;
    memcpy(g_capture_dir, dir, dn);
    g_capture_dir[dn] = '\0';

    if (!dir_exists(g_capture_dir)) {
        fprintf(stderr,
                "mlv_pipeline_capture: MLVAPP_PIPELINE_CAPTURE_DIR=%s "
                "is not writable; harness disabled.\n",
                g_capture_dir);
        g_capture_enabled = 0;
        return;
    }

    copy_env_or_default("MLVAPP_PIPELINE_CAPTURE_LABEL", "run",
                        g_capture_label, sizeof g_capture_label);
    sanitise_label(g_capture_label);
    if (g_capture_label[0] == '\0') {
        memcpy(g_capture_label, "run", 4);
    }

    parse_frame_list(getenv("MLVAPP_PIPELINE_CAPTURE_FRAMES"));

    g_capture_enabled = 1;
    fprintf(stderr,
            "mlv_pipeline_capture: enabled. dir=%s label=%s frames=%s "
            "(count=%d, all=%d)\n",
            g_capture_dir, g_capture_label,
            getenv("MLVAPP_PIPELINE_CAPTURE_FRAMES")
                ? getenv("MLVAPP_PIPELINE_CAPTURE_FRAMES")
                : "(default: 0)",
            g_capture_frame_count, g_capture_frames_all);
}

static void capture_init_once(void)
{
    /* pthread_once provides the once-only guarantee, but the runtime body
     * still needs to be cheap because we'll branch on g_capture_enabled
     * below. */
    capture_init_once_locked();
}

/* ----------------------------------------------------------------------
 * Public API: enabled probe + frame filter
 * --------------------------------------------------------------------*/

int mlv_pipeline_capture_enabled(void)
{
    pthread_once(&g_capture_init_once, capture_init_once);
    return g_capture_enabled;
}

int mlv_pipeline_capture_should_capture_frame(uint64_t frame_index)
{
    pthread_once(&g_capture_init_once, capture_init_once);
    if (!g_capture_enabled) return 0;
    if (g_capture_frames_all) return 1;
    for (int i = 0; i < g_capture_frame_count; ++i) {
        if (g_capture_frames[i] == frame_index) return 1;
    }
    return 0;
}

void mlv_pipeline_capture_set_current_frame(uint64_t frame_index)
{
    g_current_frame_tls = frame_index;
}

uint64_t mlv_pipeline_capture_get_current_frame(void)
{
    return g_current_frame_tls;
}

/* ----------------------------------------------------------------------
 * Output: bin file
 * --------------------------------------------------------------------*/

static int build_output_path(char * out, size_t out_size,
                             const char * stage,
                             uint64_t frame,
                             const char * suffix)
{
    int wrote = snprintf(out, out_size,
                         "%s/%s_%s_f%llu.%s",
                         g_capture_dir,
                         g_capture_label,
                         stage ? stage : "unknown",
                         (unsigned long long)frame,
                         suffix);
    return (wrote > 0 && (size_t)wrote < out_size) ? 0 : -1;
}

static void write_bin(const char * stage,
                      uint64_t frame,
                      const void * buffer,
                      size_t bytes)
{
    char path[1280];
    if (build_output_path(path, sizeof path, stage, frame, "bin") != 0) {
        fprintf(stderr,
                "mlv_pipeline_capture: path too long for stage=%s frame=%llu\n",
                stage ? stage : "(null)", (unsigned long long)frame);
        return;
    }
    FILE * f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr,
                "mlv_pipeline_capture: fopen(%s) failed: %s\n",
                path, strerror(errno));
        return;
    }
    if (bytes > 0 && buffer) {
        size_t got = fwrite(buffer, 1, bytes, f);
        if (got != bytes) {
            fprintf(stderr,
                    "mlv_pipeline_capture: short write %zu of %zu to %s\n",
                    got, bytes, path);
        }
    }
    fclose(f);
}

/* ----------------------------------------------------------------------
 * Output: sidecar JSON
 * --------------------------------------------------------------------*/

static const char * format_to_string(mlv_pipeline_format_t f)
{
    switch (f) {
        case MLV_PIPELINE_FORMAT_UINT16_MONO: return "uint16_mono";
        case MLV_PIPELINE_FORMAT_UINT16_RGB:  return "uint16_rgb";
        case MLV_PIPELINE_FORMAT_UINT8_RGB:   return "uint8_rgb";
        case MLV_PIPELINE_FORMAT_UNKNOWN:
        default:                              return "unknown";
    }
}

/* Hand-rolled JSON string-emitter. Escapes the minimal set required for
 * valid JSON: \" \\ \b \f \n \r \t and bytes < 0x20. */
static void fputs_json_string(FILE * f, const char * s)
{
    fputc('"', f);
    if (s) {
        for (const unsigned char * p = (const unsigned char *)s; *p; ++p) {
            unsigned char c = *p;
            switch (c) {
                case '"':  fputs("\\\"", f); break;
                case '\\': fputs("\\\\", f); break;
                case '\b': fputs("\\b",  f); break;
                case '\f': fputs("\\f",  f); break;
                case '\n': fputs("\\n",  f); break;
                case '\r': fputs("\\r",  f); break;
                case '\t': fputs("\\t",  f); break;
                default:
                    if (c < 0x20) {
                        fprintf(f, "\\u%04x", c);
                    } else {
                        fputc((int)c, f);
                    }
                    break;
            }
        }
    }
    fputc('"', f);
}

static void write_sidecar(const char * stage,
                          uint64_t frame,
                          size_t bytes,
                          const mlv_pipeline_capture_meta_t * m)
{
    char path[1280];
    if (build_output_path(path, sizeof path, stage, frame, "json") != 0) {
        return;
    }
    FILE * f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr,
                "mlv_pipeline_capture: fopen(%s) failed: %s\n",
                path, strerror(errno));
        return;
    }

    fputs("{\n", f);
    fputs("  \"stage\": ", f);
    fputs_json_string(f, stage);
    fputs(",\n", f);

    fprintf(f, "  \"frame_index\": %llu,\n", (unsigned long long)frame);

    fputs("  \"label\": ", f);
    fputs_json_string(f, g_capture_label);
    fputs(",\n", f);

    fprintf(f, "  \"width\": %d,\n", m->width);
    fprintf(f, "  \"height\": %d,\n", m->height);
    fprintf(f, "  \"bytes_per_line\": %d,\n", m->bytes_per_line);
    fprintf(f, "  \"bytes_per_pixel\": %d,\n", m->bytes_per_pixel);
    fprintf(f, "  \"channels\": %d,\n", m->channels);
    fprintf(f, "  \"bit_depth\": %d,\n", m->bit_depth);
    fprintf(f, "  \"buffer_bytes\": %zu,\n", bytes);

    fputs("  \"format\": ", f);
    fputs_json_string(f, format_to_string(m->format));
    fputs(",\n", f);

    fputs("  \"format_label\": ", f);
    fputs_json_string(f, m->format_label);
    fputs(",\n", f);

    fputs("  \"dual_iso_mode\": ", f);
    fputs_json_string(f, m->dual_iso_mode);
    fputs(",\n", f);

    fputs("  \"debayer_mode\": ", f);
    fputs_json_string(f, m->debayer_mode);
    fputs(",\n", f);

    fprintf(f, "  \"playback_policy_active\": %s,\n",
            m->playback_policy_active ? "true" : "false");
    fprintf(f, "  \"processing_subset_active\": %s,\n",
            m->processing_subset_active ? "true" : "false");

    fputs("  \"scaler\": ", f);
    fputs_json_string(f, m->scaler);
    fputs(",\n", f);

    fputs("  \"path_label\": ", f);
    fputs_json_string(f, m->path_label);
    fputs(",\n", f);

    fprintf(f, "  \"settings_hash\": \"0x%016llx\"\n",
            (unsigned long long)m->settings_hash);

    fputs("}\n", f);
    fclose(f);
}

/* ----------------------------------------------------------------------
 * Public API: capture
 * --------------------------------------------------------------------*/

void mlv_pipeline_capture(uint64_t frame_index,
                          const void * buffer,
                          const mlv_pipeline_capture_meta_t * meta)
{
    /* Fast path: most calls land here when the harness is off. */
    pthread_once(&g_capture_init_once, capture_init_once);
    if (!g_capture_enabled) return;

    if (!g_capture_frames_all) {
        int hit = 0;
        for (int i = 0; i < g_capture_frame_count; ++i) {
            if (g_capture_frames[i] == frame_index) { hit = 1; break; }
        }
        if (!hit) return;
    }

    if (!meta || !meta->stage) {
        fprintf(stderr,
                "mlv_pipeline_capture: missing meta/stage; skipping frame=%llu\n",
                (unsigned long long)frame_index);
        return;
    }

    size_t bytes = 0;
    if (meta->bytes_per_line > 0 && meta->height > 0) {
        bytes = (size_t)meta->bytes_per_line * (size_t)meta->height;
    }

    /* Serialise disk writes so concurrent threads don't interleave. */
    pthread_mutex_lock(&g_capture_write_mutex);
    write_bin(meta->stage, frame_index, buffer, bytes);
    write_sidecar(meta->stage, frame_index, bytes, meta);
    pthread_mutex_unlock(&g_capture_write_mutex);
}
