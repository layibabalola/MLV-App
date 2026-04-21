# Opt-in AVX code generation for x86 targets.
#
# This repo's current SIMD layer already benefits from __AVX__ when the compiler
# is passed -mavx, but the tree does not currently ship helperavx.h and does not
# have runtime AVX dispatch. Keep AVX as an explicit build target rather than a
# default feature, and do not define ENABLE_AVX here.

isEmpty(MLVAPP_AVX_OPTIN_PRI_INCLUDED) {
    MLVAPP_AVX_OPTIN_PRI_INCLUDED = 1

    mlvapp_avx_requested = false
    contains(CONFIG, mlvapp_enable_avx) {
        mlvapp_avx_requested = true
    }

    mlvapp_avx_env = $$lower($$(MLVAPP_ENABLE_AVX))
    equals(mlvapp_avx_env, 1): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, true): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, yes): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, on): mlvapp_avx_requested = true

    mlvapp_x86_target = false
    contains(QT_ARCH, "^(x86_64|i[3-6]86)$") {
        mlvapp_x86_target = true
    }

    equals(mlvapp_avx_requested, true) {
        equals(mlvapp_x86_target, true) {
            QMAKE_CFLAGS += -mavx
            QMAKE_CXXFLAGS += -mavx
            DEFINES += MLVAPP_BUILD_AVX=1
            !build_pass: message(MLV App: enabling AVX code generation for $$TARGET on $$QT_ARCH)
        } else {
            !build_pass: warning(MLV App: ignoring AVX request for non-x86 target $$QT_ARCH)
        }
    }
}
