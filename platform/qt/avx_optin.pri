# Opt-in AVX2 code generation for x86 targets.
#
# This repo's current SIMD layer already benefits from wider x86 codegen when
# the compiler target is raised, but the tree does not currently ship
# helperavx.h and does not have runtime AVX dispatch. Keep AVX2 as an explicit
# build target rather than a default feature, and do not define ENABLE_AVX here.

isEmpty(MLVAPP_AVX_OPTIN_PRI_INCLUDED) {
    MLVAPP_AVX_OPTIN_PRI_INCLUDED = 1

    mlvapp_avx_requested = false
    contains(CONFIG, mlvapp_enable_avx) {
        mlvapp_avx_requested = true
    }
    contains(CONFIG, mlvapp_enable_avx2) {
        mlvapp_avx_requested = true
    }

    mlvapp_avx_env = $$lower($$(MLVAPP_ENABLE_AVX))
    equals(mlvapp_avx_env, 1): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, true): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, yes): mlvapp_avx_requested = true
    equals(mlvapp_avx_env, on): mlvapp_avx_requested = true
    mlvapp_avx2_env = $$lower($$(MLVAPP_ENABLE_AVX2))
    equals(mlvapp_avx2_env, 1): mlvapp_avx_requested = true
    equals(mlvapp_avx2_env, true): mlvapp_avx_requested = true
    equals(mlvapp_avx2_env, yes): mlvapp_avx_requested = true
    equals(mlvapp_avx2_env, on): mlvapp_avx_requested = true

    mlvapp_x86_target = false
    contains(QT_ARCH, "^(x86_64|i[3-6]86)$") {
        mlvapp_x86_target = true
    }

    equals(mlvapp_avx_requested, true) {
        equals(mlvapp_x86_target, true) {
            QMAKE_CFLAGS += -mavx2
            QMAKE_CXXFLAGS += -mavx2
            DEFINES += MLVAPP_BUILD_AVX=1 MLVAPP_BUILD_AVX2=1
            !build_pass: message(MLV App: enabling AVX2 code generation for $$TARGET on $$QT_ARCH)
        } else {
            !build_pass: warning(MLV App: ignoring AVX2 request for non-x86 target $$QT_ARCH)
        }
    }
}
