/* igpu_amaze_debayer.h - C ABI boundary for the CUDA AMaZE debayer backend.
 *
 * MLVApp is built with Qt/MinGW, while CUDA is built with MSVC/nvcc. Keep this
 * boundary plain C so the app can runtime-load the backend DLL with
 * LoadLibrary/GetProcAddress or QLibrary::resolve.
 *
 * DATA CONTRACT
 *   Input  : float[width*height] RGGB raw frame after the same WB/CA prep the
 *            CPU AMaZE core receives.
 *   Output : uint16_t[width*height*3] interleaved RGB, before WB undo, or a
 *            GL_TEXTURE_2D with GL_RGBA16 storage for the zero-readback
 *            playback handoff. The GL texture form carries the same RGB16
 *            values in RGB with alpha set to 65535.
 */
#ifndef IGPU_AMAZE_DEBAYER_H
#define IGPU_AMAZE_DEBAYER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IGPU_AMAZE_DEBAYER_ABI_VERSION 1

typedef struct igpu_amaze_debayer_backend igpu_amaze_debayer_backend;

typedef struct {
    double upload_ms;
    double kernel_ms;
    double download_ms;
    double total_ms;
} igpu_amaze_debayer_timing_t;

igpu_amaze_debayer_backend * igpu_amaze_debayer_create(const char * backend_name);
void                         igpu_amaze_debayer_destroy(igpu_amaze_debayer_backend * backend);
int                          igpu_amaze_debayer_abi_version(igpu_amaze_debayer_backend * backend);
const char *                 igpu_amaze_debayer_describe(igpu_amaze_debayer_backend * backend);

int igpu_amaze_debayer_run(igpu_amaze_debayer_backend * backend,
                           const float * in_raw_float,
                           uint16_t * out_rgb16,
                           int width,
                           int height);

int igpu_amaze_debayer_run_gl_texture(igpu_amaze_debayer_backend * backend,
                                      const float * in_raw_float,
                                      unsigned int gl_texture,
                                      int width,
                                      int height);

int igpu_amaze_debayer_last_timing(igpu_amaze_debayer_backend * backend,
                                   igpu_amaze_debayer_timing_t * timing);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* IGPU_AMAZE_DEBAYER_H */
