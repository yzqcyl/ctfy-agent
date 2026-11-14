/* ft_hook.c —— FreeType 真值钩子（P2-14）。
 *
 * 本场 rev 的突破口：checker 用 FreeType 渲染字形并拿「真值」做校验；把 FT_Load_Glyph /
 * FT_Load_Char / FT_Outline_Decompose 钩住，就能在 checker 运行时把真实字形参数、outline
 * 点阵、hint 后的关键量写进 hook.log，直接还原题目要的值。
 *
 * 编译（VM 内，见 build_ft_hook.sh）：
 *   gcc -shared -fPIC -O2 -o ft_hook.so ft_hook.c $(pkg-config --cflags --libs freetype2)
 * 使用：
 *   LD_PRELOAD=./ft_hook.so CTFY_HOOK_LOG=hook.log ./checker <args>
 *
 * 设计约束：钩子里不能 drag in C++/异常；只依赖 FreeType 与 libc；malloc 失败就静默降级
 * （绝不能因为日志让 checker 崩，否则拿不到真值）。日志行格式：
 *   LOAD_GLYPH idx=%u flags=%ld ok=%d
 *   LOAD_CHAR  code=%lu flags=%ld ok=%d
 *   DECOMPOSE  n_points=%u n_contours=%u
 *   GLYPH_METRICS width=%ld height=%ld horiBearingX=%ld horiBearingY=%ld horiAdvance=%ld
 */
#define _GNU_SOURCE

#include <dlfcn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_OUTLINE_H

typedef FT_Error (*FT_Load_Glyph_fn)(FT_Face, FT_UInt, FT_Int32);
typedef FT_Error (*FT_Load_Char_fn)(FT_Face, FT_ULong, FT_Int32);
typedef FT_Error (*FT_Outline_Decompose_fn)(const FT_Outline *, const FT_Outline_Funcs *,
                                            void *);

static FT_Load_Glyph_fn real_load_glyph = NULL;
static FT_Load_Char_fn real_load_char = NULL;
static FT_Outline_Decompose_fn real_decompose = NULL;
static FILE *log_fp = NULL;
static int log_failed = 0;

static void hook_log(const char *fmt, ...) {
    if (log_fp == NULL) {
        const char *path = getenv("CTFY_HOOK_LOG");
        if (path == NULL || path[0] == '\0') {
            path = "hook.log";
        }
        log_fp = fopen(path, "a");
        if (log_fp == NULL) {
            log_failed = 1;
            return;
        }
    }
    if (log_failed) {
        return;
    }
    va_list args;
    va_start(args, fmt);
    vfprintf(log_fp, fmt, args);
    va_end(args);
    fputc('\n', log_fp);
    fflush(log_fp);          /* checker 可能异常退出，逐行 flush 保住已拿到的真值 */
}

__attribute__((constructor)) static void ft_hook_init(void) {
    real_load_glyph = (FT_Load_Glyph_fn) dlsym(RTLD_NEXT, "FT_Load_Glyph");
    real_load_char = (FT_Load_Char_fn) dlsym(RTLD_NEXT, "FT_Load_Char");
    real_decompose = (FT_Outline_Decompose_fn) dlsym(RTLD_NEXT, "FT_Outline_Decompose");
    hook_log("# ft_hook attached pid=%ld", (long) getpid());
}

__attribute__((destructor)) static void ft_hook_fini(void) {
    if (log_fp != NULL) {
        fclose(log_fp);
        log_fp = NULL;
    }
}

FT_Error FT_Load_Glyph(FT_Face face, FT_UInt glyph_index, FT_Int32 load_flags) {
    if (real_load_glyph == NULL) {
        real_load_glyph = (FT_Load_Glyph_fn) dlsym(RTLD_NEXT, "FT_Load_Glyph");
    }
    FT_Error err = real_load_glyph != NULL
        ? real_load_glyph(face, glyph_index, load_flags)
        : (FT_Error) -1;
    hook_log("LOAD_GLYPH idx=%u flags=%ld ok=%d", glyph_index, (long) load_flags, (int) err);
    if (err == 0 && face != NULL && face->glyph != NULL) {
        FT_GlyphSlot slot = face->glyph;
        hook_log("GLYPH_METRICS width=%ld height=%ld horiBearingX=%ld "
                 "horiBearingY=%ld horiAdvance=%ld",
                 (long) slot->metrics.width, (long) slot->metrics.height,
                 (long) slot->metrics.horiBearingX, (long) slot->metrics.horiBearingY,
                 (long) slot->metrics.horiAdvance);
    }
    return err;
}

FT_Error FT_Load_Char(FT_Face face, FT_ULong char_code, FT_Int32 load_flags) {
    if (real_load_char == NULL) {
        real_load_char = (FT_Load_Char_fn) dlsym(RTLD_NEXT, "FT_Load_Char");
    }
    FT_Error err = real_load_char != NULL
        ? real_load_char(face, char_code, load_flags)
        : (FT_Error) -1;
    hook_log("LOAD_CHAR code=%lu flags=%ld ok=%d", (unsigned long) char_code,
             (long) load_flags, (int) err);
    return err;
}

FT_Error FT_Outline_Decompose(const FT_Outline *outline, const FT_Outline_Funcs *funcs,
                              void *user) {
    if (real_decompose == NULL) {
        real_decompose = (FT_Outline_Decompose_fn) dlsym(RTLD_NEXT, "FT_Outline_Decompose");
    }
    if (outline != NULL) {
        hook_log("DECOMPOSE n_points=%u n_contours=%u",
                 (unsigned) outline->n_points, (unsigned) outline->n_contours);
    }
    return real_decompose != NULL ? real_decompose(outline, funcs, user) : (FT_Error) -1;
}